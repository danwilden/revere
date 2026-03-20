<template>
  <div class="nb-card robustness-panel">
    <!-- Panel header -->
    <div class="robustness-panel__header">
      <span class="nb-heading nb-heading--md">ROBUSTNESS BATTERY</span>
      <span v-if="jobStatus" :class="['status-chip', `status-chip--${chipStatus}`]">
        <span class="status-dot" />
        {{ jobStatusLabel }}
      </span>
    </div>

    <!-- Error banner -->
    <div v-if="errorMessage" class="nb-banner nb-banner--error" style="margin-bottom: 14px">
      <span style="color: var(--clr-red); font-weight: 700; margin-right: 8px">ERR //</span>
      {{ errorMessage }}
    </div>

    <!-- Idle state — promote CTA -->
    <div v-if="phase === 'idle'" class="robustness-panel__idle">
      <p class="nb-label" style="margin-bottom: 16px; line-height: 1.7">
        Run a full robustness battery against this experiment: holdout, walk-forward,
        cost stress, and parameter sensitivity. Battery runs async — results appear
        when complete.
      </p>
      <button
        class="nb-btn nb-btn--primary robustness-panel__promote-btn"
        :disabled="promoting"
        @click="handlePromote"
      >
        {{ promoting ? 'QUEUING...' : 'PROMOTE &amp; RUN BATTERY' }}
      </button>
    </div>

    <!-- Running / queued state -->
    <div v-else-if="phase === 'running'" class="robustness-panel__running">
      <div class="robustness-panel__running-row">
        <span class="nb-label">BATTERY IN PROGRESS</span>
        <span v-if="progressPct !== null" class="nb-value nb-value--sm nb-value--accent">
          {{ progressPct.toFixed(0) }}%
        </span>
      </div>
      <div :class="['nb-progress', 'robustness-panel__progress', progressPct === null && 'nb-progress--indeterminate']">
        <div
          v-if="progressPct !== null"
          class="nb-progress__bar"
          :style="{ width: `${progressPct}%` }"
        />
        <div v-else class="nb-progress__bar" />
      </div>
      <p class="nb-label" style="margin-top: 10px">
        Polling every 3s — do not close this tab.
      </p>
    </div>

    <!-- Results state -->
    <div v-else-if="phase === 'complete' && result" class="robustness-panel__results">
      <!-- Overall verdict -->
      <div class="robustness-panel__verdict">
        <span class="nb-label">OVERALL VERDICT</span>
        <span :class="['verdict-badge', result.promoted ? 'verdict-badge--pass' : 'verdict-badge--fail']">
          {{ result.promoted ? 'PROMOTED' : 'NOT PROMOTED' }}
        </span>
      </div>

      <!-- Block reasons -->
      <div v-if="result.block_reasons && result.block_reasons.length" class="robustness-panel__blocks">
        <span class="nb-label" style="display: block; margin-bottom: 8px">BLOCK REASONS</span>
        <ul class="block-reasons-list">
          <li v-for="(reason, i) in result.block_reasons" :key="i" class="block-reason-item">
            <span class="block-reason-item__bullet">&#9656;</span>
            {{ reason }}
          </li>
        </ul>
      </div>

      <!-- Per-check rows -->
      <div class="robustness-panel__checks">
        <span class="nb-label" style="display: block; margin-bottom: 10px">CHECK BREAKDOWN</span>

        <!-- Holdout -->
        <div v-if="result.holdout" class="check-row">
          <div class="check-row__label-col">
            <span :class="['pass-badge', result.holdout.passed ? 'pass-badge--pass' : 'pass-badge--fail']">
              {{ result.holdout.passed ? 'PASS' : 'FAIL' }}
            </span>
            <span class="check-row__name">HOLDOUT</span>
          </div>
          <div class="check-row__metrics">
            <span class="check-row__metric-item">
              <span class="nb-label">RETURN</span>
              <span
                :class="[
                  'nb-value nb-value--sm',
                  result.holdout.net_return_pct === null ? 'text-muted'
                    : result.holdout.net_return_pct >= 0 ? 'nb-value--positive' : 'nb-value--negative'
                ]"
              >
                {{ result.holdout.net_return_pct !== null ? `${result.holdout.net_return_pct.toFixed(2)}%` : '—' }}
              </span>
            </span>
            <span class="check-row__metric-item">
              <span class="nb-label">TRADES</span>
              <span class="nb-value nb-value--sm">{{ result.holdout.trade_count ?? '—' }}</span>
            </span>
          </div>
        </div>

        <!-- Walk-forward -->
        <div v-if="result.walk_forward" class="check-row">
          <div class="check-row__label-col">
            <span :class="['pass-badge', result.walk_forward.passed ? 'pass-badge--pass' : 'pass-badge--fail']">
              {{ result.walk_forward.passed ? 'PASS' : 'FAIL' }}
            </span>
            <span class="check-row__name">WALK-FORWARD</span>
          </div>
          <div class="check-row__metrics">
            <span class="check-row__metric-item">
              <span class="nb-label">WINDOWS</span>
              <span class="nb-value nb-value--sm">
                {{ result.walk_forward.windows_passed }} / {{ result.walk_forward.windows_total }}
              </span>
            </span>
          </div>
        </div>

        <!-- Cost stress -->
        <div v-if="result.cost_stress" class="check-row">
          <div class="check-row__label-col">
            <span :class="['pass-badge', result.cost_stress.passed ? 'pass-badge--pass' : 'pass-badge--fail']">
              {{ result.cost_stress.passed ? 'PASS' : 'FAIL' }}
            </span>
            <span class="check-row__name">COST STRESS</span>
          </div>
          <div class="check-row__metrics">
            <span
              v-for="v in (result.cost_stress.variants || [])"
              :key="v.multiplier"
              class="check-row__metric-item"
            >
              <span class="nb-label">{{ v.multiplier }}x</span>
              <span :class="['nb-value nb-value--sm', v.passed ? 'nb-value--positive' : 'nb-value--negative']">
                {{ v.passed ? 'OK' : 'FAIL' }}
              </span>
            </span>
          </div>
        </div>

        <!-- Param sensitivity -->
        <div v-if="result.param_sensitivity" class="check-row">
          <div class="check-row__label-col">
            <span :class="['pass-badge', result.param_sensitivity.passed ? 'pass-badge--pass' : 'pass-badge--fail']">
              {{ result.param_sensitivity.passed ? 'PASS' : 'FAIL' }}
            </span>
            <span class="check-row__name">PARAM SENSITIVITY</span>
          </div>
          <div class="check-row__metrics">
            <span class="check-row__metric-item">
              <span class="nb-label">RETURN RANGE</span>
              <span class="nb-value nb-value--sm">
                {{ result.param_sensitivity.return_range_pct !== null
                    ? `${result.param_sensitivity.return_range_pct.toFixed(2)}%`
                    : '—' }}
              </span>
            </span>
          </div>
        </div>
      </div>

      <!-- Approve / Discard actions — only shown if promoted -->
      <div v-if="result.promoted" class="robustness-panel__actions">
        <span class="nb-label" style="display: block; margin-bottom: 10px">ACTIONS</span>
        <div class="robustness-panel__action-row">
          <button
            class="nb-btn robustness-panel__approve-btn"
            :disabled="actioning"
            @click="confirmApprove"
          >
            APPROVE
          </button>
          <button
            class="nb-btn robustness-panel__discard-btn"
            :disabled="actioning"
            @click="openDiscard"
          >
            DISCARD
          </button>
        </div>
      </div>

      <!-- Re-run link -->
      <div style="margin-top: 20px; border-top: 1px solid var(--clr-border); padding-top: 14px">
        <button class="nb-btn" style="font-size: 11px; padding: 6px 14px" @click="resetToIdle">
          RE-RUN BATTERY
        </button>
      </div>
    </div>

    <!-- Failed state -->
    <div v-else-if="phase === 'failed'" class="robustness-panel__failed">
      <div class="nb-banner nb-banner--error" style="margin-bottom: 14px">
        Battery job failed.
        <span v-if="failedMessage">{{ failedMessage }}</span>
      </div>
      <button class="nb-btn" @click="resetToIdle">RETRY</button>
    </div>

    <!-- Approve confirmation dialog -->
    <div v-if="showApproveDialog" class="rp-dialog-backdrop" @click.self="showApproveDialog = false">
      <div class="rp-dialog nb-card nb-card--success">
        <div class="rp-dialog__header">
          <span class="nb-heading nb-heading--sm">CONFIRM APPROVE</span>
        </div>
        <p class="nb-label" style="margin: 12px 0; line-height: 1.7; color: var(--clr-text)">
          This will mark the experiment as <strong style="color: var(--clr-green)">APPROVED</strong>
          and promote it to the strategy registry. This action cannot be undone.
        </p>
        <div class="rp-dialog__footer">
          <button
            class="nb-btn robustness-panel__approve-btn"
            :disabled="actioning"
            @click="handleApprove"
          >
            {{ actioning ? 'APPROVING...' : 'CONFIRM APPROVE' }}
          </button>
          <button class="nb-btn" :disabled="actioning" @click="showApproveDialog = false">
            CANCEL
          </button>
        </div>
      </div>
    </div>

    <!-- Discard confirmation dialog -->
    <div v-if="showDiscardDialog" class="rp-dialog-backdrop" @click.self="showDiscardDialog = false">
      <div class="rp-dialog nb-card nb-card--error">
        <div class="rp-dialog__header">
          <span class="nb-heading nb-heading--sm">CONFIRM DISCARD</span>
        </div>
        <p class="nb-label" style="margin: 12px 0 8px; line-height: 1.7; color: var(--clr-text)">
          This experiment will be <strong style="color: var(--clr-red)">DISCARDED</strong>.
          Provide a reason — required.
        </p>
        <textarea
          v-model="discardReason"
          class="rp-reason-input"
          placeholder="Enter reason for discarding..."
          rows="3"
        />
        <div v-if="discardReasonError" class="nb-banner nb-banner--error" style="margin-top: 8px">
          {{ discardReasonError }}
        </div>
        <div class="rp-dialog__footer">
          <button
            class="nb-btn robustness-panel__discard-btn"
            :disabled="actioning || !discardReason.trim()"
            @click="handleDiscard"
          >
            {{ actioning ? 'DISCARDING...' : 'CONFIRM DISCARD' }}
          </button>
          <button class="nb-btn" :disabled="actioning" @click="showDiscardDialog = false">
            CANCEL
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import {
  promoteExperiment,
  getRobustnessStatus,
  approveExperiment,
  discardExperiment,
} from '../../api/experiments.js'

