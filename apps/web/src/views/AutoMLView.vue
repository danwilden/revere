<template>
  <div class="view-container">

    <!-- ===== Header ===== -->
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">AUTOML SIGNAL MINING</h1>
      </div>
      <span class="view-header__badge">FEATURE-DRIVEN MODEL SEARCH</span>
    </header>

    <!-- ===== Body ===== -->
    <div class="view-body">

      <!-- ---- SECTION 1: Launch Form ---- -->
      <NbCard title="LAUNCH CONFIGURATION">

        <ErrorBanner
          v-if="store.submitError"
          :message="store.submitError"
          style="margin-bottom: 16px"
        />

        <form class="launch-form" @submit.prevent="handleSubmit">

          <!-- Row 1: Instrument + Timeframe -->
          <div class="form-row">
            <div class="form-field">
              <label class="form-label">INSTRUMENT ID</label>
              <input
                v-model="form.instrument_id"
                class="nb-text-input"
                :class="{ 'nb-text-input--error': errors.instrument_id }"
                type="text"
                placeholder="EUR_USD"
                :disabled="store.isSubmitting"
                autocomplete="off"
                spellcheck="false"
              />
              <span v-if="errors.instrument_id" class="form-error">{{ errors.instrument_id }}</span>
            </div>

            <div class="form-field">
              <label class="form-label">TIMEFRAME</label>
              <div class="nb-select-wrapper">
                <select
                  v-model="form.timeframe"
                  class="nb-native-select"
                  :class="{ 'nb-native-select--error': errors.timeframe }"
                  :disabled="store.isSubmitting"
                >
                  <option v-for="tf in TIMEFRAMES" :key="tf" :value="tf">{{ tf }}</option>
                </select>
                <span class="nb-select-arrow">▼</span>
              </div>
              <span v-if="errors.timeframe" class="form-error">{{ errors.timeframe }}</span>
            </div>
          </div>

          <!-- Row 2: Feature Run ID + Model ID -->
          <div class="form-row">
            <div class="form-field">
              <label class="form-label">FEATURE RUN ID <span class="form-label-required">*</span></label>
              <input
                v-model="form.feature_run_id"
                class="nb-text-input font-mono"
                :class="{ 'nb-text-input--error': errors.feature_run_id }"
                type="text"
                placeholder="feature_run_id from pipeline"
                :disabled="store.isSubmitting"
                autocomplete="off"
                spellcheck="false"
              />
              <span v-if="errors.feature_run_id" class="form-error">{{ errors.feature_run_id }}</span>
            </div>

            <div class="form-field">
              <label class="form-label">MODEL ID <span class="form-label-required">*</span></label>
              <input
                v-model="form.model_id"
                class="nb-text-input font-mono"
                :class="{ 'nb-text-input--error': errors.model_id }"
                type="text"
                placeholder="HMM model ID"
                :disabled="store.isSubmitting"
                autocomplete="off"
                spellcheck="false"
              />
              <span v-if="errors.model_id" class="form-error">{{ errors.model_id }}</span>
            </div>
          </div>

          <!-- Row 3: Training / test end dates -->
          <div class="form-row">
            <div class="form-field">
              <label class="form-label">TRAIN END DATE <span class="form-label-required">*</span></label>
              <input
                v-model="form.train_end_date"
                class="nb-text-input font-mono"
                :class="{ 'nb-text-input--error': errors.train_end_date }"
                type="date"
                :disabled="store.isSubmitting"
              />
              <span v-if="errors.train_end_date" class="form-error">{{ errors.train_end_date }}</span>
            </div>

            <div class="form-field">
              <label class="form-label">TEST END DATE <span class="form-label-required">*</span></label>
              <input
                v-model="form.test_end_date"
                class="nb-text-input font-mono"
                :class="{ 'nb-text-input--error': errors.test_end_date }"
                type="date"
                :disabled="store.isSubmitting"
              />
              <span v-if="errors.test_end_date" class="form-error">{{ errors.test_end_date }}</span>
            </div>
          </div>

          <!-- Target Type radio -->
          <div class="form-field">
            <label class="form-label">TARGET TYPE</label>
            <div class="target-type-group">
              <label
                v-for="opt in TARGET_TYPES"
                :key="opt.value"
                class="target-type-option"
                :class="{ 'target-type-option--selected': form.target_type === opt.value }"
              >
                <input
                  v-model="form.target_type"
                  type="radio"
                  :value="opt.value"
                  :disabled="store.isSubmitting"
                  class="target-type-radio"
                />
                <span class="target-type-label">{{ opt.label }}</span>
                <span class="target-type-desc">{{ opt.desc }}</span>
              </label>
            </div>
            <span v-if="errors.target_type" class="form-error">{{ errors.target_type }}</span>
          </div>

          <!-- Submit -->
          <button
            type="submit"
            class="nb-btn nb-btn--primary form-submit"
            :disabled="store.isSubmitting"
          >
            {{ store.isSubmitting ? 'LAUNCHING...' : 'LAUNCH AUTOML JOB' }}
          </button>

        </form>
      </NbCard>

      <!-- ---- SECTION 2: Job Status Panel ---- -->
      <NbCard
        v-if="store.activeJobId"
        title="JOB STATUS"
        :accent="jobCardAccent"
        class="status-card"
      >
        <div class="job-status-grid">

          <!-- Job ID -->
          <div class="job-meta-row">
            <span class="nb-label">JOB ID</span>
            <span class="nb-value nb-value--sm font-mono job-id-value">
              {{ store.activeJobStatus?.job_run?.id ?? store.activeJobId }}
            </span>
          </div>

          <!-- Job status chip + automl record status -->
          <div class="job-meta-row">
            <span class="nb-label">JOB STATUS</span>
            <StatusBadge
              v-if="store.activeJobStatus?.job_run?.status"
              :status="store.activeJobStatus.job_run.status"
            />
            <span v-else class="nb-value nb-value--sm text-dim font-mono">—</span>
          </div>

          <div class="job-meta-row">
            <span class="nb-label">AUTOML STATUS</span>
            <span
              class="nb-value nb-value--sm font-mono uppercase"
              :class="automlRecordStatusClass"
            >
              {{ store.activeJobStatus?.automl_record?.status ?? '—' }}
            </span>
          </div>

          <!-- Target type -->
          <div class="job-meta-row">
            <span class="nb-label">TARGET TYPE</span>
            <span class="nb-value nb-value--sm font-mono">
              {{ store.activeJobStatus?.automl_record?.target_type ?? form.target_type ?? '—' }}
            </span>
          </div>

          <!-- Progress bar -->
          <div class="job-progress-row">
            <div class="job-progress-header">
              <span class="nb-label">PROGRESS</span>
              <span class="nb-value nb-value--sm font-mono text-yellow">
                {{ progressDisplay }}
              </span>
            </div>
            <div
              v-if="isJobInFlight"
              :class="[
                'nb-progress',
                progressPct === 0 ? 'nb-progress--indeterminate' : ''
              ]"
            >
              <div
                v-if="progressPct > 0"
                class="nb-progress__bar"
                :style="{ width: progressPct + '%' }"
              />
            </div>
            <div v-else-if="isJobSucceeded" class="nb-progress">
              <div class="nb-progress__bar nb-progress__bar--full" />
            </div>
          </div>

          <!-- Error message -->
          <ErrorBanner
            v-if="store.activeJobStatus?.job_run?.error_message"
            :message="store.activeJobStatus.job_run.error_message"
            style="margin-top: 4px"
          />

          <!-- Success message -->
          <div
            v-if="isJobSucceeded && !store.candidates.length && !store.isLoadingCandidates"
            class="nb-banner nb-banner--info status-success-msg"
          >
            <span class="font-mono uppercase" style="font-size: 11px">
              JOB SUCCEEDED — FETCHING CANDIDATES...
            </span>
          </div>

        </div>

        <!-- Actions row -->
        <div class="status-actions">
          <button class="nb-btn" style="font-size: 11px; padding: 5px 14px" @click="handleReset">
            START NEW JOB
          </button>
        </div>
      </NbCard>

      <!-- ---- SECTION 3: Candidates Table ---- -->
      <NbCard
        v-if="store.candidates.length > 0 || store.isLoadingCandidates"
        title="CANDIDATE MODELS"
        :accent="'yellow'"
      >

        <div v-if="store.isLoadingCandidates" class="candidates-loading">
          <div class="nb-progress nb-progress--indeterminate" style="margin-bottom: 10px" />
          <span class="nb-label text-dim">FETCHING CANDIDATES...</span>
        </div>

        <template v-else>
          <div class="table-scroll">
            <table class="nb-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>MODEL NAME</th>
                  <th>OBJECTIVE METRIC</th>
                  <th>METRIC VALUE</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(candidate, idx) in store.candidates" :key="candidate.name ?? idx">
                  <td class="font-mono text-muted">{{ idx + 1 }}</td>
                  <td class="font-mono candidate-name">{{ candidate.name ?? '—' }}</td>
                  <td class="font-mono text-muted uppercase" style="font-size: 11px">
                    {{ candidate.objective_metric_name ?? '—' }}
                  </td>
                  <td>
                    <span class="nb-value nb-value--accent font-mono">
                      {{ formatMetricValue(candidate.objective_metric_value) }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Convert section -->
          <div class="convert-section">
            <div class="convert-section__header">
              <span class="nb-heading nb-heading--sm">ACCEPT &amp; CONVERT TO SIGNAL</span>
            </div>

            <!-- Evaluation not accepted warning -->
            <div
              v-if="evaluationBlocked"
              class="nb-banner nb-banner--warning"
              style="margin-bottom: 12px"
            >
              <span class="font-mono uppercase" style="font-size: 11px">
                EVALUATION NOT ACCEPTED — CONVERSION BLOCKED
              </span>
            </div>

            <ErrorBanner
              v-if="store.error"
              :message="store.error"
              style="margin-bottom: 12px"
            />

            <div class="convert-form-row">
              <div class="convert-input-group">
                <label class="form-label">SIGNAL NAME</label>
                <input
                  v-model="signalName"
                  class="nb-text-input font-mono"
                  type="text"
                  placeholder="auto-generated if blank"
                  :disabled="store.isConverting || evaluationBlocked"
                  autocomplete="off"
                  spellcheck="false"
                />
              </div>
              <button
                class="nb-btn nb-btn--primary convert-btn"
                :disabled="store.isConverting || evaluationBlocked"
                @click="handleConvert"
              >
                {{ store.isConverting ? 'CONVERTING...' : 'CONVERT TO SIGNAL' }}
              </button>
            </div>

          </div>
        </template>
      </NbCard>

      <!-- ---- SECTION 4: Converted Signal ---- -->
      <NbCard
        v-if="store.convertedSignal"
        title="SIGNAL CREATED"
        accent="green"
        class="signal-card"
      >
        <div class="nb-banner nb-banner--info signal-success-banner">
          <span class="font-mono uppercase" style="font-size: 11px">
            SIGNAL CREATED — AVAILABLE IN SIGNAL BANK
          </span>
        </div>

        <div class="signal-meta-grid">
          <div class="signal-meta-row">
            <span class="nb-label">SIGNAL ID</span>
            <span class="nb-value nb-value--sm font-mono signal-id">
              {{ store.convertedSignal.id }}
            </span>
          </div>
          <div class="signal-meta-row">
            <span class="nb-label">NAME</span>
            <span class="nb-value font-mono">{{ store.convertedSignal.name }}</span>
          </div>
          <div class="signal-meta-row">
            <span class="nb-label">TYPE</span>
            <span class="signal-type-chip font-mono uppercase">
              {{ store.convertedSignal.signal_type ?? store.convertedSignal.type ?? '—' }}
            </span>
          </div>
          <div v-if="store.convertedSignal.description" class="signal-meta-row signal-meta-row--full">
            <span class="nb-label">DESCRIPTION</span>
            <span class="nb-value nb-value--sm text-muted" style="word-break: break-word">
              {{ store.convertedSignal.description }}
            </span>
          </div>
        </div>
      </NbCard>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useAutoMLStore } from '@/stores/useAutoMLStore.js'
