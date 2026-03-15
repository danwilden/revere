<template>
  <div class="view-container">
    <!-- Page header -->
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">BACKTESTS</h1>
      </div>
      <span class="view-header__badge">PHASE 6C</span>
    </header>

    <!-- Research grade warning — always visible -->
    <WarningBanner :message="RESEARCH_GRADE_WARNING" class="view-research-warn" />

    <!-- Main two-column layout -->
    <div class="view-body">

      <!-- ===== LEFT: Submission form ===== -->
      <section class="col-form">
        <NbCard title="RUN BACKTEST">

          <!-- Error from submission attempt -->
          <ErrorBanner v-if="store.submitError" :message="store.submitError" style="margin-bottom: 16px" />

          <!-- Active job panel (shows while job is in-flight OR has a terminal status) -->
          <template v-if="store.activeJob">
            <JobStatusPanel :job="store.activeJob" title="JOB STATUS" style="margin-bottom: 16px">
              <!-- Success inline actions -->
              <template v-if="isJobSucceeded">
                <div class="nb-banner nb-banner--info success-banner" style="margin-top: 12px">
                  BACKTEST COMPLETE — navigating to results...
                </div>
                <button
                  class="nb-btn nb-btn--primary"
                  style="margin-top: 12px; width: 100%"
                  @click="goToResults"
                >
                  VIEW RESULTS NOW
                </button>
              </template>
            </JobStatusPanel>

            <!-- New run button — only show when terminal -->
            <button
              v-if="isJobTerminal"
              class="nb-btn"
              style="margin-bottom: 20px; width: 100%"
              @click="store.resetJob"
            >
              NEW BACKTEST
            </button>
          </template>

          <!-- Form — hidden when job is running/queued to prevent double-submission -->
          <form v-if="!isJobActive" class="backtest-form" @submit.prevent="handleSubmit">

            <!-- Strategy selector -->
            <div class="form-field">
              <label class="nb-label form-label">STRATEGY</label>
              <div v-if="store.isLoadingStrategies" class="field-loading">
                <span class="nb-label">LOADING...</span>
              </div>
              <select
                v-else
                v-model="form.strategy_id"
                class="nb-select"
                required
              >
                <option value="" disabled>— Select strategy —</option>
                <option
                  v-for="s in store.strategies"
                  :key="s.id"
                  :value="s.id"
                >
                  {{ s.name }} [{{ (s.strategy_type ?? s.type ?? 'unknown').toUpperCase() }}]
                </option>
              </select>
              <span v-if="errors.strategy_id" class="field-error">{{ errors.strategy_id }}</span>
            </div>

            <!-- Instrument selector + pip size display -->
            <div class="form-field">
              <label class="nb-label form-label">INSTRUMENT</label>
              <div v-if="store.isLoadingInstruments" class="field-loading">
                <span class="nb-label">LOADING...</span>
              </div>
              <select
                v-else
                v-model="form.instrument_id"
                class="nb-select"
                required
                @change="onInstrumentChange"
              >
                <option value="" disabled>— Select instrument —</option>
                <option
                  v-for="inst in store.instruments"
                  :key="inst.instrument_id ?? inst.id"
                  :value="inst.instrument_id ?? inst.id"
                >
                  {{ inst.instrument_id ?? inst.id }}
                  {{ inst.display_name ? `— ${inst.display_name}` : '' }}
                </option>
              </select>
              <span v-if="errors.instrument_id" class="field-error">{{ errors.instrument_id }}</span>
            </div>

            <!-- Pip size display (read-only, derived from instrument) -->
            <div class="form-field form-field--inline">
              <div class="pip-display">
                <span class="nb-label">PIP SIZE (auto)</span>
                <span :class="['font-mono', 'pip-value', pipSizeIsJpy && 'pip-value--jpy']">
                  {{ form.pip_size }}
                  <span v-if="pipSizeIsJpy" class="pip-jpy-tag">JPY</span>
                </span>
              </div>
            </div>
            <WarningBanner :message="PIP_SIZE_WARNING" class="pip-warning" />

            <!-- Timeframe -->
            <div class="form-field">
              <label class="nb-label form-label">TIMEFRAME</label>
              <div class="timeframe-group">
                <button
                  v-for="tf in TIMEFRAMES"
                  :key="tf"
                  type="button"
                  :class="['nb-btn', 'tf-btn', form.timeframe === tf && 'tf-btn--active']"
                  @click="form.timeframe = tf"
                >
                  {{ tf }}
                </button>
              </div>
              <span v-if="errors.timeframe" class="field-error">{{ errors.timeframe }}</span>
            </div>

            <!-- HMM Model selector -->
            <div class="form-field">
              <label class="nb-label form-label">
                HMM MODEL
                <span class="optional-tag">optional</span>
              </label>
              <div v-if="store.modelsLoading" class="field-loading">
                <span class="nb-label">LOADING...</span>
              </div>
              <select
                v-else
                v-model="form.model_id"
                class="nb-select"
                @change="onModelChange"
              >
                <option value="">None — strategy doesn't use regime_label</option>
                <option
                  v-for="m in store.models"
                  :key="m.id"
                  :value="m.id"
                >
                  {{ m.instrument }} {{ m.timeframe }} — {{ m.num_states }} states ({{ formatDate(m.training_start) }} → {{ formatDate(m.training_end) }}){{ !modelMatchesCurrent(m) ? ' (different pair/tf)' : '' }}
                </option>
              </select>
              <!-- Regime label guard warning -->
              <div
                v-if="!form.model_id && form.instrument_id"
                class="regime-warn"
              >
                No model selected — strategies using <code>regime_label</code> will fail. Select an HMM model if your strategy references regime state.
              </div>
            </div>

            <!-- Date range -->
            <div class="form-row">
              <div class="form-field">
                <label class="nb-label form-label">TEST START</label>
                <input
                  v-model="form.start_date"
                  type="date"
                  class="nb-input"
                  :min="effectiveMinDate"
                  :max="effectiveMaxDate"
                  required
                />
                <span v-if="errors.start_date" class="field-error">{{ errors.start_date }}</span>
              </div>
              <div class="form-field">
                <label class="nb-label form-label">TEST END</label>
                <input
                  v-model="form.end_date"
                  type="date"
                  class="nb-input"
                  :min="effectiveMinDate"
                  :max="effectiveMaxDate"
                  required
                />
                <span v-if="errors.end_date" class="field-error">{{ errors.end_date }}</span>
              </div>
            </div>
            <!-- Data availability caption -->
            <div class="date-range-caption">
              <span v-if="rangesLoading" class="nb-label range-loading">CHECKING DATA...</span>
              <span v-else-if="form.instrument_id && !hasData" class="range-no-data">
                NO DATA — ingest {{ form.instrument_id }} {{ form.timeframe }} before running
              </span>
              <span v-else-if="rangeLabel" class="range-available">{{ rangeLabel }}</span>
            </div>

            <!-- Initial equity -->
            <div class="form-field">
              <label class="nb-label form-label">INITIAL EQUITY ($)</label>
              <input
                v-model.number="form.initial_equity"
                type="number"
                class="nb-input"
                min="100"
                step="100"
                required
              />
            </div>

            <!-- Cost parameters (collapsible) -->
            <div class="form-field">
              <button
                type="button"
                class="nb-btn costs-toggle"
                @click="showCosts = !showCosts"
              >
                <span class="costs-toggle__arrow">{{ showCosts ? '▼' : '▶' }}</span>
                COST PARAMETERS
              </button>
            </div>

            <div v-if="showCosts" class="costs-section nb-panel">
              <div class="form-row">
                <div class="form-field">
                  <label class="nb-label form-label">SPREAD (pips)</label>
                  <input
                    v-model.number="form.spread_pips"
                    type="number"
                    class="nb-input"
                    min="0"
                    step="0.1"
                  />
                </div>
                <div class="form-field">
                  <label class="nb-label form-label">SLIPPAGE (pips)</label>
                  <input
                    v-model.number="form.slippage_pips"
                    type="number"
                    class="nb-input"
                    min="0"
                    step="0.1"
                  />
                </div>
              </div>
              <div class="form-field">
                <label class="nb-label form-label">COMMISSION / UNIT ($)</label>
                <input
                  v-model.number="form.commission_per_unit"
                  type="number"
                  class="nb-input"
                  min="0"
                  step="0.0001"
                />
              </div>
            </div>

            <!-- Submit -->
            <button
              type="submit"
              class="nb-btn nb-btn--primary submit-btn"
              :disabled="store.isSubmitting"
            >
              <span v-if="store.isSubmitting">SUBMITTING...</span>
              <span v-else>RUN BACKTEST</span>
            </button>

          </form>
        </NbCard>
      </section>

      <!-- ===== RIGHT: Recent runs ===== -->
      <section class="col-runs">
        <NbCard title="RECENT RUNS">
          <template #header-right>
            <button class="nb-btn" style="font-size: 11px; padding: 4px 10px" @click="store.fetchRecentRuns">
              REFRESH
            </button>
          </template>

          <LoadingState v-if="store.isLoadingRuns" message="LOADING RUNS..." />

          <EmptyState
            v-else-if="store.recentRuns.length === 0"
            message="No backtest runs yet. Submit a job to get started."
          />

          <div v-else class="runs-list">
            <RunSummaryCard
              v-for="run in sortedRuns"
              :key="run.id"
              :run="run"
              @view-results="handleViewResults"
            />
          </div>
        </NbCard>
      </section>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

