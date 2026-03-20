import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createSession as apiCreateSession,
  listSessions as apiListSessions,
  getMessages as apiGetMessages,
  sendMessage as apiSendMessage,
  deleteSession as apiDeleteSession,
  getJobStatus as apiGetJobStatus,
} from '@/api/chat.js'

/**
 * useChatStore
 *
 * Owns all state for the AI chat workflow:
 *   sessions          — list of chat sessions from GET /api/chat/sessions
 *   activeSessionId   — currently selected session ID
 *   messages          — messages in the active session
 *   isStreaming       — true while an SSE stream is in progress
 *   streamingContent  — accumulated token text for the in-progress assistant turn
 *   backendOffline    — true when backend returns 404 or connection error for chat endpoints
 *   error             — last surfaced error string
 *   loadingSessions   — true while GET /api/chat/sessions is in flight
 *   loadingMessages   — true while GET /api/chat/sessions/{id}/messages is in flight
 */
export const useChatStore = defineStore('chat', () => {
  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------

  const sessions = ref([])
  const activeSessionId = ref(null)
  const messages = ref([])
  const isStreaming = ref(false)
  const streamingContent = ref('')
  const backendOffline = ref(false)
  const error = ref(null)
  const loadingSessions = ref(false)
  const loadingMessages = ref(false)
  // Pending action emitted by backend during a stream (action event shape)
  const pendingAction = ref(null)

  // Active background job being polled (set when a backtest is queued from chat)
  const activeChatJob = ref(null)  // { job_id } | null

  // Internal: holds the cancel function returned by sendMessage so we can
  // abort the stream from cancelStream() or on component unmount.
  let _cancelStream = null

  // Internal: interval handle for job polling
  let _pollInterval = null

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  /**
   * Classify an error as "offline" (404 or network failure).
   * Returns true when the chat backend is simply not implemented yet.
   */
  function _isOfflineError(err) {
    // Axios normalized error
    if (err?.normalized?.status === 404) return true
    if (err?.normalized?.message?.includes('No response from server')) return true
    // Raw fetch / network error
    if (err?.message?.includes('Cannot connect')) return true
    if (err?.message?.includes('Failed to fetch')) return true
    return false
  }

  // -------------------------------------------------------------------------
  // Job polling helpers
  // -------------------------------------------------------------------------

  /**
   * Start polling a background job every 3 seconds.
   * When the job reaches SUCCEEDED or FAILED, reload messages for the active session
   * so the completion notification appears in the thread.
   */
  function startJobPolling(jobId) {
    stopJobPolling()
    activeChatJob.value = { job_id: jobId }
    _pollInterval = setInterval(async () => {
      try {
        const job = await apiGetJobStatus(jobId)
        if (job.status === 'SUCCEEDED' || job.status === 'FAILED') {
          clearInterval(_pollInterval)
          _pollInterval = null
          activeChatJob.value = null
          if (activeSessionId.value) {
            await fetchMessages(activeSessionId.value)
          }
        }
      } catch {
        // Silently ignore transient polling errors
      }
    }, 3000)
  }

  /**
   * Stop any active job polling and clear polling state.
   */
  function stopJobPolling() {
    if (_pollInterval !== null) {
      clearInterval(_pollInterval)
      _pollInterval = null
    }
    activeChatJob.value = null
  }

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  /**
   * Fetch the list of chat sessions.
   * Sets backendOffline=true on 404 or connection error.
   */
  async function fetchSessions() {
    loadingSessions.value = true
    error.value = null
    try {
      const data = await apiListSessions()
      sessions.value = data.sessions ?? []
      backendOffline.value = false
    } catch (err_) {
      if (_isOfflineError(err_)) {
        backendOffline.value = true
        sessions.value = []
      } else {
        error.value = err_?.normalized?.message ?? err_?.message ?? 'Failed to load sessions'
      }
    } finally {
      loadingSessions.value = false
    }
  }

  /**
   * Create a new chat session and set it as active.
   */
  async function createSession() {
    if (backendOffline.value) return
    error.value = null
    try {
      const session = await apiCreateSession()
      sessions.value = [session, ...sessions.value]
      activeSessionId.value = session.id
      messages.value = []
    } catch (err_) {
      if (_isOfflineError(err_)) {
        backendOffline.value = true
      } else {
        error.value = err_?.normalized?.message ?? err_?.message ?? 'Failed to create session'
      }
    }
  }

  /**
   * Select a session by ID and load its messages.
   */
  async function selectSession(id) {
    if (activeSessionId.value === id) return
    stopJobPolling()
    activeSessionId.value = id
    messages.value = []
    pendingAction.value = null
    await fetchMessages(id)
  }

  /**
   * Fetch messages for a given session ID.
   */
  async function fetchMessages(sessionId) {
    if (!sessionId) return
    loadingMessages.value = true
    error.value = null
    try {
      const data = await apiGetMessages(sessionId)
      messages.value = data.messages ?? []
    } catch (err_) {
      if (_isOfflineError(err_)) {
        backendOffline.value = true
      } else {
        error.value = err_?.normalized?.message ?? err_?.message ?? 'Failed to load messages'
      }
    } finally {
      loadingMessages.value = false
    }
  }

  /**
   * Delete a chat session. Removes it from the list and clears active session/messages if deleted.
   */
  async function deleteSession(sessionId) {
    if (backendOffline.value) return
    error.value = null
    try {
      await apiDeleteSession(sessionId)
      sessions.value = sessions.value.filter((s) => s.id !== sessionId)
      if (activeSessionId.value === sessionId) {
        activeSessionId.value = null
        messages.value = []
      }
    } catch (err_) {
      error.value = err_?.normalized?.message ?? err_?.message ?? 'Failed to delete session'
    }
  }

  /**
   * Stream a user message to the active session.
   *
   * Adds the user message to the local messages list immediately (optimistic),
   * then streams the assistant response token by token into streamingContent.
   * On stream completion, replaces streamingContent with a proper message object.
   *
   * @param {string} content
   * @param {object|null} context — optional { experiment_id, strategy_id }
   */
  async function sendMessage(content, context = null) {
    if (backendOffline.value || !activeSessionId.value || !content.trim()) return

    error.value = null
    isStreaming.value = true
    streamingContent.value = ''
    pendingAction.value = null

    // Optimistic user message
    const userMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      content: content.trim(),
      created_at: new Date().toISOString(),
    }
    messages.value = [...messages.value, userMessage]

    const sessionId = activeSessionId.value

    try {
      const cancelFn = await apiSendMessage(
        sessionId,
        content.trim(),
        context,
        // onToken
        (token) => {
          streamingContent.value += token
        },
        // onDone
        (finalEvent) => {
          // Commit the completed assistant message
          messages.value = [
            ...messages.value,
            {
              id: finalEvent.message_id ?? `assist-${Date.now()}`,
              role: 'assistant',
              content: streamingContent.value,
              created_at: new Date().toISOString(),
              total_tokens: finalEvent.total_tokens ?? null,
            },
          ]
          streamingContent.value = ''
          isStreaming.value = false
          _cancelStream = null
        },
        // onError
        (errMsg) => {
          if (errMsg?.includes('Cannot connect') || errMsg?.includes('HTTP 404')) {
            backendOffline.value = true
          } else {
            error.value = errMsg
          }
          // Discard partial streaming content
          streamingContent.value = ''
          isStreaming.value = false
          _cancelStream = null
        },
        // onAction
        (evt) => {
          pendingAction.value = { action_type: evt.action, payload: evt.payload ?? {} }
          if (evt.action === 'job_queued' && evt.payload?.job_id) {
            startJobPolling(evt.payload.job_id)
          }
        },
      )

      _cancelStream = cancelFn
    } catch (err_) {
      streamingContent.value = ''
      isStreaming.value = false
      _cancelStream = null
      if (_isOfflineError(err_)) {
        backendOffline.value = true
      } else {
        error.value = err_?.message ?? 'Failed to send message'
      }
    }
  }

  /**
   * Cancel any active stream. Safe to call when no stream is active.
   */
  function cancelStream() {
    if (_cancelStream) {
      _cancelStream()
      _cancelStream = null
    }
    if (isStreaming.value) {
      streamingContent.value = ''
      isStreaming.value = false
    }
  }

  return {
    // state
    sessions,
    activeSessionId,
    messages,
    isStreaming,
    streamingContent,
    backendOffline,
    error,
    loadingSessions,
    loadingMessages,
    pendingAction,
    activeChatJob,
    // actions
    fetchSessions,
    createSession,
    selectSession,
    fetchMessages,
    deleteSession,
    sendMessage,
    cancelStream,
    startJobPolling,
    stopJobPolling,
  }
})
