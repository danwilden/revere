<template>
  <div class="view-container">
    <!-- ------------------------------------------------------------------ -->
    <!-- Header                                                               -->
    <!-- ------------------------------------------------------------------ -->
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">RESEARCH LAB</h1>
      </div>
      <div class="view-header__right">
        <button
          class="nb-btn nb-btn--primary"
          @click="triggerPanelOpen = !triggerPanelOpen"
        >
          {{ triggerPanelOpen ? 'CLOSE FORM' : 'TRIGGER RUN' }}
        </button>
        <span class="view-header__badge">PHASE 5B</span>
      </div>
    </header>

    <!-- ------------------------------------------------------------------ -->
    <!-- Trigger Run Panel (collapsible)                                      -->
    <!-- ------------------------------------------------------------------ -->
    <div v-if="triggerPanelOpen" class="nb-card trigger-panel">
      <div class="trigger-panel__header">
        <span class="nb-heading nb-heading--md">TRIGGER RESEARCH RUN</span>
        <span class="nb-label text-muted">CONFIGURE PARAMETERS</span>
      </div>

      <ErrorBanner v-if="store.submitError" :message="store.submitError" />

      <!-- Success banner after trigger -->
      <div v-if="lastTriggeredId" class="nb-banner nb-banner--info trigger-panel__success">
        <span class="text-green font-mono">RUN QUEUED //</span>
        <span class="font-mono" style="margin-left: 8px; font-size: 12px">{{ lastTriggeredId }}</span>
      </div>

      <div class="trigger-panel__form">
        <!-- Row 1: Instrument + Timeframe -->
        <div class="form-row">
          <div class="form-field">
            <label class="nb-label form-field__label">INSTRUMENT</label>
            <input
              v-model="form.instrument"
              class="nb-input"
              type="text"
              placeholder="EUR_USD"
              :disabled="store.isSubmitting"
            />
          </div>
          <div class="form-field">
            <label class="nb-label form-field__label">TIMEFRAME</label>
            <div class="tf-selector">
              <button
                v-for="tf in TIMEFRAMES"
                :key="tf"
                :class="['nb-btn', 'tf-btn', form.timeframe === tf && 'tf-btn--active']"
                :disabled="store.isSubmitting"
                @click="form.timeframe = tf"
              >
                {{ tf }}
              </button>
            </div>
          </div>
        </div>

        <!-- Row 2: Dates -->
        <div class="form-row">
          <div class="form-field">
            <label class="nb-label form-field__label">TEST START</label>
            <input
              v-model="form.test_start"
              class="nb-input"
              type="date"
              :disabled="store.isSubmitting"
            />
          </div>
          <div class="form-field">
            <label class="nb-label form-field__label">TEST END</label>
            <input
              v-model="form.test_end"
              class="nb-input"
              type="date"
              :disabled="store.isSubmitting"
            />
          </div>
        </div>

        <!-- Row 3: Task + optional parent -->
        <div class="form-row">
          <div class="form-field">
            <label class="nb-label form-field__label">TASK</label>
            <div class="tf-selector">
              <button
                v-for="t in TASKS"
                :key="t.value"
                :class="['nb-btn', 'tf-btn', form.task === t.value && 'tf-btn--active']"
                :disabled="store.isSubmitting"
                @click="form.task = t.value"
              >
                {{ t.label }}
              </button>
            </div>
          </div>
          <div v-if="form.task === 'mutate'" class="form-field">
            <label class="nb-label form-field__label">PARENT EXPERIMENT ID</label>
            <input
              v-model="form.parent_experiment_id"
              class="nb-input"
              type="text"
              placeholder="exp_xxxxxxxx"
              :disabled="store.isSubmitting"
            />
          </div>
        </div>

        <!-- Validation error -->
        <div v-if="formError" class="nb-banner nb-banner--error">
          {{ formError }}
        </div>

        <!-- Submit -->
        <div class="trigger-panel__actions">
          <button
            class="nb-btn nb-btn--primary trigger-panel__submit"
            :disabled="store.isSubmitting"
            @click="handleTrigger"
          >
            {{ store.isSubmitting ? 'QUEUING...' : 'LAUNCH RUN' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ------------------------------------------------------------------ -->
    <!-- Supervisor Status                                                    -->
    <!-- ------------------------------------------------------------------ -->
    <div class="nb-card supervisor-status">
      <div class="supervisor-status__row">
        <span class="nb-label ls-wide">SUPERVISOR STATUS</span>
        <div class="supervisor-status__state">
          <span
            v-if="supervisorState === 'running'"
            :class="['status-chip', 'status-chip--running']"
          >
            <span class="status-dot" />
            RUNNING
          </span>
          <span
            v-else-if="supervisorState === 'succeeded'"
            :class="['status-chip', 'status-chip--succeeded']"
          >
            <span class="status-dot" />
            LAST RUN: SUCCEEDED
          </span>
          <span
            v-else-if="supervisorState === 'failed'"
            :class="['status-chip', 'status-chip--failed']"
          >
            <span class="status-dot" />
            LAST RUN: FAILED
          </span>
          <span v-else class="status-chip status-chip--queued">
            <span class="status-dot" />
            IDLE
          </span>
        </div>
      </div>
      <div v-if="latestRun" class="supervisor-status__meta">
        <span class="nb-label text-muted">LATEST</span>
        <span class="nb-value nb-value--sm font-mono">{{ latestRun.id }}</span>
        <span class="nb-label text-muted">{{ latestRun.instrument }} {{ latestRun.timeframe }}</span>
        <span class="nb-label text-muted uppercase">{{ latestRun.task }}</span>
      </div>
    </div>

    <!-- ------------------------------------------------------------------ -->
    <!-- Active Runs Table                                                   -->
    <!-- ------------------------------------------------------------------ -->
    <div class="nb-card">
      <div class="runs-table-header">
        <span class="nb-heading nb-heading--md">RESEARCH RUNS</span>
        <button
          class="nb-btn"
          style="font-size: 11px; padding: 6px 14px"
          :disabled="store.loading"
          @click="store.fetchResearchRuns()"
        >
          REFRESH
        </button>
      </div>

      <LoadingState v-if="store.loading && store.researchRuns.length === 0" message="LOADING RUNS..." />
      <ErrorBanner v-else-if="store.error" :message="store.error" />
      <EmptyState v-else-if="store.researchRuns.length === 0" message="NO RESEARCH RUNS YET" />

      <div v-else class="nb-table-wrapper">
        <table class="nb-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>INSTRUMENT</th>
              <th>TF</th>
              <th>TASK</th>
              <th>GEN</th>
              <th>STATUS</th>
              <th>SHARPE</th>
              <th>WIN RATE</th>
              <th>TRADES</th>
              <th>DETAIL</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="run in store.researchRuns"
              :key="run.id"
              :class="['run-row', activeDetailRunId === run.id && 'run-row--selected']"
              @click="toggleDetailRun(run.id)"
            >
              <td class="font-mono text-muted" style="font-size: 11px">{{ truncateId(run.id) }}</td>
              <td class="font-mono">{{ run.instrument }}</td>
              <td class="font-mono">{{ run.timeframe }}</td>
              <td>
                <span :class="['task-badge', `task-badge--${run.task}`]">
                  {{ run.task?.toUpperCase() }}
                </span>
              </td>
              <td class="font-mono text-muted">{{ run.generation ?? '—' }}</td>
              <td>
                <span :class="['status-chip', statusChipClass(run.status)]">
                  <span class="status-dot" />
                  {{ run.status?.toUpperCase() }}
                </span>
              </td>
              <td>
                <span
                  :class="[
                    'nb-value nb-value--sm',
                    run.sharpe === null || run.sharpe === undefined ? 'text-muted'
                      : run.sharpe >= 1 ? 'nb-value--positive'
                      : run.sharpe < 0 ? 'nb-value--negative'
                      : ''
                  ]"
                >
                  {{ run.sharpe != null ? run.sharpe.toFixed(2) : '—' }}
                </span>
              </td>
              <td>
                <span class="nb-value nb-value--sm">
                  {{ run.win_rate != null ? (run.win_rate * 100).toFixed(1) + '%' : '—' }}
                </span>
              </td>
              <td class="font-mono">{{ run.total_trades ?? '—' }}</td>
              <td>
                <button
                  class="nb-btn"
                  style="font-size: 10px; padding: 4px 10px"
                  @click.stop="toggleDetailRun(run.id)"
                >
                  {{ activeDetailRunId === run.id ? 'HIDE' : 'VIEW' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Inline run detail panel -->
      <div v-if="activeDetailRun" class="run-detail nb-panel">
        <div class="run-detail__header">
          <span class="nb-heading nb-heading--sm">RUN DETAIL</span>
          <button class="nb-btn" style="font-size: 10px; padding: 4px 10px" @click="activeDetailRunId = null">
            CLOSE
          </button>
        </div>
        <div class="run-detail__grid">
          <div class="run-detail__field">
            <span class="nb-label">ID</span>
            <span class="nb-value nb-value--sm font-mono">{{ activeDetailRun.id }}</span>
          </div>
          <div class="run-detail__field">
            <span class="nb-label">SESSION</span>
            <span class="nb-value nb-value--sm font-mono">{{ activeDetailRun.session_id ?? '—' }}</span>
          </div>
          <div class="run-detail__field">
            <span class="nb-label">STRATEGY ID</span>
            <span class="nb-value nb-value--sm font-mono">{{ activeDetailRun.strategy_id ?? '—' }}</span>
          </div>
          <div class="run-detail__field">
            <span class="nb-label">BACKTEST ID</span>
            <span class="nb-value nb-value--sm font-mono">{{ activeDetailRun.backtest_run_id ?? '—' }}</span>
          </div>
          <div class="run-detail__field">
            <span class="nb-label">MAX DD</span>
            <span
              :class="[
                'nb-value nb-value--sm',
                activeDetailRun.max_drawdown_pct != null && activeDetailRun.max_drawdown_pct < 0 ? 'nb-value--negative' : ''
              ]"
            >
              {{ activeDetailRun.max_drawdown_pct != null ? activeDetailRun.max_drawdown_pct.toFixed(2) + '%' : '—' }}
            </span>
          </div>
          <div class="run-detail__field">
            <span class="nb-label">PARENT</span>
            <span class="nb-value nb-value--sm font-mono text-muted">{{ activeDetailRun.parent_id ?? '—' }}</span>
          </div>
        </div>
        <div v-if="activeDetailRun.hypothesis" class="run-detail__hypothesis">
          <span class="nb-label" style="display: block; margin-bottom: 6px">HYPOTHESIS</span>
          <p class="hypothesis-text">{{ activeDetailRun.hypothesis }}</p>
        </div>
        <div v-if="activeDetailRun.failure_taxonomy" class="run-detail__taxonomy">
          <span class="nb-label" style="display: block; margin-bottom: 6px">FAILURE TAXONOMY</span>
          <span class="nb-value nb-value--sm nb-value--negative">{{ activeDetailRun.failure_taxonomy }}</span>
        </div>
        <div v-if="activeDetailRun.error_message" class="nb-banner nb-banner--error" style="margin-top: 12px">
          {{ activeDetailRun.error_message }}
        </div>
      </div>
    </div>

    <!-- ------------------------------------------------------------------ -->
    <!-- Generation Lineage                                                   -->
    <!-- ------------------------------------------------------------------ -->
    <div v-if="lineageGroups.length > 0" class="nb-card">
      <span class="nb-heading nb-heading--md" style="display: block; margin-bottom: 16px">
        GENERATION LINEAGE
      </span>
      <div v-for="group in lineageGroups" :key="group.key" class="lineage-group">
        <div class="lineage-group__header">
          <span class="nb-label text-yellow">{{ group.key }}</span>
        </div>
        <div class="lineage-tree">
          <div
            v-for="node in group.nodes"
            :key="node.id"
            :class="['lineage-node', `lineage-node--depth-${Math.min(node.depth, 4)}`]"
          >
            <span class="lineage-node__gen">GEN {{ node.generation ?? node.depth }}</span>
            <span class="lineage-node__id font-mono text-muted">{{ truncateId(node.id) }}</span>
            <span :class="['status-chip', statusChipClass(node.status)]" style="font-size: 10px; padding: 2px 8px">
              <span class="status-dot" />
              {{ node.status?.toUpperCase() }}
            </span>
            <span
              v-if="node.sharpe != null"
              :class="['nb-value nb-value--sm', node.sharpe >= 1 ? 'nb-value--positive' : node.sharpe < 0 ? 'nb-value--negative' : '']"
            >
              SHARPE {{ node.sharpe.toFixed(2) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useResearchStore } from '@/stores/useResearchStore.js'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'

const store = useResearchStore()

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const TIMEFRAMES = ['M1', 'H1', 'H4', 'D']
const TASKS = [
  { value: 'generate_seed', label: 'DISCOVER' },
  { value: 'mutate', label: 'MUTATE' },
]

// ---------------------------------------------------------------------------
// Trigger panel state
// ---------------------------------------------------------------------------
const triggerPanelOpen = ref(false)
const lastTriggeredId = ref(null)
const formError = ref(null)

const form = ref({
  instrument: 'EUR_USD',
  timeframe: 'H1',
  test_start: '',
  test_end: '',
  task: 'discover',
  parent_experiment_id: '',
})

// ---------------------------------------------------------------------------
// Detail row state
// ---------------------------------------------------------------------------
const activeDetailRunId = ref(null)

const activeDetailRun = computed(() =>
  activeDetailRunId.value
    ? store.researchRuns.find((r) => r.id === activeDetailRunId.value) ?? null
    : null
)

function toggleDetailRun(id) {
  activeDetailRunId.value = activeDetailRunId.value === id ? null : id
}

// ---------------------------------------------------------------------------
// Supervisor status — derived from latest run
// ---------------------------------------------------------------------------
const latestRun = computed(() =>
  store.researchRuns.length > 0 ? store.researchRuns[0] : null
)

const supervisorState = computed(() => {
  const s = (latestRun.value?.status || '').toLowerCase()
  if (s === 'running') return 'running'
  if (s === 'succeeded') return 'succeeded'
  if (s === 'failed') return 'failed'
  return 'idle'
})

// ---------------------------------------------------------------------------
// Generation Lineage — only runs with parent_id
// ---------------------------------------------------------------------------
const lineageGroups = computed(() => {
  const hasParent = store.researchRuns.filter((r) => r.parent_id)
  if (!hasParent.length) return []

  // Build depth map using BFS over all runs
  const depthMap = new Map()
  store.researchRuns.forEach((r) => depthMap.set(r.id, r.generation ?? 0))

  // Group by instrument+timeframe
  const groups = {}
  store.researchRuns
    .filter((r) => r.parent_id)
    .forEach((r) => {
      const key = `${r.instrument} / ${r.timeframe}`
      if (!groups[key]) groups[key] = []
      groups[key].push({
        ...r,
        depth: depthMap.get(r.id) ?? 0,
      })
    })

  return Object.entries(groups).map(([key, nodes]) => ({
    key,
    nodes: nodes.sort((a, b) => (a.depth ?? 0) - (b.depth ?? 0)),
  }))
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function truncateId(id) {
  if (!id) return '—'
  return id.length > 16 ? id.slice(0, 8) + '…' + id.slice(-4) : id
}

function statusChipClass(status) {
  const s = (status || '').toLowerCase()
  const map = {
    running: 'status-chip--running',
    succeeded: 'status-chip--succeeded',
    failed: 'status-chip--failed',
    archived: 'status-chip--cancelled',
    queued: 'status-chip--queued',
  }
  return map[s] ?? 'status-chip--queued'
}

// ---------------------------------------------------------------------------
// Trigger handler
// ---------------------------------------------------------------------------
async function handleTrigger() {
  formError.value = null
  lastTriggeredId.value = null
  store.clearSubmitError()

  // Validation
  if (!form.value.instrument.trim()) {
    formError.value = 'INSTRUMENT is required'
    return
  }
  if (!form.value.test_start || !form.value.test_end) {
    formError.value = 'TEST START and TEST END are required'
    return
  }
  if (form.value.task === 'mutate' && !form.value.parent_experiment_id.trim()) {
    formError.value = 'PARENT EXPERIMENT ID is required for MUTATE task'
    return
  }

  const payload = {
    instrument: form.value.instrument.trim(),
    timeframe: form.value.timeframe,
    test_start: form.value.test_start,
    test_end: form.value.test_end,
    task: form.value.task,
  }
  if (form.value.task === 'mutate' && form.value.parent_experiment_id.trim()) {
    payload.parent_experiment_id = form.value.parent_experiment_id.trim()
  }

  try {
    const res = await store.triggerRun(payload)
    lastTriggeredId.value = res.experiment_id
    // Refresh list to show new run
    store.fetchResearchRuns()
  } catch {
    // error already set in store.submitError
  }
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
onMounted(() => {
  store.fetchResearchRuns()
})

onUnmounted(() => {
  store.stopPolling()
})
</script>

<style scoped>
.view-container {
  padding: 28px 32px;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--gap-lg);
}

/* ---- Header ---- */
.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.view-header__title {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.view-header__right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.view-header__badge {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.15em;
  color: var(--clr-text-dim);
  border: 1px solid var(--clr-border);
  padding: 4px 10px;
  text-transform: uppercase;
}

/* ---- Trigger panel ---- */
.trigger-panel__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--clr-border);
}

.trigger-panel__success {
  margin-bottom: 16px;
  font-family: var(--font-mono);
}

.trigger-panel__form {
  display: flex;
  flex-direction: column;
  gap: var(--gap-md);
}

.form-row {
  display: flex;
  gap: var(--gap-md);
  flex-wrap: wrap;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 160px;
  flex: 1;
}

.form-field__label {
  letter-spacing: 0.12em;
}

.nb-input {
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  color: var(--clr-text);
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 9px 12px;
  outline: none;
  transition: border-color 80ms;
  width: 100%;
  box-sizing: border-box;
}

.nb-input:focus {
  border-color: var(--clr-yellow);
}

.nb-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.nb-input[type="date"]::-webkit-calendar-picker-indicator {
  filter: invert(1);
  opacity: 0.5;
}

.tf-selector {
  display: flex;
  gap: 4px;
}

.tf-btn {
  font-size: 11px;
  padding: 7px 12px;
  border: 2px solid var(--clr-border);
  background: var(--clr-panel);
  color: var(--clr-text-muted);
  box-shadow: none;
}

.tf-btn--active {
  border-color: var(--clr-yellow);
  color: var(--clr-yellow);
  background: rgba(255, 230, 0, 0.06);
  box-shadow: 2px 2px 0px var(--clr-yellow);
}

.trigger-panel__actions {
  display: flex;
  align-items: center;
  gap: var(--gap-sm);
  padding-top: 4px;
}

.trigger-panel__submit {
  font-size: 13px;
  padding: 10px 28px;
}

/* ---- Supervisor status ---- */
.supervisor-status__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.supervisor-status__state {
  display: flex;
  align-items: center;
  gap: 10px;
}

.supervisor-status__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--clr-border);
  flex-wrap: wrap;
}

/* ---- Runs table ---- */
.runs-table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--clr-border);
}