import { useBacktestStore } from '@/stores/backtest.js'
import { useJobPoller } from '@/composables/useJobPoller.js'
import { useDataRanges } from '@/composables/useDataRanges.js'
import { getHmmModel } from '@/api/models.js'

import NbCard from '@/components/ui/NbCard.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import WarningBanner from '@/components/ui/WarningBanner.vue'
import JobStatusPanel from '@/components/ui/JobStatusPanel.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import RunSummaryCard from '@/components/backtest/RunSummaryCard.vue'

import {
  RESEARCH_GRADE_WARNING,
  PIP_SIZE_WARNING,
  getPipSize,
} from '@/utils/constants.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TIMEFRAMES = ['M1', 'H1', 'H4', 'D']

// ---------------------------------------------------------------------------
// Store + Router
// ---------------------------------------------------------------------------

const store = useBacktestStore()
const router = useRouter()

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

const form = ref({
  strategy_id: '',
  instrument_id: '',
  timeframe: 'H1',
  start_date: '',
  end_date: '',
  model_id: '',
  initial_equity: 10000,
  spread_pips: 1.0,
  slippage_pips: 0.5,
  commission_per_unit: 0.0,
  pip_size: 0.0001,
})

const errors = ref({})
const showCosts = ref(false)

// ---------------------------------------------------------------------------
// Derived
// ---------------------------------------------------------------------------

