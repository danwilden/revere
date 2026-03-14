<template>
  <div class="rules-editor">
    <div class="rules-editor__header">
      <span class="nb-label">STRATEGY DEFINITION JSON (RULES DSL)</span>
      <button
        class="nb-btn rules-editor__ref-toggle"
        :class="{ 'rules-editor__ref-toggle--open': showReference }"
        @click="showReference = !showReference"
      >
        {{ showReference ? 'HIDE' : 'SHOW' }} REFERENCE
      </button>
    </div>

    <div v-if="showReference" class="rules-editor__reference nb-panel">
      <div class="rules-editor__reference-grid">
        <div class="rules-ref-section">
          <span class="nb-label text-yellow">TOP-LEVEL KEYS</span>
          <div class="rules-ref-items">
            <div v-for="field in topLevelFields" :key="field.key" class="rules-ref-item">
              <code class="rules-ref-item__key">{{ field.key }}</code>
              <span class="rules-ref-item__desc text-muted">{{ field.desc }}</span>
            </div>
          </div>
        </div>

        <div class="rules-ref-section">
          <span class="nb-label text-yellow">AVAILABLE FIELDS</span>
          <div class="rules-ref-items rules-ref-items--chips">
            <code v-for="f in availableFields" :key="f" class="rules-ref-chip">{{ f }}</code>
          </div>
        </div>

        <div class="rules-ref-section">
          <span class="nb-label text-yellow">OPERATORS</span>
          <div class="rules-ref-items rules-ref-items--chips">
            <code v-for="op in operators" :key="op" class="rules-ref-chip rules-ref-chip--op">{{ op }}</code>
          </div>
        </div>

        <div class="rules-ref-section">
          <span class="nb-label text-yellow">COMPOSITES</span>
          <div class="rules-ref-items">
            <div class="rules-ref-item">
              <code class="rules-ref-item__key">all</code>
              <span class="rules-ref-item__desc text-muted">All child conditions must pass (AND)</span>
            </div>
            <div class="rules-ref-item">
              <code class="rules-ref-item__key">any</code>
              <span class="rules-ref-item__desc text-muted">At least one child must pass (OR)</span>
            </div>
            <div class="rules-ref-item">
              <code class="rules-ref-item__key">not</code>
              <span class="rules-ref-item__desc text-muted">Inverts child condition</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="rules-editor__textarea-wrap" :class="validationBorderClass">
      <textarea
        class="rules-editor__textarea font-mono"
        :value="modelValue"
        rows="18"
        spellcheck="false"
        autocomplete="off"
        @input="onInput"
      />
      <div v-if="parseError" class="rules-editor__parse-error nb-label text-red">
        JSON PARSE ERROR: {{ parseError }}
      </div>
    </div>

    <div class="rules-editor__hint nb-label text-dim">
      Edit the JSON directly. Nested all/any composites supported. Named conditions can be defined
      in the "named_conditions" object and referenced via { "ref": "condition_name" }.
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  /** The raw JSON string value */
  modelValue: {
    type: String,
    default: '',
  },
  /** Validation state from the parent: null | 'valid' | 'invalid' */
  validationState: {
    type: String,
    default: null,
  },
})

const emit = defineEmits(['update:modelValue'])

const showReference = ref(false)
const parseError = ref(null)

const topLevelFields = [
  { key: 'entry_long', desc: 'Condition tree that triggers a long entry' },
  { key: 'entry_short', desc: 'Condition tree that triggers a short entry' },
  { key: 'exit', desc: 'Condition tree that triggers position exit' },
  { key: 'stop_atr_multiplier', desc: 'Stop loss distance as ATR multiple (e.g. 1.5)' },
  { key: 'take_profit_atr_multiplier', desc: 'Take profit distance as ATR multiple (e.g. 2.0)' },
  { key: 'position_size_units', desc: 'Fixed position size in base currency units (e.g. 10000)' },
  { key: 'named_conditions', desc: 'Named reusable condition trees, referenced via { "ref": "..." }' },
]

const availableFields = [
  'rsi_14', 'adx_14', 'atr_14', 'atr_pct_14',
  'ema_slope_20', 'ema_slope_50',
  'breakout_20', 'rvol_20', 'session',
  'log_ret_1', 'log_ret_5', 'log_ret_20',
]

const operators = ['gt', 'gte', 'lt', 'lte', 'eq', 'neq', 'in']

const validationBorderClass = computed(() => {
  if (props.validationState === 'valid') return 'rules-editor__textarea-wrap--valid'
  if (props.validationState === 'invalid') return 'rules-editor__textarea-wrap--invalid'
  return ''
})

function onInput(e) {
  const raw = e.target.value
  emit('update:modelValue', raw)
  // Live parse check
  try {
    JSON.parse(raw)
    parseError.value = null
  } catch (err) {
    parseError.value = err.message
  }
}

// Seed parse check on mount
watch(
  () => props.modelValue,
  (val) => {
    if (!val) return
    try {
      JSON.parse(val)
      parseError.value = null
    } catch (err) {
      parseError.value = err.message
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.rules-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.rules-editor__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.rules-editor__ref-toggle {
  font-size: 11px;
  padding: 5px 12px;
}

.rules-editor__ref-toggle--open {
  border-color: var(--clr-yellow);
  color: var(--clr-yellow);
}

/* Reference panel */
.rules-editor__reference {
  border: 1px solid var(--clr-border);
}

.rules-editor__reference-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px;
}

.rules-ref-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rules-ref-items {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.rules-ref-items--chips {
  flex-direction: row;
  flex-wrap: wrap;
  gap: 6px;
}

.rules-ref-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rules-ref-item__key {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--clr-yellow);
  background: rgba(255, 230, 0, 0.06);
  padding: 1px 6px;
  display: inline-block;
}

.rules-ref-item__desc {
  font-family: var(--font-mono);
  font-size: 11px;
  padding-left: 2px;
}

.rules-ref-chip {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--clr-text);
  background: var(--clr-panel-alt);
  border: 1px solid var(--clr-border);
  padding: 2px 8px;
}

.rules-ref-chip--op {
  color: #4FC3F7;
  border-color: rgba(79, 195, 247, 0.3);
  background: rgba(79, 195, 247, 0.06);
}

/* Textarea */
.rules-editor__textarea-wrap {
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb);
  transition: border-color 150ms, box-shadow 150ms;
}

.rules-editor__textarea-wrap--valid {
  border-color: var(--clr-green);
  box-shadow: var(--shadow-nb-green);
}

.rules-editor__textarea-wrap--invalid {
  border-color: var(--clr-red);
  box-shadow: var(--shadow-nb-red);
}

.rules-editor__textarea {
  width: 100%;
  display: block;
  background: #0a0a0a;
  color: var(--clr-text);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  padding: 14px 16px;
  border: none;
  outline: none;
  resize: vertical;
  min-height: 320px;
}

.rules-editor__textarea:focus {
  outline: none;
}

.rules-editor__parse-error {
  padding: 6px 14px;
  background: rgba(255, 34, 34, 0.08);
  border-top: 1px solid var(--clr-red);
  font-size: 11px;
}

.rules-editor__hint {
  font-size: 11px;
  line-height: 1.5;
}
</style>
