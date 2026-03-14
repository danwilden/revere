<template>
  <div class="view-container">
    <!-- ===== Header ===== -->
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">HMM MODELS</h1>
      </div>
      <span class="view-header__badge">REGIME DETECTION</span>
    </header>

    <!-- ===== Body: two-column layout ===== -->
    <div class="view-body">

      <!-- ---- LEFT: Training Form ---- -->
      <div class="models-col models-col--form">
        <NbCard title="TRAIN NEW MODEL">
          <template #header-right>
            <span v-if="store.loadingInstruments" class="nb-label text-dim">LOADING...</span>
          </template>

          <ErrorBanner v-if="store.fetchError" :message="store.fetchError" style="margin-bottom: 14px" />

          <form class="training-form" @submit.prevent="handleSubmit">

            <!-- Instrument -->
            <div class="form-field">
              <label class="form-label">INSTRUMENT</label>
              <v-select
                v-model="form.instrument"
                :items="instrumentItems"
                item-title="label"
                item-value="value"
                variant="outlined"
                density="compact"
                :loading="store.loadingInstruments"
                :disabled="isJobInFlight"
                placeholder="Select instrument..."
                class="nb-select"
              />
              <span v-if="errors.instrument" class="form-error">{{ errors.instrument }}</span>
            </div>

            <!-- Timeframe -->
            <div class="form-field">
              <label class="form-label">TIMEFRAME</label>
              <v-select
                v-model="form.timeframe"
                :items="TIMEFRAMES"
                variant="outlined"
                density="compact"
                :disabled="isJobInFlight"
                class="nb-select"
              />
              <span v-if="errors.timeframe" class="form-error">{{ errors.timeframe }}</span>
            </div>

            <!-- Date range row -->
            <div class="form-row">
              <div class="form-field form-field--half">
                <label class="form-label">TRAIN START</label>
                <v-text-field
                  v-model="form.train_start"
                  variant="outlined"
                  density="compact"
                  placeholder="YYYY-MM-DD"
                  :disabled="isJobInFlight"
                  class="nb-input"
                />
                <span v-if="errors.train_start" class="form-error">{{ errors.train_start }}</span>
              </div>
              <div class="form-field form-field--half">
                <label class="form-label">TRAIN END</label>
                <v-text-field
                  v-model="form.train_end"
                  variant="outlined"
                  density="compact"
                  placeholder="YYYY-MM-DD"
                  :disabled="isJobInFlight"
                  class="nb-input"
                />
                <span v-if="errors.train_end" class="form-error">{{ errors.train_end }}</span>
              </div>
            </div>

            <!-- N States slider -->
            <div class="form-field">
              <div class="form-label-row">
                <label class="form-label">N STATES</label>
                <span class="nb-value nb-value--accent font-mono">{{ form.n_states }}</span>
              </div>
              <input
                v-model.number="form.n_states"
                type="range"
                min="2"
                max="8"
                step="1"
                :disabled="isJobInFlight"
                class="nb-slider"
              />
              <div class="nb-slider__ticks">
                <span v-for="n in 7" :key="n" class="nb-slider__tick nb-label">{{ n + 1 }}</span>
              </div>
            </div>

            <!-- Feature set (read-only) -->
            <div class="form-field">
              <label class="form-label">FEATURE SET</label>
              <div class="form-readonly">
                <span class="nb-value nb-value--sm font-mono text-muted">default_v1</span>
                <span class="nb-label" style="font-size: 10px">(FIXED — MVP)</span>
              </div>
            </div>

            <!-- Submit -->
            <button
              type="submit"
              class="nb-btn nb-btn--primary form-submit"
              :disabled="isJobInFlight"
            >
              {{ isJobInFlight ? 'TRAINING...' : 'TRAIN MODEL' }}
            </button>

            <ErrorBanner v-if="submitError" :message="submitError" style="margin-top: 10px" />
          </form>
        </NbCard>

        <!-- Job status panel — shown while a job is active -->
        <div v-if="store.activeJob" class="models-job-panel">
          <NbCard
            title="JOB STATUS"
            :accent="jobAccent"
          >
            <TrainingProgress :job="store.activeJob" />

            <!-- Clear button after terminal state -->
            <div
              v-if="isJobTerminal"
              style="margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--clr-border)"
            >
              <button class="nb-btn" style="font-size: 11px" @click="store.clearJob()">
                DISMISS
              </button>
            </div>
          </NbCard>
        </div>
      </div>

      <!-- ---- RIGHT: Saved Models ---- -->
      <div class="models-col models-col--list">
        <NbCard title="SAVED MODELS">
          <template #header-right>
            <button
              class="nb-btn"
              style="font-size: 11px; padding: 4px 12px"
              :disabled="store.loadingModels"
              @click="store.fetchModels()"
            >
              {{ store.loadingModels ? '...' : 'REFRESH' }}
            </button>
          </template>

          <LoadingState v-if="store.loadingModels && !store.hmmModels.length" message="LOADING MODELS..." />

          <EmptyState
            v-else-if="!store.hmmModels.length"
            message="No trained models yet — submit a training job to begin"
          />

          <div v-else class="models-list">
            <ModelCard
              v-for="model in sortedModels"
              :key="model.id"
              :model="model"
            />
          </div>
        </NbCard>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useModelsStore } from '@/stores/models.js'
