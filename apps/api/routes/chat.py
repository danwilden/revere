"""Chat API routes — Phase 6.

POST   /api/chat/sessions                           → 201 ChatSessionResponse
GET    /api/chat/sessions                           → 200 ChatSessionListResponse (newest first)
GET    /api/chat/sessions/{session_id}/messages     → 200 ChatMessageListResponse (oldest first)
POST   /api/chat/sessions/{session_id}/messages     → 200 StreamingResponse (text/event-stream)

The send_message handler streams tokens via SSE.  The agent stub at the top
of this file is replaced in Wave 3 by a real LangGraph chat agent.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.deps import get_chat_repo
from backend.schemas.requests import (
    ChatMessageContext,
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatSendMessageRequest,
    ChatSessionCreateRequest,
    ChatSessionListResponse,
    ChatSessionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Agent — delegates to the LangGraph chat agent
# ---------------------------------------------------------------------------

from backend.agents.chat_agent import invoke_chat_agent as _invoke_chat_agent


async def invoke_chat_agent(
    session_id: str,
    message_history: list,
    new_message_content: str,
    context: ChatMessageContext | None,
    chat_repo=None,
) -> tuple[str, list[dict], int]:
    """Delegate to the LangGraph chat agent.

    Loads persisted agent state (conversation_stage, pending_action, etc.)
    before invoking the graph, and saves the updated state after.  This is
    required so the confirmation flow survives across HTTP requests.
    """
    persisted = chat_repo.load_agent_state(session_id) if chat_repo else None
    reply_text, actions, total_tokens, updated_state = await _invoke_chat_agent(
        session_id=session_id,
        message_history=message_history,
        new_message_content=new_message_content,
        context=context,
        persisted_agent_state=persisted,
    )
    if chat_repo and updated_state:
        chat_repo.save_agent_state(session_id, updated_state)
    return reply_text, actions, total_tokens


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _session_to_response(session) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=session.message_count,
    )


def _message_to_response(msg) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        content=msg.content,
        total_tokens=msg.total_tokens,
        actions_json=msg.actions_json or [],
        created_at=msg.created_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_session(
    body: ChatSessionCreateRequest,
    chat_repo=Depends(get_chat_repo),
) -> ChatSessionResponse:
    """Create a new chat session.

    Returns the session object directly (not wrapped) — the frontend store
    does `sessions.value = [session, ...]` and reads `session.id`.
    """
    initial_context = body.initial_context.model_dump() if body.initial_context else None
    session = chat_repo.create_session(
        initial_context=initial_context,
        title=body.title,
    )
    return _session_to_response(session)


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    chat_repo=Depends(get_chat_repo),
) -> ChatSessionListResponse:
    """List chat sessions, newest first."""
    sessions = chat_repo.list_sessions(limit=limit)
    return ChatSessionListResponse(
        sessions=[_session_to_response(s) for s in sessions],
        count=len(sessions),
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    chat_repo=Depends(get_chat_repo),
) -> None:
    """Delete a chat session and its messages. Returns 204 on success, 404 if not found."""
    if not chat_repo.delete_session(session_id):
        raise HTTPException(
            status_code=404,
            detail=f"Chat session '{session_id}' not found",
        )


@router.get("/sessions/{session_id}/messages", response_model=ChatMessageListResponse)
async def get_messages(
    session_id: str,
    chat_repo=Depends(get_chat_repo),
) -> ChatMessageListResponse:
    """Return all messages for a session, oldest first.

    Returns 404 if the session does not exist.
    """
    session = chat_repo.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat session '{session_id}' not found",
        )
    messages = chat_repo.get_messages(session_id)
    return ChatMessageListResponse(
        messages=[_message_to_response(m) for m in messages],
        count=len(messages),
    )


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: ChatSendMessageRequest,
    chat_repo=Depends(get_chat_repo),
) -> StreamingResponse:
    """Send a user message and stream the assistant reply via SSE.

    SSE event shapes (all prefixed with `data: `):
      {"token": "..."}                                   — partial token
      {"action": "run_strategy", "payload": {...}}       — proposed action (future)
      {"message_id": "...", "total_tokens": N, "actions": [...]} — done
      {"error": "..."}                                   — error
      [DONE]                                             — stream terminator

    Response headers: Content-Type: text/event-stream, Cache-Control: no-cache,
    X-Accel-Buffering: no
    """
    session = chat_repo.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chat session '{session_id}' not found",
        )

    # Persist user message immediately — before the generator runs.
    context_dict = body.context.model_dump() if body.context else {}
    chat_repo.add_message(
        session_id,
        role="user",
        content=body.content,
        context_json=context_dict,
    )

    # Snapshot history for the agent (includes the user message we just stored).
    history = chat_repo.get_messages(session_id)

    async def generate():
        total_tokens = 0
        try:
            reply_text, actions, total_tokens = await invoke_chat_agent(
                session_id=session_id,
                message_history=history,
                new_message_content=body.content,
                context=body.context,
                chat_repo=chat_repo,
            )

            # Stream reply text word by word
            words = reply_text.split(" ")
            for i, word in enumerate(words):
                token = word if i == 0 else " " + word
                yield f"data: {json.dumps({'token': token})}\n\n"

            # Emit action events
            for action in actions:
                yield f"data: {json.dumps({'action': action['action_type'], 'payload': action.get('payload', {})})}\n\n"

            # Persist assistant message
            assistant_msg = chat_repo.add_message(
                session_id,
                role="assistant",
                content=reply_text,
                total_tokens=total_tokens,
                actions_json=actions,
            )

            # Done event — message_id required by frontend onDone handler
            done_event = {
                "message_id": assistant_msg.id,
                "total_tokens": total_tokens,
                "actions": actions,
            }
            yield f"data: {json.dumps(done_event)}\n\n"
            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.exception("Chat stream error for session %s", session_id)
            # Persist an error assistant message so history stays consistent
            try:
                error_msg = chat_repo.add_message(
                    session_id,
                    role="assistant",
                    content=f"An error occurred: {e}",
                    total_tokens=total_tokens,
                )
                error_msg_id = error_msg.id
            except Exception:
                error_msg_id = "error"
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            # Always emit a done event — frontend requires message_id to close the stream
            yield f"data: {json.dumps({'message_id': error_msg_id, 'total_tokens': total_tokens, 'actions': []})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
