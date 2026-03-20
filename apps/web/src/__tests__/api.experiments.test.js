import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  listExperiments,
  getExperiment,
  updateExperimentStatus,
  createExperiment,
} from '@/api/research.js'

vi.mock('@/api/client.js', () => ({
  default: {
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
}))

import client from '@/api/client.js'

describe('Experiments API Module', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('listExperiments()', () => {
    it('should GET /api/experiments', async () => {
      const mockResponse = {
        experiments: [
          { id: 'exp_1', name: 'Test 1', status: 'active' },
          { id: 'exp_2', name: 'Test 2', status: 'paused' },
        ],
        count: 2,
      }
      client.get.mockResolvedValue({ data: mockResponse })

      const result = await listExperiments()

      expect(client.get).toHaveBeenCalledWith('/api/experiments')
      expect(result).toEqual(mockResponse)
    })
  })

  describe('getExperiment()', () => {
    it('should GET /api/experiments/{id}', async () => {
      const mockResponse = {
        experiment: { id: 'exp_1', name: 'EUR_USD H1 Test', status: 'active' },
        iterations: [
          { generation: 0, strategy_id: 'strat_1', status: 'succeeded' },
        ],
      }
      client.get.mockResolvedValue({ data: mockResponse })

      const result = await getExperiment('exp_1')

      expect(client.get).toHaveBeenCalledWith('/api/experiments/exp_1')
      expect(result).toEqual(mockResponse)
    })
  })

  describe('updateExperimentStatus()', () => {
    it('should PATCH /api/experiments/{id}/status with status payload', async () => {
      const mockResponse = {
        experiment: { id: 'exp_1', status: 'paused' },
      }
      client.patch.mockResolvedValue({ data: mockResponse })

      const result = await updateExperimentStatus('exp_1', { status: 'paused' })

      expect(client.patch).toHaveBeenCalledWith(
        '/api/experiments/exp_1/status',
        { status: 'paused' }
      )
      expect(result).toEqual(mockResponse)
    })
  })

  describe('createExperiment()', () => {
    it('should POST /api/experiments with payload', async () => {
      const mockResponse = {
        experiment: {
          id: 'exp_new',
          name: 'EUR_USD H1 Discovery',
          status: 'active',
        },
      }
      client.post.mockResolvedValue({ data: mockResponse })

      const payload = {
        name: 'EUR_USD H1 Discovery',
        instrument: 'EUR_USD',
        timeframe: 'H1',
        test_start: '2024-01-01',
        test_end: '2024-12-31',
      }

      const result = await createExperiment(payload)

      expect(client.post).toHaveBeenCalledWith('/api/experiments', payload)
      expect(result).toEqual(mockResponse)
    })
  })
})