// ---------------------------------------------------------------------------
// Props / emits
// ---------------------------------------------------------------------------
const props = defineProps({
  experimentId: {
    type: String,
    required: true,
  },
})

const emit = defineEmits(['approved', 'discarded'])

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
// phase: 'idle' | 'running' | 'complete' | 'failed'
const phase = ref('idle')

const promoting = ref(false)
const errorMessage = ref(null)
const failedMessage = ref(null)

// Robustness polling
const jobStatus = ref(null)      // raw status string from API
const progressPct = ref(null)
const result = ref(null)         // robustness result object

// Action state
const actioning = ref(false)
const showApproveDialog = ref(false)
const showDiscardDialog = ref(false)
const discardReason = ref('')
const discardReasonError = ref(null)

let pollTimer = null

// ---------------------------------------------------------------------------
// Computed helpers
// ---------------------------------------------------------------------------
const chipStatus = computed(() => {
  const s = (jobStatus.value || '').toLowerCase()
  if (s === 'queued') return 'queued'
  if (s === 'running') return 'running'
  if (s === 'succeeded') return 'succeeded'
  if (s === 'failed') return 'failed'
  return 'queued'
})

const jobStatusLabel = computed(() => {
  const map = {
    queued: 'QUEUED',
    running: 'RUNNING',
    succeeded: 'COMPLETE',
    failed: 'FAILED',
  }
  return map[chipStatus.value] ?? (jobStatus.value || '').toUpperCase()
})

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------
const stopPolling = () => {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const startPolling = () => {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const data = await getRobustnessStatus(props.experimentId)
      jobStatus.value = data.job_status
      progressPct.value = data.progress_pct ?? null

      const terminal = ['succeeded', 'failed']
      if (terminal.includes((data.job_status || '').toLowerCase())) {
        stopPolling()
        if ((data.job_status || '').toLowerCase() === 'succeeded') {
          result.value = data.result ?? null
          phase.value = 'complete'
        } else {
          failedMessage.value = data.error_message ?? null
          phase.value = 'failed'
        }
      }
    } catch (err) {
      stopPolling()
      errorMessage.value = err.normalized?.detail ?? err.message ?? 'Polling error.'
      phase.value = 'failed'
    }
  }, 3000)
}