import { useJobPoller } from '@/composables/useJobPoller.js'
import NbCard from '@/components/ui/NbCard.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

const store = useAutoMLStore()

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TIMEFRAMES = ['M1', 'H1', 'H4', 'D']

const TARGET_TYPES = [
  {
    value: 'direction_probability',
    label: 'DIRECTION PROBABILITY',
    desc: 'Predict up/down probability',
  },
  {
    value: 'return_bucket',
    label: 'RETURN BUCKET',
    desc: 'Classify return magnitude',
  },
]

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

const form = ref({
  instrument_id: '',
  timeframe: 'H1',
  feature_run_id: '',
  model_id: '',
  train_end_date: '',
  test_end_date: '',
  target_type: 'direction_probability',
})

const errors = ref({})
const signalName = ref('')

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function validate() {
  const e = {}
  if (!form.value.instrument_id?.trim()) {
    e.instrument_id = 'INSTRUMENT ID IS REQUIRED'
  }
  if (!form.value.timeframe) {
    e.timeframe = 'SELECT A TIMEFRAME'
  }
  if (!form.value.feature_run_id?.trim()) {
    e.feature_run_id = 'FEATURE RUN ID IS REQUIRED'
  }
  if (!form.value.model_id?.trim()) {
    e.model_id = 'MODEL ID IS REQUIRED'
  }
  if (!form.value.train_end_date) {
    e.train_end_date = 'TRAIN END DATE IS REQUIRED'
  }
  if (!form.value.test_end_date) {
    e.test_end_date = 'TEST END DATE IS REQUIRED'
  }
  if (!form.value.target_type) {
    e.target_type = 'SELECT A TARGET TYPE'
  }
  errors.value = e
  return Object.keys(e).length === 0
}

