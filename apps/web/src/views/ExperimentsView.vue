<template>
  <div class="view-container">
    <!-- ------------------------------------------------------------------ -->
    <!-- Header                                                               -->
    <!-- ------------------------------------------------------------------ -->
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">EXPERIMENTS</h1>
      </div>
      <div class="view-header__right">
        <button class="nb-btn nb-btn--primary" @click="openCreateDialog">
          CREATE EXPERIMENT
        </button>
        <span class="view-header__badge">PHASE 5B</span>
      </div>
    </header>

    <!-- ------------------------------------------------------------------ -->
    <!-- Main split layout                                                    -->
    <!-- ------------------------------------------------------------------ -->
    <div class="experiments-layout">
      <!-- ================================================================ -->
      <!-- LEFT COLUMN — Experiment list                                     -->
      <!-- ================================================================ -->
      <div class="experiments-list-col">
        <!-- Filter bar -->
        <div class="filter-bar">
          <div class="filter-bar__left">
            <span class="nb-label">FILTER</span>
            <div class="status-filter">
              <button
                v-for="opt in STATUS_OPTIONS"
                :key="opt.value"
                :class="['nb-btn', 'filter-btn', statusFilter === opt.value && 'filter-btn--active']"
                @click="applyStatusFilter(opt.value)"
              >
                {{ opt.label }}
              </button>
            </div>
          </div>
          <button
            class="nb-btn"
            style="font-size: 11px; padding: 6px 12px"
            :disabled="store.loading"
            @click="store.fetchExperiments(filterParams)"
          >
            REFRESH
          </button>
        </div>

        <!-- Loading -->
        <LoadingState v-if="store.loading && store.experiments.length === 0" message="LOADING..." />
        <ErrorBanner v-else-if="store.error && !store.selectedExperiment" :message="store.error" />
        <EmptyState v-else-if="store.experiments.length === 0" message="NO EXPERIMENTS" />

        <!-- Experiment rows -->
        <div v-else class="exp-list">
          <div
            v-for="exp in store.experiments"
            :key="exp.id"
            :class="[
              'exp-row',
              store.selectedExperiment?.id === exp.id && 'exp-row--selected',
            ]"
            @click="handleSelectExperiment(exp)"
          >
            <div class="exp-row__top">
              <span class="exp-row__name">{{ exp.name }}</span>
              <span :class="['status-chip', expStatusChip(exp.status)]">
                <span class="status-dot" />
                {{ exp.status?.toUpperCase() }}
              </span>
            </div>
            <div class="exp-row__meta">
              <span class="font-mono text-muted" style="font-size: 11px">{{ exp.instrument }}</span>
              <span class="font-mono text-muted" style="font-size: 11px">{{ exp.timeframe }}</span>
              <span class="nb-label text-dim">GEN {{ exp.generation_count ?? 0 }}</span>
            </div>
          </div>
        </div>

        <!-- Count footer -->
        <div v-if="store.experiments.length > 0" class="list-footer">
          <span class="nb-label text-muted">{{ store.experiments.length }} / {{ store.count }}</span>
        </div>
      </div>

      <!-- ================================================================ -->
      <!-- RIGHT COLUMN — Experiment detail                                  -->
      <!-- ================================================================ -->
      <div class="experiments-detail-col">
        <!-- No selection -->
        <EmptyState
          v-if="!store.selectedExperiment"
          message="SELECT AN EXPERIMENT"
        />

        <!-- Detail loading -->
        <LoadingState
          v-else-if="store.loading && !store.selectedExperimentDetail"
          message="LOADING DETAIL..."
        />

        <template v-else-if="store.selectedExperiment">
          <!-- ---- Experiment header ---- -->
          <div class="nb-card detail-header-card">
            <div class="detail-header-card__top">
              <div class="detail-header-card__title">
                <h2 class="nb-heading nb-heading--lg">{{ store.selectedExperiment.name }}</h2>
                <span :class="['status-chip', expStatusChip(store.selectedExperiment.status)]">
                  <span class="status-dot" />
                  {{ store.selectedExperiment.status?.toUpperCase() }}
                </span>
              </div>
              <div class="detail-header-card__meta-row">
                <div class="meta-item">
                  <span class="nb-label">INSTRUMENT</span>
                  <span class="nb-value nb-value--sm font-mono">{{ store.selectedExperiment.instrument }}</span>
                </div>
                <div class="meta-item">
                  <span class="nb-label">TIMEFRAME</span>
                  <span class="nb-value nb-value--sm font-mono">{{ store.selectedExperiment.timeframe }}</span>
                </div>
                <div class="meta-item">
                  <span class="nb-label">START</span>
                  <span class="nb-value nb-value--sm font-mono">{{ store.selectedExperiment.test_start }}</span>
                </div>
                <div class="meta-item">
                  <span class="nb-label">END</span>
                  <span class="nb-value nb-value--sm font-mono">{{ store.selectedExperiment.test_end }}</span>
                </div>
                <div v-if="store.selectedExperiment.requested_by" class="meta-item">
                  <span class="nb-label">BY</span>
                  <span class="nb-value nb-value--sm font-mono">{{ store.selectedExperiment.requested_by }}</span>
                </div>
              </div>
              <!-- Tags -->
              <div v-if="store.selectedExperiment.tags?.length" class="detail-header-card__tags">
                <span
                  v-for="tag in store.selectedExperiment.tags"
                  :key="tag"
                  class="tag-chip"
                >
                  {{ tag }}
                </span>
              </div>
              <div v-if="store.selectedExperiment.description" class="detail-header-card__desc">
                <span class="nb-label text-muted" style="font-style: normal">{{ store.selectedExperiment.description }}</span>
              </div>
            </div>

            <!-- Status transition actions -->
            <div v-if="allowedTransitions.length > 0" class="status-actions">
              <span class="nb-label" style="margin-right: 10px">TRANSITION</span>
              <button
                v-for="t in allowedTransitions"
                :key="t.status"
                :class="['nb-btn', t.danger && 'nb-btn--danger']"
                style="font-size: 11px; padding: 7px 14px"
                :disabled="store.loading"
                @click="handleStatusTransition(t.status)"
              >
                {{ t.label }}
              </button>
            </div>

            <!-- Error banner for status update -->
            <ErrorBanner v-if="statusUpdateError" :message="statusUpdateError" />
          </div>

          <!-- ---- Lineage / iterations ---- -->
          <div class="nb-card">
            <div class="section-header">
              <span class="nb-heading nb-heading--sm">LINEAGE</span>
              <span class="nb-label text-muted">GENERATION {{ store.selectedExperiment.generation_count ?? 0 }}</span>
            </div>

            <template v-if="store.selectedExperimentDetail?.iterations?.length">
              <div class="nb-table-wrapper">
                <table class="nb-table">
                  <thead>
                    <tr>
                      <th>GEN</th>
                      <th>STRATEGY ID</th>
                      <th>BACKTEST ID</th>
                      <th>STATUS</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="iter in store.selectedExperimentDetail.iterations"
                      :key="iter.id ?? iter.generation"
                    >
                      <td class="font-mono">{{ iter.generation ?? '—' }}</td>
                      <td class="font-mono text-muted" style="font-size: 11px">{{ truncateId(iter.strategy_id) }}</td>
                      <td class="font-mono text-muted" style="font-size: 11px">{{ truncateId(iter.backtest_run_id) }}</td>
                      <td>
                        <span :class="['status-chip', expStatusChip(iter.status)]" style="font-size: 10px">
                          <span class="status-dot" />
                          {{ iter.status?.toUpperCase() ?? '—' }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
            <EmptyState v-else message="NO ITERATIONS YET" />
          </div>

          <!-- ---- Robustness Battery ---- -->
          <RobustnessPanel
            :experiment-id="store.selectedExperiment.id"
            @approved="handleApproved"
            @discarded="handleDiscarded"
          />
        </template>
      </div>
    </div>

    <!-- ------------------------------------------------------------------ -->
    <!-- Create Experiment Dialog                                             -->
    <!-- ------------------------------------------------------------------ -->
    <div
      v-if="showCreateDialog"
      class="dialog-backdrop"
      @click.self="showCreateDialog = false"
    >
      <div class="dialog nb-card">
        <div class="dialog__header">
          <span class="nb-heading nb-heading--md">CREATE EXPERIMENT</span>
          <button class="nb-btn" style="font-size: 10px; padding: 4px 10px" @click="showCreateDialog = false">
            CLOSE
          </button>
        </div>

        <ErrorBanner v-if="createError" :message="createError" />

        <div class="dialog__form">
          <div class="form-field">
            <label class="nb-label form-field__label">NAME</label>
            <input v-model="createForm.name" class="nb-input" type="text" placeholder="EUR_USD H1 Trend Discovery" :disabled="creating" />
          </div>
          <div class="form-field">
            <label class="nb-label form-field__label">DESCRIPTION (OPTIONAL)</label>
            <input v-model="createForm.description" class="nb-input" type="text" placeholder="Describe the experiment goal..." :disabled="creating" />
          </div>
          <div class="form-row">
            <div class="form-field">
              <label class="nb-label form-field__label">INSTRUMENT</label>
              <input v-model="createForm.instrument" class="nb-input" type="text" placeholder="EUR_USD" :disabled="creating" />
            </div>
            <div class="form-field">
              <label class="nb-label form-field__label">TIMEFRAME</label>
              <div class="tf-selector">
                <button
                  v-for="tf in TIMEFRAMES"
                  :key="tf"
                  :class="['nb-btn', 'tf-btn', createForm.timeframe === tf && 'tf-btn--active']"
                  :disabled="creating"
                  @click="createForm.timeframe = tf"
                >
                  {{ tf }}
                </button>
              </div>
            </div>
          </div>
          <div class="form-row">
            <div class="form-field">
              <label class="nb-label form-field__label">TEST START</label>
              <input v-model="createForm.test_start" class="nb-input" type="date" :disabled="creating" />
            </div>
            <div class="form-field">
              <label class="nb-label form-field__label">TEST END</label>
              <input v-model="createForm.test_end" class="nb-input" type="date" :disabled="creating" />
            </div>
          </div>
          <div class="form-field">
            <label class="nb-label form-field__label">REQUESTED BY (OPTIONAL)</label>
            <input v-model="createForm.requested_by" class="nb-input" type="text" placeholder="analyst name / agent" :disabled="creating" />
          </div>
          <div v-if="createFormError" class="nb-banner nb-banner--error">{{ createFormError }}</div>
        </div>

        <div class="dialog__footer">
          <button
            class="nb-btn nb-btn--primary"
            :disabled="creating"
            @click="handleCreate"
          >
            {{ creating ? 'CREATING...' : 'CREATE' }}
          </button>
          <button class="nb-btn" :disabled="creating" @click="showCreateDialog = false">
            CANCEL
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useExperimentStore } from '@/stores/useExperimentStore.js'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import RobustnessPanel from '@/components/experiments/RobustnessPanel.vue'