// ---------------------------------------------------------------------------
// Promote handler
// ---------------------------------------------------------------------------
const handlePromote = async () => {
  promoting.value = true
  errorMessage.value = null

  try {
    const data = await promoteExperiment(props.experimentId)
    jobStatus.value = data.status ?? 'queued'
    phase.value = 'running'
    startPolling()
  } catch (err) {
    errorMessage.value = err.normalized?.detail ?? err.message ?? 'Could not promote experiment.'
  } finally {
    promoting.value = false
  }
}

// ---------------------------------------------------------------------------
// Approve / Discard
// ---------------------------------------------------------------------------
const confirmApprove = () => {
  showApproveDialog.value = true
}

const handleApprove = async () => {
  actioning.value = true
  errorMessage.value = null
  try {
    const data = await approveExperiment(props.experimentId)
    showApproveDialog.value = false
    emit('approved', data.experiment)
  } catch (err) {
    errorMessage.value = err.normalized?.detail ?? err.message ?? 'Could not approve experiment.'
    showApproveDialog.value = false
  } finally {
    actioning.value = false
  }
}

const openDiscard = () => {
  discardReason.value = ''
  discardReasonError.value = null
  showDiscardDialog.value = true
}

const handleDiscard = async () => {
  const reason = discardReason.value.trim()
  if (!reason) {
    discardReasonError.value = 'A reason is required to discard.'
    return
  }
  actioning.value = true
  discardReasonError.value = null
  errorMessage.value = null
  try {
    const data = await discardExperiment(props.experimentId, reason)
    showDiscardDialog.value = false
    emit('discarded', data.experiment)
  } catch (err) {
    errorMessage.value = err.normalized?.detail ?? err.message ?? 'Could not discard experiment.'
    showDiscardDialog.value = false
  } finally {
    actioning.value = false
  }
}

