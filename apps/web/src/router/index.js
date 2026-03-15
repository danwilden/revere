import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/data',
  },
  {
    path: '/data',
    name: 'Data',
    component: () => import('@/views/DataView.vue'),
    meta: { label: 'DATA', icon: 'mdi-database-import' },
  },
  {
    path: '/coverage',
    name: 'Coverage',
    component: () => import('@/views/CoverageView.vue'),
    meta: { label: 'COVERAGE', icon: 'mdi-chart-gantt' },
  },
  {
    path: '/models',
    name: 'Models',
    component: () => import('@/views/ModelsView.vue'),
    meta: { label: 'MODELS', icon: 'mdi-brain' },
  },
  {
    path: '/strategies',
    name: 'Strategies',
    component: () => import('@/views/StrategiesView.vue'),
    meta: { label: 'STRATEGIES', icon: 'mdi-strategy' },
  },
  {
    path: '/backtests',
    name: 'Backtests',
    component: () => import('@/views/BacktestView.vue'),
    meta: { label: 'BACKTESTS', icon: 'mdi-chart-timeline-variant' },
  },
  {
    path: '/results',
    name: 'Results',
    component: () => import('@/views/ResultsView.vue'),
    meta: { label: 'RESULTS', icon: 'mdi-chart-line' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