import { useJobPoller } from '@/composables/useJobPoller.js'
import NbCard from '@/components/ui/NbCard.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import ModelCard from '@/components/models/ModelCard.vue'
import TrainingProgress from '@/components/models/TrainingProgress.vue'

// ---------------------------------------------------------------------------
// Store + constants
// ---------------------------------------------------------------------------

const store = useModelsStore()

const TIMEFRAMES = ['M1', 'H1', 'H4', 'D']

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

const form = ref({
  instrument: null,
  timeframe: 'H1',
  train_start: '',
  train_end: '',
  n_states: 7,
})

const errors = ref({})
const submitError = ref(null)

// ---------------------------------------------------------------------------
// Derived
// ---------------------------------------------------------------------------

const instrumentItems = computed(() =>
  store.instruments.map((inst) => ({
    label: inst.display_name ?? inst.id,
    value: inst.id,
  }))
)

const sortedModels = computed(() =>
  [...store.hmmModels].sort((a, b) => {
    const ta = a.created_at ?? ''
    const tb = b.created_at ?? ''
    return tb.localeCompare(ta)
  })
)

const jobStatus = computed(() => store.activeJob?.status?.toLowerCase())
const isJobInFlight = computed(() =>
  ['queued', 'running'].includes(jobStatus.value)
)
const isJobTerminal = computed(() =>
  ['succeeded', 'failed', 'cancelled'].includes(jobStatus.value)
)

const jobAccent = computed(() => {
  if (jobStatus.value === 'succeeded') return 'green'
  if (['failed', 'cancelled'].includes(jobStatus.value)) return 'red'
  if (jobStatus.value === 'running') return 'yellow'
  return null
})

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

