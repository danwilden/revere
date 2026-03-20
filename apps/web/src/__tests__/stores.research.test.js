import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useResearchStore } from '@/stores/useResearchStore.js'

vi.mock('@/api/research.js', () => ({
  triggerResearchRun: vi.fn(),
  listResearchRuns: vi.fn(),
  getResearchRun: vi.fn(),
}))

import { triggerResearchRun, listResearchRuns, getResearchRun } from '@/api/research.js'

describe('useResearchStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('fetchResearchRuns()', () => {
    it('should set loading state and populate researchRuns', async () => {
      const store = useResearchStore()
      const mockRuns = [
        { id: 'run_1', instrument: 'EUR_USD', status: 'succeeded', sharpe: 1.2 },
        { id: 'run_2', instrument: 'GBP_USD', status: 'running', sharpe: null },
      ]
      listResearchRuns.mockResolvedValue(mockRuns)

      expect(store.researchRuns).toEqual([])
      expect(store.loading).toBe(false)

      const promise = store.fetchResearchRuns()
      expect(store.loading).toBe(true)

      await promise
      expect(store.loading).toBe(false)
      expect(store.researchRuns).toEqual(mockRuns)
      expect(store.error).toBe(null)
    })

    it('should set error on API failure', async () => {
      const store = useResearchStore()
      const mockError = new Error('API Error')
      mockError.normalized = { message: 'Network timeout' }
      listResearchRuns.mockRejectedValue(mockError)

      await store.fetchResearchRuns()

      expect(store.error).toBe('Network timeout')
      expect(store.loading).toBe(false)
      expect(store.researchRuns).toEqual([])
    })

    it('should pass params through to API', async () => {
      const store = useResearchStore()
      listResearchRuns.mockResolvedValue([])

      await store.fetchResearchRuns({ limit: 10, instrument: 'EUR_USD' })

      expect(listResearchRuns).toHaveBeenCalledWith({ limit: 10, instrument: 'EUR_USD' })
    })
  })

  describe('triggerRun()', () => {
    it('should POST run trigger and set activeRunId', async () => {
      const store = useResearchStore()
      const mockResponse = {
        experiment_id: 'exp_new_123',
        session_id: 'sess_abc',
        status: 'queued',
        created_at: '2026-03-15T10:00:00Z',
      }
      triggerResearchRun.mockResolvedValue(mockResponse)
      // Mock getResearchRun to prevent polling errors
      getResearchRun.mockResolvedValue({
        id: 'exp_new_123',
        status: 'succeeded',
      })

      const payload = {
        instrument: 'EUR_USD',
        timeframe: 'H1',
        test_start: '2024-01-01',
        test_end: '2024-12-31',
        task: 'discover',
      }

      const promise = store.triggerRun(payload)
      expect(store.isSubmitting).toBe(true)

      const result = await promise
      expect(store.isSubmitting).toBe(false)
      expect(store.activeRunId).toBe('exp_new_123')
      expect(result).toEqual(mockResponse)
      expect(store.submitError).toBe(null)
    })

    it('should set submitError on failure', async () => {
      const store = useResearchStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Invalid payload' }
      triggerResearchRun.mockRejectedValue(mockError)

      await expect(store.triggerRun({})).rejects.toThrow()

      expect(store.submitError).toBe('Invalid payload')
      expect(store.isSubmitting).toBe(false)
    })
  })

  describe('Polling behavior', () => {
    it('should start polling after trigger', async () => {
      const store = useResearchStore()
      triggerResearchRun.mockResolvedValue({
        experiment_id: 'exp_123',
        status: 'queued',
      })
      getResearchRun.mockResolvedValue({
        id: 'exp_123',
        status: 'running',
      })

      await store.triggerRun({})

      expect(store.activeRunId).toBe('exp_123')

      // Advance 3 seconds and check polling happened
      vi.advanceTimersByTime(3000)

      // Wait for async calls
      await new Promise((resolve) => setTimeout(resolve, 100))

      expect(getResearchRun).toHaveBeenCalledWith('exp_123')
    })

    it('should stop polling on terminal status', async () => {
      const store = useResearchStore()
      triggerResearchRun.mockResolvedValue({
        experiment_id: 'exp_terminal',
        status: 'queued',
      })
      getResearchRun.mockResolvedValueOnce({ id: 'exp_terminal', status: 'running' })
        .mockResolvedValueOnce({ id: 'exp_terminal', status: 'succeeded' })

      await store.triggerRun({})
      expect(store.activeRunId).toBe('exp_terminal')

      // First poll cycle
      vi.advanceTimersByTime(3000)
      await new Promise((resolve) => setTimeout(resolve, 100))

      // Second poll cycle — status becomes succeeded (terminal)
      vi.advanceTimersByTime(3000)
      await new Promise((resolve) => setTimeout(resolve, 100))

      // After terminal status, polling should stop
      const callCountBefore = getResearchRun.mock.calls.length

      vi.advanceTimersByTime(3000)
      await new Promise((resolve) => setTimeout(resolve, 100))

      // Call count should not increase if polling stopped
      expect(getResearchRun.mock.calls.length).toBe(callCountBefore)
    })

    it('stopPolling() should clear timer', async () => {
      const store = useResearchStore()
      triggerResearchRun.mockResolvedValue({ experiment_id: 'exp_123', status: 'queued' })
      getResearchRun.mockResolvedValue({ id: 'exp_123', status: 'running' })

      await store.triggerRun({})
      store.stopPolling()

      const callCountBefore = getResearchRun.mock.calls.length
      vi.advanceTimersByTime(6000) // 2 poll cycles
      await new Promise((resolve) => setTimeout(resolve, 100))

      // Should not have called getResearchRun again
      expect(getResearchRun.mock.calls.length).toBe(callCountBefore)
    })
  })

  describe('clearSubmitError()', () => {
    it('should clear submitError', () => {
      const store = useResearchStore()
      store.submitError = 'Some error'
      store.clearSubmitError()
      expect(store.submitError).toBe(null)
    })
  })

  describe('clearError()', () => {
    it('should clear list-level error', () => {
      const store = useResearchStore()
      store.error = 'List error'
      store.clearError()
      expect(store.error).toBe(null)
    })
  })
})
