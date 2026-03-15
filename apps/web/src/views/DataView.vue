<template>
  <div class="view-container">

    <!-- ===== Header ===== -->
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">DATA INGESTION</h1>
      </div>
      <span class="view-header__badge">MARKET DATA</span>
    </header>

    <!-- ===== Two-column body ===== -->
    <div class="view-body">

      <!-- ---- LEFT: Form + Job Status ---- -->
      <div class="data-col data-col--form">

        <NbCard title="INGEST BARS">

          <ErrorBanner v-if="store.submitError" :message="store.submitError" style="margin-bottom: 14px" />

          <!-- Job panel — shown while job is active or terminal -->
          <template v-if="store.activeJob">
            <JobStatusPanel :job="store.activeJob" title="JOB STATUS" :cancelling="cancelling" style="margin-bottom: 16px" @cancel="handleCancel">
              <!-- Success completion summary -->
              <template v-if="isJobSucceeded">
                <div class="completion-summary nb-panel" style="margin-top: 12px">
                  <div class="nb-label" style="margin-bottom: 10px; color: var(--clr-green)">
                    INGESTION COMPLETE
                  </div>
                  <div v-if="store.activeJob.result_ref" class="completion-detail font-mono">
                    {{ store.activeJob.result_ref }}
                  </div>
                  <div v-else class="completion-detail font-mono">
                    Job finished successfully.
                  </div>
                </div>
              </template>
            </JobStatusPanel>

            <!-- Dismiss / new run button -->
            <button
              v-if="isJobTerminal"
              class="nb-btn"
              style="margin-bottom: 20px; width: 100%"
              @click="handleNewRun"
            >
              NEW INGESTION RUN
            </button>
          </template>

          <!-- Form — only visible when no active job -->
          <form v-if="!isJobActive" class="ingestion-form" @submit.prevent="handleSubmit">

            <!-- ---- Instrument multi-select ---- -->
            <div class="form-field">
              <div class="form-label-row">
                <label class="form-label">INSTRUMENTS</label>
                <div class="form-label-actions">
                  <button type="button" class="link-btn" @click="selectAll">ALL</button>
                  <span class="text-dim">/</span>
                  <button type="button" class="link-btn" @click="selectNone">NONE</button>
                </div>
              </div>

              <div v-if="instrumentsLoading" class="field-loading">
                <span class="nb-label">LOADING INSTRUMENTS...</span>
              </div>
              <ErrorBanner v-else-if="instrumentsError" :message="instrumentsError" />
              <div v-else class="instrument-grid">
                <label
                  v-for="inst in instruments"
                  :key="inst.id"
                  :class="['instrument-checkbox', form.instruments.includes(inst.id) && 'instrument-checkbox--checked']"
                >
                  <input
                    type="checkbox"
                    :value="inst.id"
                    v-model="form.instruments"
                    class="instrument-checkbox__input"
                    :disabled="store.isSubmitting"
                  />
                  <span class="instrument-checkbox__label font-mono">
                    {{ inst.display_name ?? inst.id }}
                  </span>
                </label>
              </div>
              <span v-if="errors.instruments" class="field-error">{{ errors.instruments }}</span>
            </div>

            <!-- ---- Source selector ---- -->
            <div class="form-field">
              <label class="form-label">DATA SOURCE</label>
              <div class="source-group">
                <button
                  v-for="src in SOURCES"
                  :key="src.value"
                  type="button"
                  :class="['nb-btn', 'source-btn', form.source === src.value && 'source-btn--active']"
                  :disabled="store.isSubmitting"
                  @click="form.source = src.value"
                >
                  {{ src.label }}
                </button>
              </div>
              <span v-if="errors.source" class="field-error">{{ errors.source }}</span>
            </div>

            <!-- ---- Dukascopy mode selector (shown only when DUKASCOPY source is active) ---- -->
            <div v-if="form.source === 'dukascopy'" class="form-field">
              <label class="form-label">DUKASCOPY MODE</label>
              <div class="source-group">
                <button
                  v-for="mode in DUKASCOPY_MODES"
                  :key="mode.value"
                  type="button"
                  :class="['nb-btn', 'source-btn', form.dukascopy_mode === mode.value && 'source-btn--active']"
                  :disabled="store.isSubmitting"
                  @click="form.dukascopy_mode = mode.value"
                >
                  {{ mode.label }}
                </button>
              </div>
              <span class="dukascopy-mode-hint font-mono">{{ dukascopyModeHint }}</span>
            </div>

            <!-- ---- Date range ---- -->
            <div class="form-row">
              <div class="form-field">
                <label class="form-label">START DATE</label>
                <input
                  v-model="form.start_date"
                  type="date"
                  class="nb-input"
                  :disabled="store.isSubmitting"
                />
                <span v-if="errors.start_date" class="field-error">{{ errors.start_date }}</span>
              </div>
              <div class="form-field">
                <label class="form-label">END DATE</label>
                <input
                  v-model="form.end_date"
                  type="date"
                  class="nb-input"
                  :disabled="store.isSubmitting"
                />
                <span v-if="errors.end_date" class="field-error">{{ errors.end_date }}</span>
              </div>
            </div>

            <!-- ---- Submit ---- -->
            <button
              type="submit"
              class="nb-btn nb-btn--primary submit-btn"
              :disabled="store.isSubmitting || instrumentsLoading"
            >
              {{
                store.isSubmitting
                  ? 'SUBMITTING...'
                  : form.source === 'dukascopy' && form.dukascopy_mode === 'download'
                    ? 'DOWNLOAD + INGEST'
                    : 'START INGESTION'
              }}
            </button>

          </form>
        </NbCard>

      </div>

      <!-- ---- RIGHT: Recent jobs ---- -->
      <div class="data-col data-col--history">
        <NbCard title="RECENT JOBS">
          <template #header-right>
            <button
              class="nb-btn"
              style="font-size: 11px; padding: 4px 10px"
              :disabled="historyLoading"
              @click="loadHistory"
            >
              {{ historyLoading ? '...' : 'REFRESH' }}
            </button>
          </template>

          <LoadingState v-if="historyLoading && !jobHistory.length" message="LOADING HISTORY..." />

          <EmptyState
            v-else-if="!jobHistory.length"
            message="No ingestion jobs yet — run your first ingestion to see history here."
          />

          <div v-else class="job-history-list">
            <div
              v-for="job in jobHistory"
              :key="job.id"
              class="job-history-card nb-panel"
            >
              <div class="job-history-card__header">
                <span class="font-mono job-history-card__id text-dim">
                  {{ shortId(job.id) }}
                </span>
                <StatusBadge :status="job.status ?? 'unknown'" />
              </div>

              <div v-if="job.created_at" class="job-history-card__meta">
                <span class="nb-label">STARTED</span>
                <span class="font-mono" style="font-size: 11px">{{ formatDate(job.created_at) }}</span>
              </div>

              <div v-if="job.message || job.stage_label" class="job-history-card__message font-mono text-muted">
                {{ job.message ?? job.stage_label }}
              </div>

              <!-- Progress bar for in-flight historical jobs -->
              <div
                v-if="isInFlight(job.status)"
                class="nb-progress nb-progress--indeterminate"
                style="margin-top: 8px"
              >
                <div class="nb-progress__bar" />
              </div>
            </div>
          </div>
        </NbCard>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useIngestionStore } from '@/stores/ingestion.js'
