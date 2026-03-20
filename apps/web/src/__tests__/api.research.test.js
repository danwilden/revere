import { describe, it, expect, beforeEach, vi } from 'vitest'
import { triggerResearchRun, listResearchRuns, getResearchRun } from '@/api/research.js'

// Mock the client module
vi.mock('@/api/client.js', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import client from '@/api/client.js'

describe('Research API Module', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('triggerResearchRun()', () => {
    it('should POST to /api/research/run with payload', async () => {
      const mockResponse = {
        experiment_id: 'exp_123',
        session_id: 'sess_456',
        status: 'queued',
        created_at: '2026-03-15T10:00:00Z',
      }
      client.post.mockResolvedValue({ data: mockResponse })

      const payload = {
        instrument: 'EUR_USD',
        timeframe: 'H1',
        test_start: '2024-01-01',
        test_end: '2024-12-31',
        task: 'discover',
      }

      const result = await triggerResearchRun(payload)

      expect(client.post).toHaveBeenCalledWith('/api/research/run', payload)
      expect(result).toEqual(mockResponse)
    })

    it('should return response data, not wrapped response', async () => {
      const mockData = { experiment_id: 'exp_999' }
      client.post.mockResolvedValue({ data: mockData })

      const result = await triggerResearchRun({})
      expect(result).toBe(mockData)
    })
  })

  describe('listResearchRuns()', () => {
    it('should GET /api/research/runs without params', async () => {
      const mockRuns = [
        { id: 'run_1', instrument: 'EUR_USD', status: 'succeeded' },
        { id: 'run_2', instrument: 'GBP_USD', status: 'running' },
      ]
      client.get.mockResolvedValue({ data: mockRuns })

      const result = await listResearchRuns()

      expect(client.get).toHaveBeenCalledWith('/api/research/runs', { params: {} })
      expect(result).toEqual(mockRuns)
    })

    it('should pass query params through', async () => {
      client.get.mockResolvedValue({ data: [] })

      await listResearchRuns({ limit: 20, instrument: 'EUR_USD' })

      expect(client.get).toHaveBeenCalledWith('/api/research/runs', {
        params: { limit: 20, instrument: 'EUR_USD' },
      })
    })
  })

  describe('getResearchRun()', () => {
    it('should GET /api/research/runs/{id}', async () => {
      const mockRun = {
        id: 'run_abc123',
        instrument: 'EUR_USD',
        status: 'succeeded',
        sharpe: 1.5,
      }
      client.get.mockResolvedValue({ data: mockRun })

      const result = await getResearchRun('run_abc123')

      expect(client.get).toHaveBeenCalledWith('/api/research/runs/run_abc123')
      expect(result).toEqual(mockRun)
    })
  })
})
