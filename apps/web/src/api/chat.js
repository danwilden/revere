import client from './client.js'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// ---------------------------------------------------------------------------
// Non-streaming calls via axios
// ---------------------------------------------------------------------------

export const createSession = (payload = {}) =>
  client.post('/api/chat/sessions', { title: '', ...payload }).then((r) => r.data)

export const listSessions = () =>
  client.get('/api/chat/sessions').then((r) => r.data)

export const getMessages = (sessionId) =>
  client.get(`/api/chat/sessions/${sessionId}/messages`).then((r) => r.data)

export const deleteSession = (sessionId) =>
  client.delete(`/api/chat/sessions/${sessionId}`)

export const getJobStatus = (jobId) =>
  client.get(`/api/jobs/${jobId}`).then((r) => r.data)

// ---------------------------------------------------------------------------
// SSE streaming via fetch
// ---------------------------------------------------------------------------

/**
 * sendMessage — streams a message to a chat session via SSE over POST.
 *
 * @param {string} sessionId
 * @param {string} content
 * @param {object|null} context — optional { experiment_id, strategy_id }
 * @param {function} onToken  — called with each token string as it arrives
 * @param {function} onDone   — called with the final event payload { message_id, total_tokens, actions }
 * @param {function} onError  — called with an error string on stream error or fetch failure
 * @param {function} [onAction] — called with action event payload { action, payload } when backend emits an action
 * @returns {function} cancel — call to abort the stream
 */
export async function sendMessage(sessionId, content, context, onToken, onDone, onError, onAction) {
  const controller = new AbortController()
  let reader = null

  // Return a cancel handle immediately; the caller may invoke it before the
  // stream even opens (e.g. component unmount during in-flight request).
  function cancel() {
    controller.abort()
    if (reader) {
      reader.cancel().catch(() => {})
    }
  }

  ;(async () => {
    try {
      const payload = { content }
      if (context && (context.experiment_id || context.strategy_id)) {
        payload.context = context
      }

      const response = await fetch(
        `${BASE_URL}/api/chat/sessions/${sessionId}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal,
        },
      )

      if (!response.ok) {
        // Surface non-2xx as an error (includes 404 for unimplemented endpoints)
        const errorText = await response.text().catch(() => '')
        let errorMsg = `HTTP ${response.status}`
        try {
          const parsed = JSON.parse(errorText)
          if (parsed?.detail) errorMsg = String(parsed.detail)
        } catch {
          // ignore JSON parse failure
        }
        onError(errorMsg)
        return
      }

      reader = response.body.getReader()
      const decoder = new TextDecoder()
      // Buffer carries any incomplete SSE line from the previous chunk
      let buffer = ''

      while (true) {
        const { value, done } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        // Keep the last (potentially incomplete) line in the buffer
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith('data: ')) continue
          const raw = trimmed.slice(6).trim()
          if (!raw || raw === '[DONE]') continue

          let evt
          try {
            evt = JSON.parse(raw)
          } catch {
            // Malformed SSE line — skip
            continue
          }

          if (evt.token !== undefined) {
            onToken(evt.token)
          } else if (evt.message_id !== undefined) {
            // "done" event shape
            onDone(evt)
          } else if (evt.error !== undefined) {
            onError(evt.error)
            return
          } else if (evt.action !== undefined) {
            onAction && onAction(evt)
          }
        }
      }

      // Handle any remaining buffer content after stream closes
      if (buffer.trim().startsWith('data: ')) {
        const raw = buffer.trim().slice(6).trim()
        if (raw && raw !== '[DONE]') {
          try {
            const evt = JSON.parse(raw)
            if (evt.token !== undefined) onToken(evt.token)
            else if (evt.message_id !== undefined) onDone(evt)
            else if (evt.error !== undefined) onError(evt.error)
            else if (evt.action !== undefined) onAction && onAction(evt)
          } catch {
            // ignore
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        // Stream cancelled — not a real error
        return
      }
      // Network errors, connection refused, etc.
      const msg =
        err?.message === 'Failed to fetch'
          ? 'Cannot connect to backend. Is the server running?'
          : (err?.message ?? 'Stream error')
      onError(msg)
    } finally {
      if (reader) {
        reader.cancel().catch(() => {})
      }
    }
  })()

  return cancel
}