const store = useExperimentStore()

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const TIMEFRAMES = ['M1', 'H1', 'H4', 'D']

const STATUS_OPTIONS = [
  { value: '', label: 'ALL' },
  { value: 'active', label: 'ACTIVE' },
  { value: 'paused', label: 'PAUSED' },
  { value: 'completed', label: 'COMPLETED' },
  { value: 'archived', label: 'ARCHIVED' },
  { value: 'validated', label: 'VALIDATED' },
  { value: 'discarded', label: 'DISCARDED' },
]

// Allowed status transitions per current status
const TRANSITIONS = {
  active: [
    { status: 'paused', label: 'PAUSE' },
    { status: 'completed', label: 'COMPLETE' },
    { status: 'archived', label: 'ARCHIVE', danger: true },
  ],
  paused: [
    { status: 'active', label: 'RESUME' },
    { status: 'archived', label: 'ARCHIVE', danger: true },
  ],
  completed: [
    { status: 'archived', label: 'ARCHIVE', danger: true },
  ],
}

// ---------------------------------------------------------------------------
// Filter state
// ---------------------------------------------------------------------------
const statusFilter = ref('')

const filterParams = computed(() => {
  const p = { limit: 50 }
  if (statusFilter.value) p.status = statusFilter.value
  return p
})

