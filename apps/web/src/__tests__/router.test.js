import { describe, it, expect } from 'vitest'
import router from '@/router/index.js'

describe('Router Configuration', () => {
  it('should have Research route', () => {
    const route = router.getRoutes().find((r) => r.name === 'Research')
    expect(route).toBeDefined()
    expect(route.path).toBe('/research')
  })

  it('should have Experiments route', () => {
    const route = router.getRoutes().find((r) => r.name === 'Experiments')
    expect(route).toBeDefined()
    expect(route.path).toBe('/experiments')
  })

  it('should have Chat route', () => {
    const route = router.getRoutes().find((r) => r.name === 'Chat')
    expect(route).toBeDefined()
    expect(route.path).toBe('/chat')
  })

  it('should have AutoML route', () => {
    const route = router.getRoutes().find((r) => r.name === 'AutoML')
    expect(route).toBeDefined()
    expect(route.path).toBe('/automl')
  })

  it('Research route should lazy-import ResearchView', async () => {
    const route = router.getRoutes().find((r) => r.name === 'Research')
    expect(route.component).toBeDefined()
    // component is a lazy-loaded function; verify it's callable
    expect(typeof route.component).toBe('function')
  })

  it('Experiments route should lazy-import ExperimentsView', async () => {
    const route = router.getRoutes().find((r) => r.name === 'Experiments')
    expect(route.component).toBeDefined()
    expect(typeof route.component).toBe('function')
  })

  it('Chat route should lazy-import ChatView', async () => {
    const route = router.getRoutes().find((r) => r.name === 'Chat')
    expect(route.component).toBeDefined()
    expect(typeof route.component).toBe('function')
  })

  it('AutoML route should lazy-import AutoMLView', async () => {
    const route = router.getRoutes().find((r) => r.name === 'AutoML')
    expect(route.component).toBeDefined()
    expect(typeof route.component).toBe('function')
  })

  it('all Phase 6 routes should have proper meta labels', () => {
    const expectedMeta = {
      Research: 'RESEARCH',
      Experiments: 'EXPERIMENTS',
      Chat: 'CHAT',
      AutoML: 'AUTOML',
    }

    Object.entries(expectedMeta).forEach(([name, label]) => {
      const route = router.getRoutes().find((r) => r.name === name)
      expect(route.meta?.label).toBe(label)
    })
  })

  it('all Phase 6 routes should have icon metadata', () => {
    const routesToCheck = ['Research', 'Experiments', 'Chat', 'AutoML']

    routesToCheck.forEach((name) => {
      const route = router.getRoutes().find((r) => r.name === name)
      expect(route.meta?.icon).toBeDefined()
      expect(typeof route.meta.icon).toBe('string')
      expect(route.meta.icon.startsWith('mdi-')).toBe(true)
    })
  })
})
