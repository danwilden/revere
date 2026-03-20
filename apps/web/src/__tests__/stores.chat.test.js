import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from '@/stores/chat.js'

vi.mock('@/api/chat.js', () => ({
  createSession: vi.fn(),
  listSessions: vi.fn(),
  getMessages: vi.fn(),
  sendMessage: vi.fn(),
}))

import {
  createSession,
  listSessions,
  getMessages,
  sendMessage,
} from '@/api/chat.js'

describe('useChatStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.clearAllTimers()
  })

  describe('fetchSessions()', () => {
    it('should populate sessions and set backendOffline=false', async () => {
      const store = useChatStore()
      const mockResponse = {
        sessions: [
          { id: 'sess_1', created_at: '2026-03-15T10:00:00Z', message_count: 5 },
          { id: 'sess_2', created_at: '2026-03-14T14:00:00Z', message_count: 0 },
        ],
      }
      listSessions.mockResolvedValue(mockResponse)

      const promise = store.fetchSessions()
      expect(store.loadingSessions).toBe(true)

      await promise

      expect(store.sessions).toEqual(mockResponse.sessions)
      expect(store.backendOffline).toBe(false)
      expect(store.loadingSessions).toBe(false)
      expect(store.error).toBe(null)
    })

    it('should set backendOffline=true on 404', async () => {
      const store = useChatStore()
      const mockError = new Error('Not Found')
      mockError.normalized = { status: 404, message: 'Endpoint not found' }
      listSessions.mockRejectedValue(mockError)

      await store.fetchSessions()

      expect(store.backendOffline).toBe(true)
      expect(store.sessions).toEqual([])
      expect(store.loadingSessions).toBe(false)
    })

    it('should set backendOffline=true on connection error', async () => {
      const store = useChatStore()
      const mockError = new Error('Cannot connect to server')
      listSessions.mockRejectedValue(mockError)

      await store.fetchSessions()

      expect(store.backendOffline).toBe(true)
      expect(store.loadingSessions).toBe(false)
    })

    it('should set error for non-offline errors', async () => {
      const store = useChatStore()
      const mockError = new Error('Server error')
      mockError.normalized = { status: 500, message: 'Internal server error' }
      listSessions.mockRejectedValue(mockError)

      await store.fetchSessions()

      expect(store.error).toBe('Internal server error')
      expect(store.backendOffline).toBe(false)
    })
  })

  describe('createSession()', () => {
    it('should create session and set it as active', async () => {
      const store = useC hatStore()
      const mockSession = {
        id: 'sess_new',
        created_at: '2026-03-15T10:00:00Z',
        message_count: 0,
      }
      createSession.mockResolvedValue(mockSession)

      await store.createSession()

      expect(store.sessions[0]).toEqual(mockSession)
      expect(store.activeSessionId).toBe('sess_new')
      expect(store.messages).toEqual([])
    })

    it('should not attempt creation if backendOffline', async () => {
      const store = useChatStore()
      store.backendOffline = true

      await store.createSession()

      expect(createSession).not.toHaveBeenCalled()
    })

    it('should set error on failure', async () => {
      const store = useChatStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Quota exceeded' }
      createSession.mockRejectedValue(mockError)

      await store.createSession()

      expect(store.error).toBe('Quota exceeded')
    })
  })

  describe('selectSession()', () => {
    it('should set activeSessionId and load messages', async () => {
      const store = useChatStore()
      const mockMessages = {
        messages: [
          { id: 'msg_1', role: 'user', content: 'Hi' },
          { id: 'msg_2', role: 'assistant', content: 'Hello' },
        ],
      }
      getMessages.mockResolvedValue(mockMessages)

      await store.selectSession('sess_123')

      expect(store.activeSessionId).toBe('sess_123')
      expect(store.messages).toEqual(mockMessages.messages)
      expect(getMessages).toHaveBeenCalledWith('sess_123')
    })

    it('should skip if same session already active', async () => {
      const store = useChatStore()
      store.activeSessionId = 'sess_123'

      await store.selectSession('sess_123')

      expect(getMessages).not.toHaveBeenCalled()
    })
  })

  describe('sendMessage()', () => {
    it('should add user message optimistically and stream assistant response', async () => {
      const store = useChatStore()
      store.activeSessionId = 'sess_123'

      let onTokenCb, onDoneCb
      sendMessage.mockImplementation((sessId, content, ctx, onToken, onDone) => {
        onTokenCb = onToken
        onDoneCb = onDone
        return Promise.resolve(() => {})
      })

      const sendPromise = store.sendMessage('Hello', { experiment_id: 'exp_1' })
      // Store should be streaming immediately
      expect(store.isStreaming).toBe(true)

      // Messages list should contain optimistic user message
      const userMsg = store.messages[0]
      expect(userMsg.role).toBe('user')
      expect(userMsg.content).toBe('Hello')

      // Simulate token arrival
      if (onTokenCb) {
        onTokenCb('Hello')
        onTokenCb(' ')
        onTokenCb('world')
      }

      // Simulate done
      if (onDoneCb) {
        onDoneCb({ message_id: 'msg_456', total_tokens: 10 })
      }

      await sendPromise
      await new Promise((r) => setTimeout(r, 50))

      expect(store.isStreaming).toBe(false)
      expect(store.streamingContent).toBe('')
      // Assistant message should be appended
      expect(store.messages.some((m) => m.role === 'assistant')).toBe(true)
    })

    it('should not send if backend offline', async () => {
      const store = useChatStore()
      store.backendOffline = true

      await store.sendMessage('Test', null)

      expect(sendMessage).not.toHaveBeenCalled()
    })

    it('should not send if no active session', async () => {
      const store = useChatStore()
      store.activeSessionId = null

      await store.sendMessage('Test', null)

      expect(sendMessage).not.toHaveBeenCalled()
    })

    it('should handle onError callback', async () => {
      const store = useChatStore()
      store.activeSessionId = 'sess_123'

      let onErrorCb
      sendMessage.mockImplementation((sessId, content, ctx, onToken, onDone, onError) => {
        onErrorCb = onError
        return Promise.resolve(() => {})
      })

      await store.sendMessage('Test', null)

      if (onErrorCb) {
        onErrorCb('Stream error message')
      }

      await new Promise((r) => setTimeout(r, 50))

      expect(store.error).toBe('Stream error message')
      expect(store.isStreaming).toBe(false)
      expect(store.streamingContent).toBe('')
    })

    it('should set backendOffline on 404 error', async () => {
      const store = useChatStore()
      store.activeSessionId = 'sess_123'

      let onErrorCb
      sendMessage.mockImplementation((sessId, content, ctx, onToken, onDone, onError) => {
        onErrorCb = onError
        return Promise.resolve(() => {})
      })

      await store.sendMessage('Test', null)

      if (onErrorCb) {
        onErrorCb('HTTP 404')
      }

      await new Promise((r) => setTimeout(r, 50))

      expect(store.backendOffline).toBe(true)
    })

    it('should include context in call if provided', async () => {
      const store = useChatStore()
      store.activeSessionId = 'sess_123'

      sendMessage.mockResolvedValue(() => {})

      const context = { experiment_id: 'exp_123', strategy_id: 'strat_456' }
      await store.sendMessage('Test', context)

      expect(sendMessage).toHaveBeenCalledWith(
        'sess_123',
        'Test',
        context,
        expect.any(Function),
        expect.any(Function),
        expect.any(Function)
      )
    })
  })

  describe('cancelStream()', () => {
    it('should call cancel function and reset streaming state', async () => {
      const store = useChatStore()
      const mockCancel = vi.fn()

      let onTokenCb
      sendMessage.mockImplementation((sessId, content, ctx, onToken, onDone) => {
        onTokenCb = onToken
        return Promise.resolve(mockCancel)
      })

      store.activeSessionId = 'sess_123'
      await store.sendMessage('Test', null)

      store.cancelStream()

      expect(mockCancel).toHaveBeenCalled()
      expect(store.isStreaming).toBe(false)
      expect(store.streamingContent).toBe('')
    })

    it('should be safe to call when no stream is active', () => {
      const store = useChatStore()
      // Should not throw
      expect(() => store.cancelStream()).not.toThrow()
    })
  })
})
