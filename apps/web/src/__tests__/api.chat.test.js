import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createSession, listSessions, getMessages, sendMessage } from '@/api/chat.js'

vi.mock('@/api/client.js', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import client from '@/api/client.js'

describe('Chat API Module', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    global.fetch = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('createSession()', () => {
    it('should POST /api/chat/sessions', async () => {
      const mockSession = {
        id: 'sess_123',
        created_at: '2026-03-15T10:00:00Z',
        message_count: 0,
      }
      client.post.mockResolvedValue({ data: mockSession })

      const result = await createSession()

      expect(client.post).toHaveBeenCalledWith('/api/chat/sessions')
      expect(result).toEqual(mockSession)
    })
  })

  describe('listSessions()', () => {
    it('should GET /api/chat/sessions', async () => {
      const mockResponse = {
        sessions: [
          { id: 'sess_1', created_at: '2026-03-15T10:00:00Z', message_count: 5 },
          { id: 'sess_2', created_at: '2026-03-14T14:30:00Z', message_count: 2 },
        ],
      }
      client.get.mockResolvedValue({ data: mockResponse })

      const result = await listSessions()

      expect(client.get).toHaveBeenCalledWith('/api/chat/sessions')
      expect(result).toEqual(mockResponse)
    })
  })

  describe('getMessages()', () => {
    it('should GET /api/chat/sessions/{sessionId}/messages', async () => {
      const mockResponse = {
        messages: [
          { id: 'msg_1', role: 'user', content: 'Hello', created_at: '2026-03-15T10:00:00Z' },
          {
            id: 'msg_2',
            role: 'assistant',
            content: 'Hi there!',
            created_at: '2026-03-15T10:00:05Z',
          },
        ],
      }
      client.get.mockResolvedValue({ data: mockResponse })

      const result = await getMessages('sess_123')

      expect(client.get).toHaveBeenCalledWith('/api/chat/sessions/sess_123/messages')
      expect(result).toEqual(mockResponse)
    })
  })

  describe('sendMessage() — SSE streaming', () => {
    it('should stream tokens via onToken callback', async () => {
      const mockStream = `data: {"token":"hello"}\ndata: {"token":" "}\ndata: {"token":"world"}\ndata: {"message_id":"msg_999"}\n`

      global.fetch.mockResolvedValue({
        ok: true,
        body: {
          getReader: () => ({
            read: vi.fn()
              .mockResolvedValueOnce({ value: new TextEncoder().encode(mockStream.slice(0, 20)), done: false })
              .mockResolvedValueOnce({ value: new TextEncoder().encode(mockStream.slice(20, 40)), done: false })
              .mockResolvedValueOnce({ value: new TextEncoder().encode(mockStream.slice(40)), done: false })
              .mockResolvedValueOnce({ value: undefined, done: true }),
            cancel: vi.fn().mockResolvedValue(undefined),
          }),
        },
      })

      const onToken = vi.fn()
      const onDone = vi.fn()
      const onError = vi.fn()

      const cancel = await sendMessage(
        'sess_123',
        'test message',
        null,
        onToken,
        onDone,
        onError
      )

      // Wait for the async stream processing
      await new Promise((resolve) => setTimeout(resolve, 100))

      expect(typeof cancel).toBe('function')
      expect(onToken.mock.calls.length).toBeGreaterThan(0)
    })

    it('should call onError on non-2xx response', async () => {
      global.fetch.mockResolvedValue({
        ok: false,
        status: 404,
        text: vi.fn().mockResolvedValue('{"detail":"Not found"}'),
      })

      const onToken = vi.fn()
      const onDone = vi.fn()
      const onError = vi.fn()

      const cancel = await sendMessage(
        'sess_123',
        'test',
        null,
        onToken,
        onDone,
        onError
      )

      // Wait for async processing
      await new Promise((resolve) => setTimeout(resolve, 50))

      expect(onError).toHaveBeenCalled()
      expect(typeof cancel).toBe('function')
    })

    it('should return a cancel function', async () => {
      let abortCalled = false
      const mockController = {
        signal: {},
        abort: () => {
          abortCalled = true
        },
      }

      global.fetch.mockImplementation((url, init) => {
        // Verify signal was passed
        expect(init.signal).toBeDefined()
        return new Promise(() => {}) // Never resolves, so we can test cancel
      })

      const cancel = await sendMessage(
        'sess_123',
        'test',
        null,
        () => {},
        () => {},
        () => {}
      )

      expect(typeof cancel).toBe('function')
    })

    it('should include context in request body if provided', async () => {
      global.fetch.mockResolvedValue({
        ok: true,
        body: {
          getReader: () => ({
            read: vi.fn().mockResolvedValueOnce({ value: undefined, done: true }),
            cancel: vi.fn().mockResolvedValue(undefined),
          }),
        },
      })

      const context = { experiment_id: 'exp_123', strategy_id: 'strat_456' }

      await sendMessage(
        'sess_123',
        'test message',
        context,
        () => {},
        () => {},
        () => {}
      )

      const fetchCall = global.fetch.mock.calls[0]
      const body = JSON.parse(fetchCall[1].body)
      expect(body.content).toBe('test message')
      expect(body.context).toEqual(context)
    })
  })
})