const pipSizeIsJpy = computed(() => form.value.pip_size === 0.01)

const isJobActive = computed(() => {
  const s = store.activeJob?.status?.toLowerCase()
  return s === 'queued' || s === 'running'
})

const isJobTerminal = computed(() => {
  const s = store.activeJob?.status?.toLowerCase()
  return ['succeeded', 'failed', 'cancelled'].includes(s)
})

const isJobSucceeded = computed(() =>
  store.activeJob?.status?.toLowerCase() === 'succeeded'
)

const sortedRuns = computed(() =>
  [...store.recentRuns].sort((a, b) => {
    const ta = new Date(a.created_at ?? 0).getTime()
    const tb = new Date(b.created_at ?? 0).getTime()
    return tb - ta
  })
)

// ---------------------------------------------------------------------------
// Data availability range (from useDataRanges composable)
// ---------------------------------------------------------------------------

const instrumentIdRef = computed(() => form.value.instrument_id)
const timeframeRef = computed(() => form.value.timeframe)
const { minDate, maxDate, rangeLabel, hasData, isLoading: rangesLoading } = useDataRanges(instrumentIdRef, timeframeRef)

// ---------------------------------------------------------------------------
// HMM model date range (fetched on model selection)
// ---------------------------------------------------------------------------

const selectedModelDateRange = ref({ min: '', max: '' })

async function onModelChange() {
  const modelId = form.value.model_id
  if (!modelId) {
    selectedModelDateRange.value = { min: '', max: '' }
    return
  }
  try {
    const model = await getHmmModel(modelId)
    selectedModelDateRange.value = {
      min: model.training_start?.slice(0, 10) ?? '',
      max: model.training_end?.slice(0, 10) ?? '',
    }
  } catch (e) {
    console.warn('Could not fetch model details', e)
    selectedModelDateRange.value = { min: '', max: '' }
  }
}

// Effective date bounds: intersection of data range and model training range
const effectiveMinDate = computed(() => {
  const a = minDate.value
  const b = selectedModelDateRange.value.min
  if (!a && !b) return ''
  if (!a) return b
  if (!b) return a
  return a > b ? a : b
})

