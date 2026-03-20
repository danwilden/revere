import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createAutoMLJob, getAutoMLJob, getAutoMLCandidates, convertToSignal } from '@/api/automl.js'

vi.mock('@/api/client.js', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import client from '@/api/client.js'

describe('AutoML API Module', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('createAutoMLJob()', () => {
    it('should POST to /api/automl/jobs and return job', async () => {
      const mockJobRun = {
        id: 'job_123',
        status: 'queued',
        created_at: '2026-03-15T10:00:00Z',
      }
      client.post.mockResolvedValue({ data: mockJobRun })

      const payload = {
        instrument_id: 'EUR_USD',
        timeframe: 'H1',
        feature_run_id: 'feature_789',
        target_type: 'direction_probability',
      }

      const result = await createAutoMLJob(payload)

      expect(client.post).toHaveBeenCalledWith('/api/automl/jobs', payload)
      expect(result).toEqual(mockJobRun)
    })
  })

  describe('getAutoMLJob()', () => {
    it('should GET /api/automl/jobs/{jobId}', async () => {
      const mockResponse = {
        job_run: { id: 'job_123', status: 'running', progress_pct: 45 },
        automl_record: { status: 'running', target_type: 'direction_probability' },
      }
      client.get.mockResolvedValue({ data: mockResponse })

      const result = await getAutoMLJob('job_123')

      expect(client.get).toHaveBeenCalledWith('/api/automl/jobs/job_123')
      expect(result).toEqual(mockResponse)
    })
  })

  describe('getAutoMLCandidates()', () => {
    it('should GET /api/automl/jobs/{jobId}/candidates', async () => {
      const mockCandidates = [
        { name: 'model_1', objective_metric_name: 'auc', objective_metric_value: 0.85 },
        { name: 'model_2', objective_metric_name: 'auc', objective_metric_value: 0.83 },
      ]
      client.get.mockResolvedValue({ data: mockCandidates })

      const result = await getAutoMLCandidates('job_123')

      expect(client.get).toHaveBeenCalledWith('/api/automl/jobs/job_123/candidates')
      expect(result).toEqual(mockCandidates)
    })
  })

  describe('convertToSignal()', () => {
    it('should POST to /api/automl/jobs/{jobId}/convert without signal_name', async () => {
      const mockSignal = {
        id: 'sig_xyz',
        name: 'automl_signal_gen_1',
        type: 'AUTOML_DIRECTION_PROB',
      }
      client.post.mockResolvedValue({ data: mockSignal })

      const result = await convertToSignal('job_123', undefined)

      expect(client.post).toHaveBeenCalledWith(
        '/api/automl/jobs/job_123/convert',
        null,
        { params: {} }
      )
      expect(result).toEqual(mockSignal)
    })

    it('should POST with signal_name query param if provided', async () => {
      const mockSignal = { id: 'sig_abc', name: 'my_custom_signal' }
      client.post.mockResolvedValue({ data: mockSignal })

      const result = await convertToSignal('job_123', 'my_custom_signal')

      expect(client.post).toHaveBeenCalledWith(
        '/api/automl/jobs/job_123/convert',
        null,
        { params: { signal_name: 'my_custom_signal' } }
      )
      expect(result).toEqual(mockSignal)
    })
  })
})