// ---------------------------------------------------------------------------
// Derived state
// ---------------------------------------------------------------------------

const jobRunStatus = computed(() =>
  store.activeJobStatus?.job_run?.status?.toLowerCase() ?? null
)

const isJobInFlight = computed(() =>
  ['queued', 'running'].includes(jobRunStatus.value)
)

const isJobSucceeded = computed(() => jobRunStatus.value === 'succeeded')

const isJobTerminal = computed(() =>
  ['succeeded', 'failed', 'cancelled'].includes(jobRunStatus.value)
)

const progressPct = computed(() =>
  store.activeJobStatus?.job_run?.progress_pct ?? 0
)

const progressDisplay = computed(() => {
  if (!jobRunStatus.value) return '—'
  if (isJobTerminal.value) return isJobSucceeded.value ? '100%' : 'N/A'
  if (progressPct.value > 0) return `${Math.round(progressPct.value)}%`
  return 'PENDING'
})

const jobCardAccent = computed(() => {
  if (jobRunStatus.value === 'succeeded') return 'green'
  if (['failed', 'cancelled'].includes(jobRunStatus.value)) return 'red'
  if (jobRunStatus.value === 'running') return 'yellow'
  return null
})

const automlRecordStatusClass = computed(() => {
  const s = store.activeJobStatus?.automl_record?.status?.toLowerCase()
  if (s === 'completed') return 'text-green'
  if (s === 'failed') return 'text-red'
  if (s === 'running') return 'text-yellow'
  return 'text-muted'
})