.nb-table-wrapper {
  overflow-x: auto;
}

.run-row {
  cursor: pointer;
  transition: background 80ms;
}

.run-row:hover {
  background: var(--clr-panel-alt);
}

.run-row--selected {
  background: rgba(255, 230, 0, 0.04);
  border-left: 3px solid var(--clr-yellow);
}

.task-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 2px 8px;
  border: 1px solid;
}

.task-badge--discover {
  border-color: var(--clr-yellow);
  color: var(--clr-yellow);
  background: rgba(255, 230, 0, 0.06);
}

.task-badge--mutate {
  border-color: var(--clr-orange);
  color: var(--clr-orange);
  background: rgba(255, 107, 0, 0.08);
}

/* ---- Inline run detail ---- */
.run-detail {
  margin-top: 12px;
  border-top: 2px solid var(--clr-border-bright);
  padding-top: 16px;
}

.run-detail__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.run-detail__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--gap-sm);
  margin-bottom: 14px;
}

.run-detail__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: var(--clr-panel-alt);
  border: 1px solid var(--clr-border);
}

.run-detail__hypothesis {
  padding: 10px 12px;
  background: rgba(255, 230, 0, 0.03);
  border-left: 3px solid var(--clr-yellow);
  margin-bottom: 10px;
}

.hypothesis-text {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--clr-text);
  margin: 0;
}

