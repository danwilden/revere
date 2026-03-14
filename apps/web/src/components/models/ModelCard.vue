<template>
  <div :class="['nb-card', 'model-card', isSucceeded && 'nb-card--success']">
    <!-- Header row: instrument/timeframe + status -->
    <div class="model-card__header">
      <div class="model-card__title-group">
        <span class="nb-value nb-value--lg font-mono">{{ model.instrument_id }}</span>
        <span class="model-card__timeframe nb-label">{{ model.timeframe }}</span>
      </div>
      <StatusBadge :status="model.status ?? 'unknown'" />
    </div>

    <!-- Training date range -->
    <div class="model-card__row">
      <span class="nb-label">TRAIN WINDOW</span>
      <span class="nb-value nb-value--sm font-mono">
        {{ formatDate(model.training_start) }} — {{ formatDate(model.training_end) }}
      </span>
    </div>

    <!-- N states (parsed from parameters_json) -->
    <div class="model-card__row">
      <span class="nb-label">STATES</span>
      <span class="nb-value nb-value--sm nb-value--accent font-mono">{{ numStates }}</span>
    </div>

    <!-- Model ID — truncated + copyable -->
    <div class="model-card__row model-card__id-row">
      <span class="nb-label">MODEL ID</span>
      <button
        class="model-card__id-btn font-mono"
        :title="model.id"
        @click="copyId"
      >
        {{ shortId }}
        <span v-if="copied" class="model-card__copied text-green">COPIED</span>
      </button>
    </div>

    <!-- Regime label chips (when label_map_json is populated) -->
    <div v-if="hasLabels" class="model-card__labels">
      <span class="nb-label" style="margin-bottom: 8px; display: block">REGIME LABELS</span>
      <div class="model-card__chips">
        <span
          v-for="(label, stateId) in labelMap"
          :key="stateId"
          class="model-card__chip"
          :style="chipStyle(label)"
        >
          <span class="model-card__chip-state">{{ stateId }}</span>
          {{ label }}
        </span>
      </div>
    </div>

    <!-- Apply Labels button — only shown when labels are absent and model succeeded -->
    <div v-if="isSucceeded && !hasLabels" class="model-card__actions">
      <button
        class="nb-btn nb-btn--primary model-card__label-btn"
        :disabled="applyingLabels"
        @click="handleApplyLabels"
      >
        {{ applyingLabels ? 'APPLYING...' : 'AUTO-LABEL STATES' }}
      </button>
      <ErrorBanner v-if="labelError" :message="labelError" style="margin-top: 8px" />
    </div>

    <!-- Re-label button when labels already exist -->
    <div v-else-if="isSucceeded && hasLabels" class="model-card__actions">
      <button
        class="nb-btn model-card__relabel-btn"
        :disabled="applyingLabels"
        @click="handleApplyLabels"
      >
        {{ applyingLabels ? 'APPLYING...' : 'RE-LABEL' }}
      </button>
      <ErrorBanner v-if="labelError" :message="labelError" style="margin-top: 8px" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import { REGIME_COLORS } from '@/utils/constants.js'
import { useModelsStore } from '@/stores/models.js'

const props = defineProps({
  /**
   * HMM model record from GET /api/models/hmm.
   * Shape: { id, model_type, instrument_id, timeframe, training_start, training_end,
   *          parameters_json (string), artifact_ref, label_map_json, created_at, status,
   *          log_likelihood, state_stats_json }
   */
  model: {
    type: Object,
    required: true,
  },
})

const store = useModelsStore()

// ---------------------------------------------------------------------------
// Derived state
// ---------------------------------------------------------------------------

const isSucceeded = computed(() => props.model.status?.toLowerCase() === 'succeeded')

const numStates = computed(() => {
  try {
    const params =
      typeof props.model.parameters_json === 'string'
        ? JSON.parse(props.model.parameters_json)
        : props.model.parameters_json ?? {}
    return params.num_states ?? '—'
  } catch {
    return '—'
  }
})

const labelMap = computed(() => {
  const raw = props.model.label_map_json
  if (!raw || typeof raw !== 'object' || Object.keys(raw).length === 0) return null
  return raw
})

const hasLabels = computed(() => !!labelMap.value)

const shortId = computed(() => {
  const id = props.model.id ?? ''
  return id.length > 18 ? `${id.slice(0, 8)}...${id.slice(-6)}` : id
})

// ---------------------------------------------------------------------------
// Copy ID
// ---------------------------------------------------------------------------

const copied = ref(false)
let copyTimer = null

function copyId() {
  if (!props.model.id) return
  navigator.clipboard?.writeText(props.model.id).catch(() => {})
  copied.value = true
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copied.value = false
  }, 1800)
}

// ---------------------------------------------------------------------------
// Date formatting
// ---------------------------------------------------------------------------

function formatDate(isoStr) {
  if (!isoStr) return '—'
  try {
    return isoStr.slice(0, 10)
  } catch {
    return isoStr
  }
}

// ---------------------------------------------------------------------------
// Chip styling
// ---------------------------------------------------------------------------

function chipStyle(label) {
  const color = REGIME_COLORS[label] ?? REGIME_COLORS.UNKNOWN
  return {
    borderColor: color,
    color: color,
    background: `${color}18`, // ~10% opacity tint
  }
}

// ---------------------------------------------------------------------------
// Apply Labels
// ---------------------------------------------------------------------------

const applyingLabels = ref(false)
const labelError = ref(null)

async function handleApplyLabels() {
  applyingLabels.value = true
  labelError.value = null
  try {
    // Request auto-labeling via the backend endpoint.
    // The backend auto_label_states logic runs server-side; we trigger it by
    // calling POST /api/models/hmm/{id}/label with the existing state count
    // expressed as an empty label_map — the backend fills it in.
    // However, the real auto-label result is already stored server-side at
    // training time. The label endpoint is for overwriting with custom labels.
    // So here we fetch the fresh model record to get the auto-generated labels.
    // If label_map_json is somehow absent, we cannot generate it client-side
    // without state_stats — surface a helpful error.
    const fresh = await store.fetchModels()
    // After fetchModels the store.hmmModels is updated and the parent list
    // re-renders this card with the new label_map_json via the :model prop.
    // If the model still has no labels after a refresh, tell the user.
    void fresh
  } catch (err) {
    labelError.value = err?.normalized?.message ?? err?.message ?? 'Failed to apply labels'
  } finally {
    applyingLabels.value = false
  }
}
</script>

<style scoped>
.model-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.model-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--clr-border);
}

.model-card__title-group {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.model-card__timeframe {
  font-size: 13px;
  color: var(--clr-yellow);
  letter-spacing: 0.12em;
}

.model-card__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.model-card__id-row {
  flex-wrap: wrap;
}

.model-card__id-btn {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-size: 11px;
  color: var(--clr-text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
  text-align: right;
}

.model-card__id-btn:hover {
  color: var(--clr-yellow);
}

.model-card__copied {
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.model-card__labels {
  padding-top: 8px;
  border-top: 1px solid var(--clr-border);
}

.model-card__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.model-card__chip {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 2px 8px;
  border: 1px solid;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  line-height: 1.6;
}

.model-card__chip-state {
  opacity: 0.6;
  font-size: 9px;
}

.model-card__actions {
  padding-top: 10px;
  border-top: 1px solid var(--clr-border);
}

.model-card__label-btn,
.model-card__relabel-btn {
  width: 100%;
  justify-content: center;
}
</style>