const evaluationBlocked = computed(() => {
  const record = store.activeJobStatus?.automl_record
  if (!record) return true
  // Block if evaluation explicitly has accept: false, or if evaluation is absent
  if (!record.evaluation) return false // no evaluation yet — don't block prematurely
  return record.evaluation.accept !== true
})

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

let stopPoller = null

function startPolling(jobId) {
  if (stopPoller) stopPoller()

  const { stop } = useJobPoller(jobId, {
    intervalMs: 2500,

    onProgress: (job) => {
      // useJobPoller gives us the generic job from GET /api/jobs/{id}
      // We also need the AutoML-specific envelope (automl_record)
      // so we call store.pollJob to get the full AutoMLJobStatusResponse
      if (!job._pollError) {
        store.pollJob(jobId)
      }
    },

    onComplete: (_job) => {
      // Refresh AutoML status one final time to capture automl_record.status
      store.pollJob(jobId).then(() => {
        // Auto-fetch candidates once the job reports succeeded
        store.fetchCandidates(jobId)
      })
    },

    onError: (_job) => {
      store.pollJob(jobId)
    },
  })

  stopPoller = stop
}

// ---------------------------------------------------------------------------
// Submit handler
// ---------------------------------------------------------------------------

async function handleSubmit() {
  if (!validate()) return

  const payload = {
    instrument_id: form.value.instrument_id.trim(),
    timeframe: form.value.timeframe,
    feature_run_id: form.value.feature_run_id.trim(),
    model_id: form.value.model_id.trim(),
    train_end_date: form.value.train_end_date,
    test_end_date: form.value.test_end_date,
    target_type: form.value.target_type,
  }

  try {
    const jobId = await store.submitJob(payload)
    startPolling(jobId)
  } catch (_err) {
    // error already set in store.submitError — nothing to do here
  }
}

// ---------------------------------------------------------------------------
// Convert handler
// ---------------------------------------------------------------------------

async function handleConvert() {
  if (!store.activeJobId) return
  try {
    await store.convertJobToSignal(store.activeJobId, signalName.value.trim() || undefined)
    signalName.value = ''
  } catch (_err) {
    // error already set in store.error
  }
}

// ---------------------------------------------------------------------------
// Reset handler
// ---------------------------------------------------------------------------