function applyStatusFilter(value) {
  statusFilter.value = value
  store.fetchExperiments(filterParams.value)
}

// ---------------------------------------------------------------------------
// Status actions
// ---------------------------------------------------------------------------
const statusUpdateError = ref(null)

const allowedTransitions = computed(() => {
  const status = store.selectedExperiment?.status?.toLowerCase()
  return TRANSITIONS[status] ?? []
})

async function handleStatusTransition(newStatus) {
  if (!store.selectedExperiment) return
  statusUpdateError.value = null
  try {
    await store.updateStatus(store.selectedExperiment.id, newStatus)
  } catch (err) {
    statusUpdateError.value = err?.normalized?.message ?? err.message ?? 'Status update failed'
  }
}

// ---------------------------------------------------------------------------
// Experiment selection
// ---------------------------------------------------------------------------
async function handleSelectExperiment(exp) {
  statusUpdateError.value = null
  await store.selectExperiment(exp.id)
}

// ---------------------------------------------------------------------------
// Robustness panel callbacks
// ---------------------------------------------------------------------------
function handleApproved(experiment) {
  // The experiment has been approved — refresh list + detail
  store.fetchExperiments(filterParams.value)
  if (store.selectedExperiment?.id === experiment.id) {
    store.selectedExperiment = experiment
    if (store.selectedExperimentDetail) {
      store.selectedExperimentDetail = {
        ...store.selectedExperimentDetail,
        experiment,
      }
    }
  }
}