import { useInstruments } from '@/composables/useInstruments.js'
import { useJobPoller } from '@/composables/useJobPoller.js'
import { listIngestionJobs } from '@/api/ingestion.js'
import { cancelJob } from '@/api/jobs.js'

import NbCard from '@/components/ui/NbCard.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import JobStatusPanel from '@/components/ui/JobStatusPanel.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SOURCES = [
  { value: 'oanda', label: 'OANDA' },
  { value: 'dukascopy', label: 'DUKASCOPY' },
]

// ---------------------------------------------------------------------------
// Store + composables
// ---------------------------------------------------------------------------

const store = useIngestionStore()
const { instruments, loading: instrumentsLoading, error: instrumentsError } = useInstruments()

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

const form = ref({
  instruments: [],
  source: 'oanda',
  dukascopy_mode: 'download',
  start_date: '',
  end_date: '',
})

// Reset dukascopy_mode to default whenever the user switches away from dukascopy
watch(
  () => form.value.source,
  (newSource) => {
    if (newSource !== 'dukascopy') {
      form.value.dukascopy_mode = 'download'
    }
  },
)

const DUKASCOPY_MODES = [
  { value: 'download', label: 'DOWNLOAD + INGEST' },
  { value: 'local', label: 'LOCAL FILES' },
]