function handleReset() {
  if (stopPoller) {
    stopPoller()
    stopPoller = null
  }
  store.resetJob()
  signalName.value = ''
  errors.value = {}
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onUnmounted(() => {
  if (stopPoller) stopPoller()
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatMetricValue(val) {
  if (val === null || val === undefined) return '—'
  if (typeof val === 'number') return val.toFixed(4)
  return String(val)
}
</script>

<style scoped>
/* ===== Layout ===== */

.view-container {
  padding: 28px 32px;
  min-height: 100%;
}

.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 28px;
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

.view-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 960px;
}

/* ===== Form ===== */

.launch-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.form-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--clr-text-muted);
}

.form-label-required {
  color: var(--clr-red);
}

.form-label-optional {
  color: var(--clr-text-dim);
  font-size: 9px;
}

.form-error {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--clr-red);
  letter-spacing: 0.05em;
}

.nb-text-input {
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  color: var(--clr-text);
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 8px 12px;
  outline: none;
  width: 100%;
  box-sizing: border-box;
  transition: border-color 0.1s;
}

.nb-text-input:focus {
  border-color: var(--clr-yellow);
}

.nb-text-input:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.nb-text-input--error {
  border-color: var(--clr-red) !important;
}

.nb-select-wrapper {
  position: relative;
}

.nb-native-select {
  -webkit-appearance: none;
  appearance: none;
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  color: var(--clr-text);
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 8px 36px 8px 12px;
  outline: none;
  width: 100%;
  cursor: pointer;
}

.nb-native-select:focus {
  border-color: var(--clr-yellow);
}

.nb-native-select:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.nb-native-select--error {
  border-color: var(--clr-red) !important;
}

.nb-select-arrow {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 9px;
  color: var(--clr-text-muted);
  pointer-events: none;
}

/* Target type radio group */

.target-type-group {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.target-type-option {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 14px;
  border: 2px solid var(--clr-border);
  background: var(--clr-panel);
  cursor: pointer;
  transition: border-color 0.1s, box-shadow 0.1s;
}

.target-type-option:hover {
  border-color: var(--clr-border-bright);
}

.target-type-option--selected {
  border-color: var(--clr-yellow);
  box-shadow: var(--shadow-nb-yellow);
  background: var(--clr-panel-alt);
}

.target-type-radio {
  display: none;
}

.target-type-label {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--clr-text);
  text-transform: uppercase;
}

.target-type-option--selected .target-type-label {
  color: var(--clr-yellow);
}

.target-type-desc {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--clr-text-dim);
  text-transform: lowercase;
}

.form-submit {
  align-self: flex-start;
  font-size: 13px;
  padding: 11px 28px;
  margin-top: 2px;
}

/* ===== Job Status Card ===== */

.status-card {
  /* inherits nb-card */
}

.job-status-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.job-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--clr-border);
  gap: 12px;
}

.job-meta-row:last-child {
  border-bottom: none;
}

.job-id-value {
  font-size: 11px;
  color: var(--clr-text-muted);
  letter-spacing: 0.04em;
  word-break: break-all;
  text-align: right;
  max-width: 520px;
}

.job-progress-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 0;
  border-bottom: 1px solid var(--clr-border);
}

.job-progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.nb-progress__bar--full {
  width: 100%;
  background: var(--clr-green);
}

.status-success-msg {
  margin-top: 6px;
}

.status-actions {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--clr-border);
  display: flex;
  gap: 10px;
}

/* ===== Candidates Table ===== */

.candidates-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 24px 0;
}

.table-scroll {
  overflow-x: auto;
  margin-bottom: 20px;
}

.candidate-name {
  font-size: 12px;
  color: var(--clr-text);
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== Convert section ===== */

.convert-section {
  border-top: 2px solid var(--clr-border);
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.convert-section__header {
  margin-bottom: 4px;
}

.convert-form-row {
  display: flex;
  align-items: flex-end;
  gap: 14px;
}

.convert-input-group {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.convert-btn {
  padding: 9px 22px;
  font-size: 12px;
  white-space: nowrap;
  flex-shrink: 0;
}

/* ===== Converted Signal Card ===== */

.signal-card {
  /* nb-card--success applied via accent="green" prop */
}

.signal-success-banner {
  margin-bottom: 16px;
}

.signal-meta-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.signal-meta-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--clr-border);
  gap: 16px;
}

.signal-meta-row--full {
  flex-direction: column;
  gap: 4px;
}

.signal-meta-row:last-child {
  border-bottom: none;
}

.signal-id {
  font-size: 11px;
  color: var(--clr-text-muted);
  word-break: break-all;
  text-align: right;
}

.signal-type-chip {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--clr-green);
  border: 1px solid var(--clr-green);
  padding: 2px 8px;
  background: transparent;
}
</style>