// ---------------------------------------------------------------------------
// Re-run
// ---------------------------------------------------------------------------
const resetToIdle = () => {
  stopPolling()
  phase.value = 'idle'
  jobStatus.value = null
  progressPct.value = null
  result.value = null
  errorMessage.value = null
  failedMessage.value = null
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------
onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
/* ---- Panel shell ---- */
.robustness-panel {
  position: relative;
}

.robustness-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--clr-border);
}

/* ---- Idle / promote button ---- */
.robustness-panel__promote-btn {
  font-size: 13px;
  padding: 10px 28px;
  box-shadow: var(--shadow-nb-yellow);
  border: 2px solid #000;
}

/* ---- Running ---- */
.robustness-panel__running-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.robustness-panel__progress {
  margin-bottom: 0;
  height: 6px;
}

/* ---- Verdict ---- */
.robustness-panel__verdict {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--clr-border);
}

.verdict-badge {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 4px 14px;
  border: 2px solid;
}

.verdict-badge--pass {
  background: var(--clr-green);
  border-color: var(--clr-green);
  color: #000;
  box-shadow: 3px 3px 0px #000;
}

.verdict-badge--fail {
  background: var(--clr-red);
  border-color: var(--clr-red);
  color: #fff;
  box-shadow: 3px 3px 0px #000;
}

/* ---- Block reasons ---- */
.robustness-panel__blocks {
  background: rgba(255, 34, 34, 0.06);
  border-left: 4px solid var(--clr-red);
  padding: 10px 14px;
  margin-bottom: 18px;
}

.block-reasons-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.block-reason-item {
  font-family: var(--font-mono);
  font-size: 12px;
  color: #ff8888;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.block-reason-item__bullet {
  color: var(--clr-red);
  flex-shrink: 0;
  margin-top: 1px;
}

/* ---- Check rows ---- */
.robustness-panel__checks {
  margin-bottom: 18px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.check-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--clr-panel-alt);
  border: 1px solid var(--clr-border);
}

.check-row__label-col {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 180px;
}

.check-row__name {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--clr-text);
}

.check-row__metrics {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}

.check-row__metric-item {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

/* Pass/fail pill */
.pass-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 2px 8px;
  border: 1px solid;
  flex-shrink: 0;
}

.pass-badge--pass {
  background: rgba(0, 255, 65, 0.15);
  border-color: var(--clr-green);
  color: var(--clr-green);
}

.pass-badge--fail {
  background: rgba(255, 34, 34, 0.15);
  border-color: var(--clr-red);
  color: var(--clr-red);
}

/* ---- Actions ---- */
.robustness-panel__actions {
  padding-top: 18px;
  border-top: 1px solid var(--clr-border);
}

.robustness-panel__action-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.robustness-panel__approve-btn {
  background: var(--clr-green);
  border: 2px solid #000;
  color: #000;
  font-weight: 700;
  box-shadow: 3px 3px 0px #000;
}

.robustness-panel__approve-btn:hover:not(:disabled) {
  background: #00cc34;
  border-color: #000;
  color: #000;
}

.robustness-panel__discard-btn {
  background: transparent;
  border: 2px solid var(--clr-red);
  color: var(--clr-red);
  font-weight: 700;
  box-shadow: 3px 3px 0px var(--clr-red);
}

.robustness-panel__discard-btn:hover:not(:disabled) {
  background: rgba(255, 34, 34, 0.1);
  border-color: var(--clr-red);
  color: var(--clr-red);
}

/* ---- Dialogs ---- */
.rp-dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  padding: 16px;
}

.rp-dialog {
  width: 100%;
  max-width: 440px;
  background: var(--clr-surface);
}

.rp-dialog__header {
  margin-bottom: 4px;
}

.rp-dialog__footer {
  display: flex;
  gap: 12px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.rp-reason-input {
  width: 100%;
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  color: var(--clr-text);
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 10px 12px;
  resize: vertical;
  margin-top: 4px;
  outline: none;
  transition: border-color 80ms;
}

.rp-reason-input:focus {
  border-color: var(--clr-yellow);
}

.rp-reason-input::placeholder {
  color: var(--clr-text-dim);
}
</style>