const dukascopyModeHint = computed(() =>
  form.value.dukascopy_mode === 'download'
    ? 'Downloads CSVs via Node then ingests automatically'
    : 'Ingest from CSVs already on disk in the configured directory',
)

const errors = ref({})

// ---------------------------------------------------------------------------
// Recent jobs history (right panel)
// ---------------------------------------------------------------------------

const jobHistory = ref([])
const historyLoading = ref(false)

async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await listIngestionJobs()
    const raw = Array.isArray(data) ? data : (data?.jobs ?? [])
    // Sort most-recent first
    jobHistory.value = [...raw].sort((a, b) => {
      const ta = new Date(a.created_at ?? 0).getTime()
      const tb = new Date(b.created_at ?? 0).getTime()
      return tb - ta
    })
  } catch {
    // Non-fatal — history panel fails silently
  } finally {
    historyLoading.value = false
  }
}

// ---------------------------------------------------------------------------
// Derived state
// ---------------------------------------------------------------------------

const jobStatusLower = computed(() => store.activeJob?.status?.toLowerCase())

const isJobActive = computed(() =>
  ['queued', 'running'].includes(jobStatusLower.value)
)

const isJobTerminal = computed(() =>
  ['succeeded', 'failed', 'cancelled'].includes(jobStatusLower.value)
)

const isJobSucceeded = computed(() => jobStatusLower.value === 'succeeded')

// ---------------------------------------------------------------------------
// Cancel
// ---------------------------------------------------------------------------

const cancelling = ref(false)

async function handleCancel() {
  if (!store.activeJobId || cancelling.value) return
  cancelling.value = true
  try {
    const updated = await cancelJob(store.activeJobId)
    store.updateJobState(updated)
    stopPolling()
  } catch {
    // Non-fatal — job may have finished between click and request
  } finally {
    cancelling.value = false
  }
}

// ---------------------------------------------------------------------------
// Instrument selection helpers
// ---------------------------------------------------------------------------

function selectAll() {
  form.value.instruments = instruments.value.map((i) => i.id)
}