function validate() {
  const e = {}
  if (!form.value.instrument) e.instrument = 'Select an instrument'
  if (!form.value.timeframe) e.timeframe = 'Select a timeframe'
  if (!form.value.train_start || !DATE_RE.test(form.value.train_start)) {
    e.train_start = 'Enter a valid date (YYYY-MM-DD)'
  }
  if (!form.value.train_end || !DATE_RE.test(form.value.train_end)) {
    e.train_end = 'Enter a valid date (YYYY-MM-DD)'
  }
  if (
    form.value.train_start &&
    form.value.train_end &&
    form.value.train_start >= form.value.train_end
  ) {
    e.train_end = 'End date must be after start date'
  }
  errors.value = e
  return Object.keys(e).length === 0
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

let stopPoller = null

function startPolling(jobId) {
  if (stopPoller) stopPoller()
  const { stop } = useJobPoller(jobId, {
    onProgress: (job) => store.updateJobState(job),
    onComplete: (job) => {
      store.updateJobState(job)
      store.trainedModelId = job.result_ref ?? null
      store.fetchModels()
    },
    onError: (job) => {
      store.updateJobState(job)
    },
  })
  stopPoller = stop
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------

async function handleSubmit() {
  submitError.value = null
  if (!validate()) return

  try {
    const payload = {
      instrument: form.value.instrument,
      timeframe: form.value.timeframe,
      train_start: `${form.value.train_start}T00:00:00`,
      train_end: `${form.value.train_end}T00:00:00`,
      num_states: form.value.n_states,
      feature_set_name: 'default_v1',
    }

    const jobId = await store.submitTrainingJob(payload)
    startPolling(jobId)
  } catch (err) {
    submitError.value =
      err?.normalized?.message ??
      err?.response?.data?.detail ??
      err?.message ??
      'Failed to submit training job'
  }
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  store.fetchInstruments()
  store.fetchModels()
})

onUnmounted(() => {
  if (stopPoller) stopPoller()
})
</script>

<style scoped>
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

/* ===== Body layout ===== */

.view-body {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 20px;
  align-items: start;
}

.models-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.models-job-panel {
  /* sits below the training form card */
}

/* ===== Training form ===== */

.training-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
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

.form-readonly {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
}

.form-error {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--clr-red);
  letter-spacing: 0.05em;
  margin-top: 2px;
}

.form-submit {
  margin-top: 4px;
  width: 100%;
  justify-content: center;
  font-size: 14px;
  padding: 12px 24px;
}

/* ===== Slider ===== */

.nb-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 4px;
  background: var(--clr-border);
  outline: none;
  cursor: pointer;
}

.nb-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px;
  height: 16px;
  background: var(--clr-yellow);
  border: 2px solid #000;
  box-shadow: 2px 2px 0px #000;
  cursor: pointer;
}

.nb-slider::-moz-range-thumb {
  width: 16px;
  height: 16px;
  background: var(--clr-yellow);
  border: 2px solid #000;
  box-shadow: 2px 2px 0px #000;
  cursor: pointer;
  border-radius: 0;
}

.nb-slider:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.nb-slider__ticks {
  display: flex;
  justify-content: space-between;
  padding: 0 2px;
  margin-top: 2px;
}

.nb-slider__tick {
  font-size: 9px;
  letter-spacing: 0;
  color: var(--clr-text-dim);
}

/* ===== Models list ===== */

.models-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ===== Vuetify overrides (scoped can't reach internals, use :deep) ===== */

:deep(.nb-select .v-field),
:deep(.nb-input .v-field) {
  font-family: var(--font-mono) !important;
  font-size: 13px !important;
  background: var(--clr-panel) !important;
}

:deep(.nb-select .v-field__input),
:deep(.nb-input .v-field__input) {
  font-family: var(--font-mono) !important;
  font-size: 13px !important;
  color: var(--clr-text) !important;
  padding-top: 6px !important;
  padding-bottom: 6px !important;
  min-height: unset !important;
}

:deep(.nb-select .v-select__selection-text) {
  font-family: var(--font-mono) !important;
  font-size: 13px !important;
}

:deep(.nb-select .v-field__outline__start),
:deep(.nb-select .v-field__outline__end),
:deep(.nb-select .v-field__outline__notch::before),
:deep(.nb-select .v-field__outline__notch::after),
:deep(.nb-input .v-field__outline__start),
:deep(.nb-input .v-field__outline__end),
:deep(.nb-input .v-field__outline__notch::before),
:deep(.nb-input .v-field__outline__notch::after) {
  border-color: var(--clr-border) !important;
  border-width: 2px !important;
}

:deep(.nb-select.v-input--focused .v-field__outline__start),
:deep(.nb-select.v-input--focused .v-field__outline__end),
:deep(.nb-input.v-input--focused .v-field__outline__start),
:deep(.nb-input.v-input--focused .v-field__outline__end) {
  border-color: var(--clr-yellow) !important;
}

/* Dropdown list items */
:deep(.v-list-item__content) {
  font-family: var(--font-mono) !important;
  font-size: 12px !important;
}
</style>
