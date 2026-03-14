<template>
  <div class="app-shell">
    <!-- Sidebar -->
    <nav class="app-sidebar">
      <!-- Brand -->
      <div class="app-sidebar__brand scanlines">
        <span class="brand-name">MEDALLION</span>
        <span class="brand-sub">REVERE ANALYTICS</span>
      </div>

      <!-- Nav items -->
      <div class="app-sidebar__nav">
        <router-link
          v-for="item in navItems"
          :key="item.name"
          :to="item.to"
          custom
          v-slot="{ isActive, navigate }"
        >
          <a
            :class="['nav-item', isActive && 'nav-item--active']"
            @click="navigate"
          >
            <v-icon :icon="item.icon" size="16" />
            <span>{{ item.label }}</span>
          </a>
        </router-link>
      </div>

      <!-- Status bar -->
      <div class="app-sidebar__status">
        <span class="nb-label">BACKEND</span>
        <StatusBadge :status="backendStatus" />
      </div>
    </nav>

    <!-- Main content area -->
    <main class="app-main">
      <slot />
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useHealthStore } from '@/stores/health.js'
import StatusBadge from '@/components/ui/StatusBadge.vue'

const healthStore = useHealthStore()

const backendStatus = computed(() => {
  const s = healthStore.status
  if (s === 'online') return 'online'
  if (s === 'offline') return 'failed'
  return 'queued'
})

onMounted(() => {
  healthStore.check()
})

const navItems = [
  { name: 'Data', to: '/data', label: 'DATA', icon: 'mdi-database-import-outline' },
  { name: 'Models', to: '/models', label: 'MODELS', icon: 'mdi-brain' },
  { name: 'Strategies', to: '/strategies', label: 'STRATEGIES', icon: 'mdi-chess-knight' },
  { name: 'Backtests', to: '/backtests', label: 'BACKTESTS', icon: 'mdi-chart-timeline-variant' },
  { name: 'Results', to: '/results', label: 'RESULTS', icon: 'mdi-chart-line' },
]
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--clr-bg);
}

/* ---- Sidebar ---- */
.app-sidebar {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--clr-surface);
  border-right: 2px solid var(--clr-border);
  overflow: hidden;
}

.app-sidebar__brand {
  padding: 20px 20px 16px;
  border-bottom: 2px solid var(--clr-border);
  background: var(--clr-panel);
}

.brand-name {
  display: block;
  font-family: var(--font-mono);
  font-size: 20px;
  font-weight: 700;
  color: var(--clr-yellow);
  letter-spacing: 0.12em;
  line-height: 1;
}

.brand-sub {
  display: block;
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--clr-text-dim);
  letter-spacing: 0.18em;
  text-transform: uppercase;
  margin-top: 5px;
}

.app-sidebar__nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 8px 0;
  overflow-y: auto;
}

/* ---- Status bar ---- */
.app-sidebar__status {
  border-top: 1px solid var(--clr-border);
  padding: 12px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--clr-panel);
}

/* ---- Main content ---- */
.app-main {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--clr-bg);
}
</style>