const effectiveMaxDate = computed(() => {
  const a = maxDate.value
  const b = selectedModelDateRange.value.max
  if (!a && !b) return ''
  if (!a) return b
  if (!b) return a
  return a < b ? a : b
})

// Clamp selected dates when the effective bounds tighten
watch(effectiveMinDate, (min) => {
  if (min && form.value.start_date < min) form.value.start_date = min
})
watch(effectiveMaxDate, (max) => {
  if (max && form.value.end_date > max) form.value.end_date = max
})

// ---------------------------------------------------------------------------
// Model option display helpers
// ---------------------------------------------------------------------------

function formatDate(isoStr) {
  if (!isoStr) return '?'
  return isoStr.slice(0, 10)
}

function modelMatchesCurrent(model) {
  const instrMatch = !form.value.instrument_id || model.instrument === form.value.instrument_id
  const tfMatch = !form.value.timeframe || model.timeframe === form.value.timeframe
  return instrMatch && tfMatch
}

// ---------------------------------------------------------------------------
// Instrument change handler — recompute pip_size immediately
// ---------------------------------------------------------------------------

function onInstrumentChange() {
  form.value.pip_size = getPipSize(form.value.instrument_id)
}

// Also watch instrument_id in case programmatic assignment happens
watch(
  () => form.value.instrument_id,
  (id) => {
    form.value.pip_size = getPipSize(id)
  }
)

// ---------------------------------------------------------------------------
// Form validation
// ---------------------------------------------------------------------------

function validate() {
  const e = {}
  if (!form.value.strategy_id) e.strategy_id = 'Strategy is required.'
  if (!form.value.instrument_id) e.instrument_id = 'Instrument is required.'
  if (!form.value.timeframe) e.timeframe = 'Timeframe is required.'
  if (!form.value.start_date) e.start_date = 'Start date is required.'
  if (!form.value.end_date) e.end_date = 'End date is required.'
  if (form.value.start_date && form.value.end_date && form.value.start_date >= form.value.end_date) {
    e.end_date = 'End date must be after start date.'
  }
  errors.value = e
  return Object.keys(e).length === 0
}

// ---------------------------------------------------------------------------
// Job polling
// ---------------------------------------------------------------------------

let pollerCleanup = null

function startPolling(jobId) {
  stopPolling()
  const { stop } = useJobPoller(jobId, {
    onProgress: store.updateJobState,
    onComplete: handleJobComplete,
    onError: store.onJobError,
  })
  pollerCleanup = stop
}

function stopPolling() {
  if (pollerCleanup) {
    pollerCleanup()
    pollerCleanup = null
  }
}

function handleJobComplete(job) {
  store.onJobComplete(job)
  // Auto-navigate after 1.5s, but only if still on this page
  setTimeout(() => {
    if (store.completedRunId) {
      router.push({ name: 'Results', query: { runId: store.completedRunId } })
    }
  }, 1500)
}

// ---------------------------------------------------------------------------
// Submit handler
// ---------------------------------------------------------------------------

async function handleSubmit() {
  if (!validate()) return

  const payload = {
    strategy_id: form.value.strategy_id,
    instrument_id: form.value.instrument_id,
    timeframe: form.value.timeframe,
    start_date: form.value.start_date,
    end_date: form.value.end_date,
    model_id: form.value.model_id || null,
    pip_size: form.value.pip_size,
    initial_equity: form.value.initial_equity,
    spread_pips: form.value.spread_pips,
    slippage_pips: form.value.slippage_pips,
    commission_per_unit: form.value.commission_per_unit,
  }

  try {
    const { job_id } = await store.submitJob(payload)
    startPolling(job_id)
  } catch {
    // submitError is already set in the store; no further action needed
  }
}

// ---------------------------------------------------------------------------
// Navigation helpers
// ---------------------------------------------------------------------------

function goToResults() {
  if (store.completedRunId) {
    router.push({ name: 'Results', query: { runId: store.completedRunId } })
  }
}