.run-detail__taxonomy {
  padding: 8px 12px;
  background: rgba(255, 34, 34, 0.05);
  border-left: 3px solid var(--clr-red);
}

/* ---- Generation lineage ---- */
.lineage-group {
  margin-bottom: 20px;
}

.lineage-group__header {
  padding: 6px 12px;
  background: var(--clr-panel-alt);
  border: 1px solid var(--clr-border);
  border-bottom: none;
}

.lineage-tree {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--clr-border);
}

.lineage-node {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--clr-border);
  transition: background 80ms;
}

.lineage-node:last-child {
  border-bottom: none;
}

.lineage-node:hover {
  background: var(--clr-panel-alt);
}

/* Depth indentation via left padding */
.lineage-node--depth-0 { padding-left: 12px; }
.lineage-node--depth-1 { padding-left: 28px; border-left: 2px solid var(--clr-border); }
.lineage-node--depth-2 { padding-left: 44px; border-left: 2px solid var(--clr-orange); }
.lineage-node--depth-3 { padding-left: 60px; border-left: 2px solid var(--clr-yellow); }
.lineage-node--depth-4 { padding-left: 76px; border-left: 2px solid var(--clr-green); }

.lineage-node__gen {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--clr-yellow);
  min-width: 44px;
}

.lineage-node__id {
  font-size: 11px;
  flex: 1;
}
</style>
