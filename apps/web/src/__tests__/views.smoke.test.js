import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// Mock the stores
vi.mock('@/stores/useResearchStore.js', () => ({
  useResearchStore: vi.fn(() => ({
    researchRuns: [],
    activeRunId: null,
    isSubmitting: false,
    submitError: null,
    loading: false,
    error: null,
    fetchResearchRuns: vi.fn(),
    triggerRun: vi.fn(),
    stopPolling: vi.fn(),
    clearSubmitError: vi.fn(),
  })),
}))

vi.mock('@/stores/useExperimentStore.js', () => ({
  useExperimentStore: vi.fn(() => ({
    experiments: [],
    count: 0,
    selectedExperiment: null,
    selectedExperimentDetail: null,
    loading: false,
    error: null,
    fetchExperiments: vi.fn(),
    selectExperiment: vi.fn(),
    updateStatus: vi.fn(),
    createNewExperiment: vi.fn(),
  })),
}))

vi.mock('@/stores/useAutoMLStore.js', () => ({
  useAutoMLStore: vi.fn(() => ({
    activeJobId: null,
    activeJobStatus: null,
    candidates: [],
    convertedSignal: null,
    isSubmitting: false,
    isLoadingCandidates: false,
    isConverting: false,
    submitError: null,
    error: null,
    submitJob: vi.fn(),
    pollJob: vi.fn(),
    fetchCandidates: vi.fn(),
    convertJobToSignal: vi.fn(),
    resetJob: vi.fn(),
  })),
}))

vi.mock('@/stores/chat.js', () => ({
  useChatStore: vi.fn(() => ({
    sessions: [],
    activeSessionId: null,
    messages: [],
    isStreaming: false,
    streamingContent: '',
    backendOffline: true,
    error: null,
    loadingSessions: false,
    loadingMessages: false,
    fetchSessions: vi.fn(),
    createSession: vi.fn(),
    selectSession: vi.fn(),
    sendMessage: vi.fn(),
    cancelStream: vi.fn(),
  })),
}))

// Mock components
vi.mock('@/components/ui/ErrorBanner.vue', () => ({
  default: { name: 'ErrorBanner', template: '<div></div>' },
}))

vi.mock('@/components/ui/LoadingState.vue', () => ({
  default: { name: 'LoadingState', template: '<div></div>' },
}))

vi.mock('@/components/ui/EmptyState.vue', () => ({
  default: { name: 'EmptyState', template: '<div></div>' },
}))

vi.mock('@/components/ui/NbCard.vue', () => ({
  default: { name: 'NbCard', template: '<div><slot /></div>' },
}))

vi.mock('@/components/ui/StatusBadge.vue', () => ({
  default: { name: 'StatusBadge', template: '<div></div>' },
}))

vi.mock('@/components/experiments/RobustnessPanel.vue', () => ({
  default: { name: 'RobustnessPanel', template: '<div></div>' },
}))

vi.mock('@/composables/useJobPoller.js', () => ({
  useJobPoller: vi.fn(() => ({
    stop: vi.fn(),
  })),
}))

// Import views after mocking
import ResearchView from '@/views/ResearchView.vue'
import ExperimentsView from '@/views/ExperimentsView.vue'
import ChatView from '@/views/ChatView.vue'
import AutoMLView from '@/views/AutoMLView.vue'

describe('Phase 6 View Smoke Tests', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('ResearchView', () => {
    it('should mount without errors', () => {
      const wrapper = mount(ResearchView)
      expect(wrapper.exists()).toBe(true)
    })

    it('should display header with title', () => {
      const wrapper = mount(ResearchView)
      expect(wrapper.text()).toContain('RESEARCH LAB')
    })

    it('should render with empty runs list', () => {
      const wrapper = mount(ResearchView)
      expect(wrapper.html()).toBeTruthy()
    })

    it('should not crash with empty state', () => {
      const wrapper = mount(ResearchView)
      expect(() => {
        wrapper.vm.$nextTick()
      }).not.toThrow()
    })
  })

  describe('ExperimentsView', () => {
    it('should mount without errors', () => {
      const wrapper = mount(ExperimentsView)
      expect(wrapper.exists()).toBe(true)
    })

    it('should display header with title', () => {
      const wrapper = mount(ExperimentsView)
      expect(wrapper.text()).toContain('EXPERIMENTS')
    })

    it('should render split layout', () => {
      const wrapper = mount(ExperimentsView)
      expect(wrapper.html()).toContain('experiments-layout')
    })

    it('should not crash with empty experiments', () => {
      const wrapper = mount(ExperimentsView)
      expect(() => {
        wrapper.vm.$nextTick()
      }).not.toThrow()
    })
  })

  describe('ChatView', () => {
    it('should mount without errors', () => {
      const wrapper = mount(ChatView)
      expect(wrapper.exists()).toBe(true)
    })

    it('should render session panel', () => {
      const wrapper = mount(ChatView)
      expect(wrapper.text()).toContain('SESSIONS')
    })

    it('should show offline banner when backend offline', () => {
      const wrapper = mount(ChatView)
      expect(wrapper.text()).toContain('OFFLINE')
    })

    it('should not crash in offline state', () => {
      const wrapper = mount(ChatView)
      expect(() => {
        wrapper.vm.$nextTick()
      }).not.toThrow()
    })
  })

  describe('AutoMLView', () => {
    it('should mount without errors', () => {
      const wrapper = mount(AutoMLView)
      expect(wrapper.exists()).toBe(true)
    })

    it('should display header with title', () => {
      const wrapper = mount(AutoMLView)
      expect(wrapper.text()).toContain('AUTOML SIGNAL MINING')
    })

    it('should render launch form', () => {
      const wrapper = mount(AutoMLView)
      expect(wrapper.text()).toContain('LAUNCH CONFIGURATION')
    })

    it('should not crash with empty state', () => {
      const wrapper = mount(AutoMLView)
      expect(() => {
        wrapper.vm.$nextTick()
      }).not.toThrow()
    })
  })
})
