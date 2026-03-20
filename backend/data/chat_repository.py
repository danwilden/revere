"""Chat session and message persistence.

Stores:
  - data/metadata/chat_sessions.json — dict of session_id -> session record
  - data/metadata/chat_messages/{session_id}.json — list of message records

Uses a threading.Lock to guard all writes.  Reads are always under the same
lock so concurrent request threads see consistent state.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ChatSession:
    """In-memory representation of a chat session record."""

    __slots__ = (
        "id",
        "title",
        "initial_context",
        "created_at",
        "updated_at",
        "message_count",
    )

    def __init__(
        self,
        id: str,
        title: str,
        initial_context: dict | None,
        created_at: str,
        updated_at: str,
        message_count: int,
    ) -> None:
        self.id = id
        self.title = title
        self.initial_context = initial_context
        self.created_at = created_at
        self.updated_at = updated_at
        self.message_count = message_count

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "initial_context": self.initial_context,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChatSession":
        return cls(
            id=d["id"],
            title=d.get("title", ""),
            initial_context=d.get("initial_context"),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            message_count=d.get("message_count", 0),
        )


class ChatMessage:
    """In-memory representation of a single chat message."""

    __slots__ = (
        "id",
        "session_id",
        "role",
        "content",
        "context_json",
        "total_tokens",
        "actions_json",
        "created_at",
    )

    def __init__(
        self,
        id: str,
        session_id: str,
        role: str,
        content: str,
        context_json: dict | None,
        total_tokens: int | None,
        actions_json: list[dict],
        created_at: str,
    ) -> None:
        self.id = id
        self.session_id = session_id
        self.role = role
        self.content = content
        self.context_json = context_json
        self.total_tokens = total_tokens
        self.actions_json = actions_json
        self.created_at = created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "context_json": self.context_json,
            "total_tokens": self.total_tokens,
            "actions_json": self.actions_json,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChatMessage":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            role=d["role"],
            content=d["content"],
            context_json=d.get("context_json"),
            total_tokens=d.get("total_tokens"),
            actions_json=d.get("actions_json") or [],
            created_at=d["created_at"],
        )


class ChatRepository:
    """File-backed persistence for chat sessions and messages.

    Session index: {base_path}/chat_sessions.json
      Stored as a JSON object keyed on session_id.

    Per-session messages: {base_path}/chat_messages/{session_id}.json
      Stored as a JSON array, append-only, oldest first.

    All writes are serialised through a single threading.Lock.
    """

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)
        self._sessions_path = self._base / "chat_sessions.json"
        self._messages_dir = self._base / "chat_messages"
        self._messages_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_sessions(self) -> dict[str, dict]:
        """Load the sessions index from disk.  Must be called under _lock."""
        if not self._sessions_path.exists():
            return {}
        try:
            return json.loads(self._sessions_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_sessions(self, sessions: dict[str, dict]) -> None:
        """Atomically write sessions index.  Must be called under _lock."""
        content = json.dumps(sessions, indent=2, default=str)
        tmp = self._sessions_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(self._sessions_path)

    def _messages_path(self, session_id: str) -> Path:
        return self._messages_dir / f"{session_id}.json"

    def _load_messages(self, session_id: str) -> list[dict]:
        """Load messages for a session.  Must be called under _lock."""
        path = self._messages_path(session_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save_messages(self, session_id: str, messages: list[dict]) -> None:
        """Atomically write messages for a session.  Must be called under _lock."""
        path = self._messages_path(session_id)
        content = json.dumps(messages, indent=2, default=str)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(
        self,
        initial_context: dict | None = None,
        title: str = "",
    ) -> ChatSession:
        """Create and persist a new chat session. Returns the new ChatSession."""
        now = _now_iso()
        session = ChatSession(
            id=str(uuid.uuid4()),
            title=title,
            initial_context=initial_context,
            created_at=now,
            updated_at=now,
            message_count=0,
        )
        with self._lock:
            sessions = self._load_sessions()
            sessions[session.id] = session.to_dict()
            self._save_sessions(sessions)
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        """Return the session or None if not found."""
        with self._lock:
            sessions = self._load_sessions()
        raw = sessions.get(session_id)
        if raw is None:
            return None
        return ChatSession.from_dict(raw)

    def list_sessions(self, limit: int = 50) -> list[ChatSession]:
        """Return sessions ordered newest first, up to limit."""
        with self._lock:
            sessions = self._load_sessions()
        records = list(sessions.values())
        records.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
        return [ChatSession.from_dict(r) for r in records[:limit]]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        context_json: dict | None = None,
        total_tokens: int | None = None,
        actions_json: list[dict] | None = None,
    ) -> ChatMessage:
        """Append a message to the session and increment message_count.

        Raises KeyError if session_id does not exist.
        """
        now = _now_iso()
        message = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            context_json=context_json,
            total_tokens=total_tokens,
            actions_json=actions_json or [],
            created_at=now,
        )
        with self._lock:
            sessions = self._load_sessions()
            if session_id not in sessions:
                raise KeyError(f"Session '{session_id}' not found")

            # Append message
            messages = self._load_messages(session_id)
            messages.append(message.to_dict())
            self._save_messages(session_id, messages)

            # Update session metadata
            sessions[session_id]["message_count"] = len(messages)
            sessions[session_id]["updated_at"] = now
            self._save_sessions(sessions)

        return message

    def get_messages(self, session_id: str) -> list[ChatMessage]:
        """Return messages for a session, oldest first."""
        with self._lock:
            messages = self._load_messages(session_id)
        return [ChatMessage.from_dict(m) for m in messages]

    def delete_session(self, session_id: str) -> bool:
        """Remove a session and its message/agent-state files. Returns True if deleted, False if not found."""
        with self._lock:
            sessions = self._load_sessions()
            if session_id not in sessions:
                return False
            del sessions[session_id]
            self._save_sessions(sessions)
            self._messages_path(session_id).unlink(missing_ok=True)
            self._agent_state_path(session_id).unlink(missing_ok=True)
        return True

    # ------------------------------------------------------------------
    # Agent state persistence (conversation_stage, pending_action, etc.)
    # ------------------------------------------------------------------

    def _agent_state_path(self, session_id: str) -> Path:
        return self._messages_dir / f"{session_id}_agent_state.json"

    def save_agent_state(self, session_id: str, state: dict) -> None:
        """Persist agent state dict for a session (JSON-serializable only)."""
        path = self._agent_state_path(session_id)
        content = json.dumps(state, indent=2, default=str)
        tmp = path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)

    def load_agent_state(self, session_id: str) -> dict | None:
        """Load persisted agent state for a session, or None if not found."""
        path = self._agent_state_path(session_id)
        if not path.exists():
            return None
        try:
            with self._lock:
                return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