function handleViewResults(runId) {
  router.push({ name: 'Results', query: { runId } })
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  store.fetchInstruments()
  store.fetchStrategies()
  store.fetchModels()
  store.fetchRecentRuns()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
/* ============================================================
   Layout
   ============================================================ */

.view-container {
  padding: 28px 32px;
  min-height: 100%;
}

.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.view-header__title {
  display: flex;
  flex-direction: column;
  gap: 6px;
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

.view-research-warn {
  margin-bottom: 20px;
}

.view-body {
  display: grid;
  grid-template-columns: 420px 1fr;
  gap: 20px;
  align-items: start;
}

/* Collapse to single column on narrow viewports */
@media (max-width: 900px) {
  .view-body {
    grid-template-columns: 1fr;
  }
}

/* ============================================================
   Form
   ============================================================ */

.backtest-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-field--inline {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
}

.form-label {
  font-size: 10px;
  letter-spacing: 0.14em;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.field-loading {
  padding: 8px 0;
  color: var(--clr-text-muted);
}

.field-error {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--clr-red);
  letter-spacing: 0.05em;
}

/* Native select styled to match neobrutalist spec */
.nb-select {
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--clr-surface);
  color: var(--clr-text);
  border: 2px solid var(--clr-border);
  padding: 8px 12px;
  width: 100%;
  cursor: pointer;
  box-shadow: var(--shadow-nb-sm);
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%23888' d='M6 8L0 0h12z'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  padding-right: 32px;
}

.nb-select:focus {
  outline: none;
  border-color: var(--clr-yellow);
}

.nb-select option {
  background: var(--clr-surface);
  color: var(--clr-text);
}

/* Native date/number inputs */
.nb-input {
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--clr-surface);
  color: var(--clr-text);
  border: 2px solid var(--clr-border);
  padding: 8px 12px;
  width: 100%;
  box-shadow: var(--shadow-nb-sm);
}

.nb-input:focus {
  outline: none;
  border-color: var(--clr-yellow);
}

/* Timeframe button group */
.timeframe-group {
  display: flex;
  gap: 6px;
}

.tf-btn {
  flex: 1;
  padding: 7px 0;
  font-size: 12px;
  text-align: center;
  justify-content: center;
}

.tf-btn--active {
  background: var(--clr-yellow);
  border-color: var(--clr-yellow);
  color: #000;
  box-shadow: var(--shadow-nb-yellow);
}

/* Pip size display */
.pip-display {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pip-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--clr-text);
  letter-spacing: 0.05em;
  display: flex;
  align-items: center;
  gap: 8px;
}

.pip-value--jpy {
  color: var(--clr-orange);
}

.pip-jpy-tag {
  font-size: 10px;
  background: var(--clr-orange);
  color: #000;
  padding: 2px 6px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

.pip-warning {
  margin-top: -4px;
}

/* Cost params */
.costs-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  font-size: 11px;
}

.costs-toggle__arrow {
  font-size: 10px;
  color: var(--clr-yellow);
}

.costs-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: -4px;
}

/* Submit button */
.submit-btn {
  width: 100%;
  padding: 14px;
  font-size: 14px;
  letter-spacing: 0.12em;
  margin-top: 4px;
}

/* Success banner */
.success-banner {
  animation: flash-in 0.3s ease;
}

@keyframes flash-in {
  from { opacity: 0; transform: translateY(-4px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ============================================================
   Recent runs panel
   ============================================================ */

.runs-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.col-runs {
  /* Sticky so runs panel stays visible while scrolling the form */
  position: sticky;
  top: 20px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
}

/* ============================================================
   HMM model selector / regime label guard
   ============================================================ */

.optional-tag {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 400;
  letter-spacing: 0.08em;
  color: var(--clr-text-dim);
  text-transform: lowercase;
  margin-left: 6px;
  border: 1px solid var(--clr-border);
  padding: 1px 5px;
}

.regime-warn {
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  color: var(--clr-yellow);
  border: 2px solid var(--clr-yellow);
  background: rgba(255, 230, 0, 0.06);
  padding: 8px 10px;
  box-shadow: 3px 3px 0 #000;
  margin-top: 4px;
}

.regime-warn code {
  font-family: var(--font-mono);
  background: rgba(255, 230, 0, 0.15);
  padding: 1px 4px;
  font-size: 11px;
}

/* ============================================================
   Date range availability caption
   ============================================================ */

.date-range-caption {
  margin-top: -6px;
  min-height: 18px;
}

.range-loading {
  font-size: 9px;
  color: var(--clr-text-dim);
  letter-spacing: 0.1em;
}

.range-available {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--clr-green, #00ff41);
  letter-spacing: 0.06em;
}

.range-no-data {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--clr-red);
  letter-spacing: 0.06em;
}
</style>