function handleDiscarded(experiment) {
  store.fetchExperiments(filterParams.value)
  if (store.selectedExperiment?.id === experiment.id) {
    store.selectedExperiment = experiment
    if (store.selectedExperimentDetail) {
      store.selectedExperimentDetail = {
        ...store.selectedExperimentDetail,
        experiment,
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Create dialog
// ---------------------------------------------------------------------------
const showCreateDialog = ref(false)
const creating = ref(false)
const createError = ref(null)
const createFormError = ref(null)

const createForm = ref({
  name: '',
  description: '',
  instrument: 'EUR_USD',
  timeframe: 'H1',
  test_start: '',
  test_end: '',
  requested_by: '',
})

function openCreateDialog() {
  createError.value = null
  createFormError.value = null
  createForm.value = {
    name: '',
    description: '',
    instrument: 'EUR_USD',
    timeframe: 'H1',
    test_start: '',
    test_end: '',
    requested_by: '',
  }
  showCreateDialog.value = true
}

async function handleCreate() {
  createFormError.value = null
  createError.value = null

  if (!createForm.value.name.trim()) {
    createFormError.value = 'NAME is required'
    return
  }
  if (!createForm.value.instrument.trim()) {
    createFormError.value = 'INSTRUMENT is required'
    return
  }
  if (!createForm.value.test_start || !createForm.value.test_end) {
    createFormError.value = 'TEST START and TEST END are required'
    return
  }

  const payload = {
    name: createForm.value.name.trim(),
    instrument: createForm.value.instrument.trim(),
    timeframe: createForm.value.timeframe,
    test_start: createForm.value.test_start,
    test_end: createForm.value.test_end,
  }
  if (createForm.value.description.trim()) {
    payload.description = createForm.value.description.trim()
  }
  if (createForm.value.requested_by.trim()) {
    payload.requested_by = createForm.value.requested_by.trim()
  }

  creating.value = true
  try {
    const data = await store.createNewExperiment(payload)
    showCreateDialog.value = false
    // Auto-select the newly created experiment
    const created = data.experiment ?? data
    if (created?.id) {
      await store.selectExperiment(created.id)
    }
  } catch (err) {
    createError.value = err?.normalized?.message ?? err.message ?? 'Failed to create experiment'
  } finally {
    creating.value = false
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function truncateId(id) {
  if (!id) return '—'
  return id.length > 16 ? id.slice(0, 8) + '…' + id.slice(-4) : id
}

function expStatusChip(status) {
  const s = (status || '').toLowerCase()
  const map = {
    active: 'status-chip--running',
    running: 'status-chip--running',
    paused: 'status-chip--queued',
    completed: 'status-chip--succeeded',
    succeeded: 'status-chip--succeeded',
    archived: 'status-chip--cancelled',
    validated: 'status-chip--succeeded',
    discarded: 'status-chip--failed',
    failed: 'status-chip--failed',
  }
  return map[s] ?? 'status-chip--queued'
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
onMounted(() => {
  store.fetchExperiments()
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

/* ---- Split layout ---- */
.experiments-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: var(--gap-lg);
  min-height: 0;
  align-items: start;
}

/* ---- Left column ---- */
.experiments-list-col {
  display: flex;
  flex-direction: column;
  gap: var(--gap-sm);
  position: sticky;
  top: 0;
}

.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--gap-sm);
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--clr-surface);
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb);
}

.filter-bar__left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.status-filter {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.filter-btn {
  font-size: 10px;
  padding: 5px 10px;
  border: 1px solid var(--clr-border);
  background: var(--clr-panel);
  color: var(--clr-text-muted);
  box-shadow: none;
}

.filter-btn--active {
  border-color: var(--clr-yellow);
  color: var(--clr-yellow);
  background: rgba(255, 230, 0, 0.06);
  box-shadow: 2px 2px 0px var(--clr-yellow);
}

.exp-list {
  display: flex;
  flex-direction: column;
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb);
  background: var(--clr-surface);
}

.exp-row {
  padding: 12px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--clr-border);
  transition: background 80ms;
}

.exp-row:last-child {
  border-bottom: none;
}

.exp-row:hover {
  background: var(--clr-panel);
}

.exp-row--selected {
  background: rgba(255, 230, 0, 0.04);
  border-left: 3px solid var(--clr-yellow);
}

.exp-row__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.exp-row__name {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  color: var(--clr-text);
  letter-spacing: 0.04em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.exp-row__meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.list-footer {
  padding: 8px 14px;
  background: var(--clr-panel);
  border: 1px solid var(--clr-border);
  border-top: none;
}

/* ---- Right column ---- */
.experiments-detail-col {
  display: flex;
  flex-direction: column;
  gap: var(--gap-lg);
}

/* ---- Detail header card ---- */
.detail-header-card__top {
  margin-bottom: 16px;
}

.detail-header-card__title {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--clr-border);
}

.detail-header-card__meta-row {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-header-card__tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.tag-chip {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  padding: 3px 10px;
  border: 1px solid var(--clr-border-bright);
  color: var(--clr-text-muted);
  background: var(--clr-panel-alt);
  text-transform: uppercase;
}

.detail-header-card__desc {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
}

.status-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--clr-border);
  flex-wrap: wrap;
}

/* ---- Section header ---- */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--clr-border);
}

.nb-table-wrapper {
  overflow-x: auto;
}

/* ---- Forms (shared with ResearchView) ---- */
.form-row {
  display: flex;
  gap: var(--gap-md);
  flex-wrap: wrap;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 140px;
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

/* ---- Dialog ---- */
.dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  padding: 16px;
}

.dialog {
  width: 100%;
  max-width: 520px;
  background: var(--clr-surface);
  max-height: 90vh;
  overflow-y: auto;
}

.dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--clr-border);
}

.dialog__form {
  display: flex;
  flex-direction: column;
  gap: var(--gap-md);
  margin-bottom: 20px;
}

.dialog__footer {
  display: flex;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--clr-border);
  flex-wrap: wrap;
}
</style>
