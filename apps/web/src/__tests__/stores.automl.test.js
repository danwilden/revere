import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAutoMLStore } from '@/stores/useAutoMLStore.js'

vi.mock('@/api/automl.js', () => ({
  createAutoMLJob: vi.fn(),
  getAutoMLJob: vi.fn(),
  getAutoMLCandidates: vi.fn(),
  convertToSignal: vi.fn(),
}))

import {
  createAutoMLJob,
  getAutoMLJob,
  getAutoMLCandidates,
  convertToSignal,
} from '@/api/automl.js'

describe('useAutoMLStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('submitJob()', () => {
    it('should POST job and set activeJobId', async () => {
      const store = useAutoMLStore()
      const mockJobRun = {
        id: 'job_123',
        status: 'queued',
        created_at: '2026-03-15T10:00:00Z',
      }
      createAutoMLJob.mockResolvedValue(mockJobRun)

      const payload = {
        instrument_id: 'EUR_USD',
        timeframe: 'H1',
        feature_run_id: 'feature_789',
        target_type: 'direction_probability',
      }

      expect(store.isSubmitting).toBe(false)
      const promise = store.submitJob(payload)
      expect(store.isSubmitting).toBe(true)

      const jobId = await promise

      expect(jobId).toBe('job_123')
      expect(store.activeJobId).toBe('job_123')
      expect(store.isSubmitting).toBe(false)
      expect(store.submitError).toBe(null)
      expect(store.jobs).toContain(mockJobRun)
    })

    it('should seed activeJobStatus with minimal shape', async () => {
      const store = useAutoMLStore()
      createAutoMLJob.mockResolvedValue({
        id: 'job_new',
        status: 'queued',
        created_at: '2026-03-15T10:00:00Z',
      })

      await store.submitJob({ feature_run_id: 'feat_1', target_type: 'direction_probability' })

      expect(store.activeJobStatus).toBeDefined()
      expect(store.activeJobStatus.job_run.id).toBe('job_new')
      expect(store.activeJobStatus.job_run.status).toBe('queued')
      expect(store.activeJobStatus.automl_record).toBe(null)
    })

    it('should set submitError on failure', async () => {
      const store = useAutoMLStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Invalid feature run' }
      createAutoMLJob.mockRejectedValue(mockError)

      await expect(store.submitJob({})).rejects.toThrow()

      expect(store.submitError).toBe('Invalid feature run')
      expect(store.isSubmitting).toBe(false)
      expect(store.activeJobId).toBe(null)
    })

    it('should reset candidates and converted signal on submit', async () => {
      const store = useAutoMLStore()
      store.candidates = [{ name: 'old_model' }]
      store.convertedSignal = { id: 'sig_old' }
      createAutoMLJob.mockResolvedValue({ id: 'job_new', status: 'queued' })

      await store.submitJob({})

      expect(store.candidates).toEqual([])
      expect(store.convertedSignal).toBe(null)
    })
  })

  describe('pollJob()', () => {
    it('should update activeJobStatus', async () => {
      const store = useAutoMLStore()
      const mockStatus = {
        job_run: { id: 'job_123', status: 'running', progress_pct: 50 },
        automl_record: { status: 'running' },
      }
      getAutoMLJob.mockResolvedValue(mockStatus)

      await store.pollJob('job_123')

      expect(store.activeJobStatus).toEqual(mockStatus)
    })

    it('should preserve last known state on error', async () => {
      const store = useAutoMLStore()
      const oldStatus = {
        job_run: { id: 'job_123', status: 'running', progress_pct: 30 },
      }
      store.activeJobStatus = oldStatus

      const mockError = new Error('Network error')
      getAutoMLJob.mockRejectedValue(mockError)

      await store.pollJob('job_123')

      // Should not have changed
      expect(store.activeJobStatus).toEqual(oldStatus)
    })
  })

  describe('fetchCandidates()', () => {
    it('should populate candidates array', async () => {
      const store = useAutoMLStore()
      const mockCandidates = [
        { name: 'model_1', objective_metric_value: 0.85 },
        { name: 'model_2', objective_metric_value: 0.82 },
      ]
      getAutoMLCandidates.mockResolvedValue(mockCandidates)

      expect(store.isLoadingCandidates).toBe(false)
      const promise = store.fetchCandidates('job_123')
      expect(store.isLoadingCandidates).toBe(true)

      await promise

      expect(store.candidates).toEqual(mockCandidates)
      expect(store.isLoadingCandidates).toBe(false)
    })

    it('should set error and empty candidates on failure', async () => {
      const store = useAutoMLStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Job not completed' }
      getAutoMLCandidates.mockRejectedValue(mockError)

      await store.fetchCandidates('job_123')

      expect(store.error).toBe('Job not completed')
      expect(store.candidates).toEqual([])
      expect(store.isLoadingCandidates).toBe(false)
    })
  })

  describe('convertJobToSignal()', () => {
    it('should convert job to signal and set convertedSignal', async () => {
      const store = useAutoMLStore()
      const mockSignal = {
        id: 'sig_xyz',
        name: 'automl_signal_1',
        signal_type: 'AUTOML_DIRECTION_PROB',
      }
      convertToSignal.mockResolvedValue(mockSignal)

      expect(store.isConverting).toBe(false)
      const promise = store.convertJobToSignal('job_123', 'my_signal')
      expect(store.isConverting).toBe(true)

      const result = await promise

      expect(result).toEqual(mockSignal)
      expect(store.convertedSignal).toEqual(mockSignal)
      expect(store.isConverting).toBe(false)
    })

    it('should pass undefined signal name if not provided', async () => {
      const store = useAutoMLStore()
      convertToSignal.mockResolvedValue({ id: 'sig_1' })

      await store.convertJobToSignal('job_123', undefined)

      expect(convertToSignal).toHaveBeenCalledWith('job_123', undefined)
    })

    it('should set error on conversion failure', async () => {
      const store = useAutoMLStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Evaluation not accepted' }
      convertToSignal.mockRejectedValue(mockError)

      await expect(store.convertJobToSignal('job_123')).rejects.toThrow()

      expect(store.error).toBe('Evaluation not accepted')
      expect(store.isConverting).toBe(false)
    })
  })

  describe('resetJob()', () => {
    it('should clear all active-job state', () => {
      const store = useAutoMLStore()
      store.activeJobId = 'job_123'
      store.activeJobStatus = { job_run: { id: 'job_123' } }
      store.candidates = [{ name: 'model_1' }]
      store.convertedSignal = { id: 'sig_1' }
      store.submitError = 'Error'
      store.error = 'Error'

      store.resetJob()

      expect(store.activeJobId).toBe(null)
      expect(store.activeJobStatus).toBe(null)
      expect(store.candidates).toEqual([])
      expect(store.convertedSignal).toBe(null)
      expect(store.submitError).toBe(null)
      expect(store.error).toBe(null)
    })
  })
})
