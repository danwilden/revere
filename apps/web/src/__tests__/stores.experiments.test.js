import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useExperimentStore } from '@/stores/useExperimentStore.js'

vi.mock('@/api/research.js', () => ({
  listExperiments: vi.fn(),
  getExperiment: vi.fn(),
  createExperiment: vi.fn(),
  updateExperimentStatus: vi.fn(),
}))

import {
  listExperiments,
  getExperiment,
  createExperiment,
  updateExperimentStatus,
} from '@/api/research.js'

describe('useExperimentStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('fetchExperiments()', () => {
    it('should populate experiments and count', async () => {
      const store = useExperimentStore()
      const mockResponse = {
        experiments: [
          { id: 'exp_1', name: 'EUR_USD H1', status: 'active', generation_count: 0 },
          { id: 'exp_2', name: 'GBP_USD H4', status: 'paused', generation_count: 2 },
        ],
        count: 2,
      }
      listExperiments.mockResolvedValue(mockResponse)

      expect(store.experiments).toEqual([])
      await store.fetchExperiments()

      expect(store.experiments).toEqual(mockResponse.experiments)
      expect(store.count).toBe(2)
      expect(store.error).toBe(null)
    })

    it('should pass filter params through', async () => {
      const store = useExperimentStore()
      listExperiments.mockResolvedValue({ experiments: [], count: 0 })

      await store.fetchExperiments({ status: 'active', limit: 50 })

      expect(listExperiments).toHaveBeenCalledWith({ status: 'active', limit: 50 })
    })

    it('should set error on failure', async () => {
      const store = useExperimentStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Connection failed' }
      listExperiments.mockRejectedValue(mockError)

      await store.fetchExperiments()

      expect(store.error).toBe('Connection failed')
      expect(store.experiments).toEqual([])
    })
  })

  describe('fetchExperiment()', () => {
    it('should set selectedExperimentDetail', async () => {
      const store = useExperimentStore()
      const mockResponse = {
        experiment: { id: 'exp_1', name: 'Test', status: 'active' },
        iterations: [
          { generation: 0, strategy_id: 'strat_1', status: 'succeeded' },
        ],
      }
      getExperiment.mockResolvedValue(mockResponse)

      await store.fetchExperiment('exp_1')

      expect(store.selectedExperimentDetail).toEqual(mockResponse)
      expect(store.error).toBe(null)
    })
  })

  describe('selectExperiment()', () => {
    it('should fetch detail and set selected experiment', async () => {
      const store = useExperimentStore()
      store.experiments = [
        { id: 'exp_1', name: 'Test', status: 'active' },
      ]
      const mockDetail = {
        experiment: { id: 'exp_1', name: 'Test', status: 'active' },
        iterations: [],
      }
      getExperiment.mockResolvedValue(mockDetail)

      await store.selectExperiment('exp_1')

      expect(store.selectedExperiment).toEqual(mockDetail.experiment)
      expect(store.selectedExperimentDetail).toEqual(mockDetail)
    })

    it('should optimistically set from local list', async () => {
      const store = useExperimentStore()
      const localExp = { id: 'exp_1', name: 'Test', status: 'active' }
      store.experiments = [localExp]
      getExperiment.mockResolvedValue({ experiment: localExp, iterations: [] })

      const selectPromise = store.selectExperiment('exp_1')
      // Should be set immediately before fetch completes
      expect(store.selectedExperiment).toEqual(localExp)

      await selectPromise
    })
  })

  describe('updateStatus()', () => {
    it('should update experiment status and patch local state', async () => {
      const store = useExperimentStore()
      store.experiments = [
        { id: 'exp_1', name: 'Test', status: 'active' },
      ]
      store.selectedExperiment = store.experiments[0]

      const updated = { id: 'exp_1', name: 'Test', status: 'paused' }
      updateExperimentStatus.mockResolvedValue({ experiment: updated })

      await store.updateStatus('exp_1', 'paused')

      expect(store.experiments[0].status).toBe('paused')
      expect(store.selectedExperiment.status).toBe('paused')
    })

    it('should also update selectedExperimentDetail', async () => {
      const store = useExperimentStore()
      const original = { id: 'exp_1', status: 'active' }
      const updated = { id: 'exp_1', status: 'completed' }
      store.selectedExperimentDetail = {
        experiment: original,
        iterations: [],
      }

      updateExperimentStatus.mockResolvedValue({ experiment: updated })

      await store.updateStatus('exp_1', 'completed')

      expect(store.selectedExperimentDetail.experiment).toEqual(updated)
    })

    it('should set error on failure', async () => {
      const store = useExperimentStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Invalid status' }
      updateExperimentStatus.mockRejectedValue(mockError)

      await expect(store.updateStatus('exp_1', 'invalid')).rejects.toThrow()
      expect(store.error).toBe('Invalid status')
    })
  })

  describe('createNewExperiment()', () => {
    it('should insert created experiment at front and increment count', async () => {
      const store = useExperimentStore()
      store.experiments = [{ id: 'exp_old', name: 'Old' }]
      store.count = 1

      const newExp = { id: 'exp_new', name: 'New' }
      createExperiment.mockResolvedValue({ experiment: newExp })

      await store.createNewExperiment({ name: 'New', instrument: 'EUR_USD' })

      expect(store.experiments[0]).toEqual(newExp)
      expect(store.count).toBe(2)
    })

    it('should set error on failure', async () => {
      const store = useExperimentStore()
      const mockError = new Error('Failed')
      mockError.normalized = { message: 'Validation error' }
      createExperiment.mockRejectedValue(mockError)

      await expect(store.createNewExperiment({})).rejects.toThrow()
      expect(store.error).toBe('Validation error')
    })
  })

  describe('clearSelection()', () => {
    it('should clear selected experiment and detail', () => {
      const store = useExperimentStore()
      store.selectedExperiment = { id: 'exp_1' }
      store.selectedExperimentDetail = { experiment: { id: 'exp_1' } }

      store.clearSelection()

      expect(store.selectedExperiment).toBe(null)
      expect(store.selectedExperimentDetail).toBe(null)
    })
  })

  describe('clearError()', () => {
    it('should clear error', () => {
      const store = useExperimentStore()
      store.error = 'Some error'
      store.clearError()
      expect(store.error).toBe(null)
    })
  })
})