function selectNone() {
  form.value.instruments = []
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function validate() {
  const e = {}

  if (!form.value.instruments.length) {
    e.instruments = 'Select at least one instrument.'
  }
  if (!form.value.source) {
    e.source = 'Select a data source.'
  }
  if (!form.value.start_date) {
    e.start_date = 'Start date is required.'
  }
  if (!form.value.end_date) {
    e.end_date = 'End date is required.'
  }
  if (form.value.start_date && form.value.end_date && form.value.start_date >= form.value.end_date) {
    e.end_date = 'End date must be after start date.'
  }

  errors.value = e
  return Object.keys(e).length === 0
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

let pollerCleanup = null

function startPolling(jobId) {
  stopPolling()
  const { stop } = useJobPoller(jobId, {
    onProgress: store.updateJobState,
    onComplete: (job) => {
      store.onJobComplete(job)
      loadHistory()
    },
    onError: (job) => {
      store.onJobError(job)
      loadHistory()
    },
  })
  pollerCleanup = stop
}

function stopPolling() {
  if (pollerCleanup) {
    pollerCleanup()
    pollerCleanup = null
  }
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------

async function handleSubmit() {
  if (!validate()) return

  try {
    let jobId

    if (form.value.source === 'dukascopy' && form.value.dukascopy_mode === 'download') {
      const payload = {
        instruments: form.value.instruments,
        start_date: form.value.start_date,
        end_date: form.value.end_date,
      }
      jobId = await store.submitDukascopyDownloadJob(payload)
    } else {
      const payload = {
        instruments: form.value.instruments,
        source: form.value.source,
        start_date: form.value.start_date,
        end_date: form.value.end_date,
      }
      jobId = await store.submitJob(payload)
    }

    startPolling(jobId)
  } catch {
    // submitError is set inside the store action; no extra handling needed here
  }
}

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

function handleNewRun() {
  stopPolling()
  store.resetJob()
}

// ---------------------------------------------------------------------------
// Utility formatters
// ---------------------------------------------------------------------------

function shortId(id) {
  if (!id) return '—'
  return id.length > 14 ? `${id.slice(0, 7)}…${id.slice(-5)}` : id
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString('en-GB', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function isInFlight(status) {
  return ['queued', 'running'].includes((status ?? '').toLowerCase())
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  loadHistory()
  // If there's already an active job in the store from a previous session,
  // resume polling so the panel updates
  if (store.activeJobId && isJobActive.value) {
    startPolling(store.activeJobId)
  }
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
  display: grid;
  grid-template-columns: 400px 1fr;
  gap: 20px;
  align-items: start;
}

@media (max-width: 900px) {
  .view-body {
    grid-template-columns: 1fr;
  }
}

.data-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Right column: sticky so history stays visible */
.data-col--history {
  position: sticky;
  top: 20px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
}

/* ============================================================
   Form
   ============================================================ */

.ingestion-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--clr-text-muted);
}

.form-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.form-label-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.link-btn {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  color: var(--clr-yellow);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.link-btn:hover {
  color: #fff;
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

/* ---- Instrument checkbox grid ---- */

.instrument-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding: 10px;
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb-sm);
}

.instrument-checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  cursor: pointer;
  border: 2px solid transparent;
  transition: border-color 0.08s;
  user-select: none;
}

.instrument-checkbox:hover {
  border-color: var(--clr-border);
  background: var(--clr-surface);
}

.instrument-checkbox--checked {
  border-color: var(--clr-yellow);
  background: color-mix(in srgb, var(--clr-yellow) 8%, transparent);
}

.instrument-checkbox__input {
  appearance: none;
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border: 2px solid var(--clr-border);
  background: var(--clr-bg);
  cursor: pointer;
  flex-shrink: 0;
  position: relative;
}

.instrument-checkbox__input:checked {
  background: var(--clr-yellow);
  border-color: var(--clr-yellow);
}

.instrument-checkbox__input:checked::after {
  content: '';
  position: absolute;
  top: 1px;
  left: 3px;
  width: 4px;
  height: 7px;
  border: 2px solid #000;
  border-top: none;
  border-left: none;
  transform: rotate(45deg);
}

.instrument-checkbox__label {
  font-size: 12px;
  color: var(--clr-text);
  letter-spacing: 0.04em;
}

/* ---- Source selector ---- */

.source-group {
  display: flex;
  gap: 8px;
}

.source-btn {
  flex: 1;
  padding: 8px 0;
  font-size: 12px;
  letter-spacing: 0.1em;
  text-align: center;
  justify-content: center;
}

.source-btn--active {
  background: var(--clr-yellow);
  border-color: var(--clr-yellow);
  color: #000;
  box-shadow: var(--shadow-nb-yellow);
}

/* ---- Dukascopy mode hint ---- */

.dukascopy-mode-hint {
  font-size: 11px;
  color: var(--clr-text-dim);
  letter-spacing: 0.03em;
  line-height: 1.4;
}

/* ---- Date inputs ---- */

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

.nb-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ---- Submit ---- */

.submit-btn {
  width: 100%;
  padding: 14px;
  font-size: 14px;
  letter-spacing: 0.12em;
  margin-top: 4px;
}

/* ---- Completion summary ---- */

.completion-summary {
  font-size: 12px;
}

.completion-detail {
  font-size: 12px;
  color: var(--clr-text-muted);
  word-break: break-all;
}

/* ============================================================
   Job history panel (right column)
   ============================================================ */

.job-history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.job-history-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.job-history-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.job-history-card__id {
  font-size: 11px;
  letter-spacing: 0.04em;
}

.job-history-card__meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.job-history-card__message {
  font-size: 11px;
  color: var(--clr-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
