<template>
  <div class="code-editor">
    <div class="code-editor__header">
      <span class="nb-label">STRATEGY PYTHON CODE</span>
      <button
        class="nb-btn code-editor__hint-toggle"
        :class="{ 'code-editor__hint-toggle--open': showHints }"
        @click="showHints = !showHints"
      >
        {{ showHints ? 'HIDE' : 'SHOW' }} API REFERENCE
      </button>
    </div>

    <div v-if="showHints" class="code-editor__hints nb-panel">
      <div class="code-hints-grid">
        <div class="code-hints-section">
          <span class="nb-label text-yellow">REQUIRED METHODS</span>
          <div class="code-hints-items">
            <div v-for="m in requiredMethods" :key="m.sig" class="code-hints-method">
              <code class="code-hints-method__sig">{{ m.sig }}</code>
              <span class="code-hints-method__desc text-muted">{{ m.desc }}</span>
            </div>
          </div>
        </div>

        <div class="code-hints-section">
          <span class="nb-label text-yellow">OPTIONAL OVERRIDES</span>
          <div class="code-hints-items">
            <div v-for="m in optionalMethods" :key="m.sig" class="code-hints-method">
              <code class="code-hints-method__sig">{{ m.sig }}</code>
              <span class="code-hints-method__desc text-muted">{{ m.desc }}</span>
            </div>
          </div>
        </div>

        <div class="code-hints-section">
          <span class="nb-label text-yellow">features DICT KEYS</span>
          <div class="code-hints-chips">
            <code v-for="f in featureKeys" :key="f" class="code-hints-chip">{{ f }}</code>
          </div>
        </div>

        <div class="code-hints-section">
          <span class="nb-label text-yellow">bar DICT KEYS</span>
          <div class="code-hints-chips">
            <code v-for="b in barKeys" :key="b" class="code-hints-chip code-hints-chip--bar">{{ b }}</code>
          </div>
        </div>
      </div>

      <div class="code-hints-note nb-banner nb-banner--info">
        Class must extend BaseStrategy (already in sandbox scope — no import needed).
        state is a StrategyState instance: state.is_flat, state.is_long, state.is_short, state.current_regime.
        equity is a float representing current portfolio equity.
      </div>
    </div>

    <div class="code-editor__textarea-wrap" :class="validationBorderClass">
      <div class="code-editor__line-nums" aria-hidden="true">
        <span
          v-for="n in lineCount"
          :key="n"
          class="code-editor__line-num"
        >{{ n }}</span>
      </div>
      <textarea
        class="code-editor__textarea font-mono"
        :value="modelValue"
        spellcheck="false"
        autocomplete="off"
        @input="onInput"
        @keydown.tab.prevent="onTab"
      />
    </div>

    <div class="code-editor__footer">
      <span class="nb-label text-dim">
        {{ lineCount }} lines
      </span>
      <span class="nb-label text-dim">
        Python 3 — BaseStrategy in scope
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  /** Raw Python code string */
  modelValue: {
    type: String,
    default: '',
  },
  /** Validation state: null | 'valid' | 'invalid' */
  validationState: {
    type: String,
    default: null,
  },
})

const emit = defineEmits(['update:modelValue'])

const showHints = ref(false)

const requiredMethods = [
  { sig: 'should_enter_long(self, bar, features, state, equity)', desc: 'Return True to open a long' },
  { sig: 'should_enter_short(self, bar, features, state, equity)', desc: 'Return True to open a short' },
  { sig: 'should_exit(self, bar, features, state, equity)', desc: 'Return True to close the open position' },
]

const optionalMethods = [
  { sig: 'position_size(self, bar, features, state, equity)', desc: 'Override default position sizing (default: 10,000 units)' },
  { sig: 'stop_price(self, bar, features, state, equity)', desc: 'Override ATR-based stop loss price' },
  { sig: 'take_profit_price(self, bar, features, state, equity)', desc: 'Override ATR-based take profit price' },
]

const featureKeys = [
  'rsi_14', 'adx_14', 'atr_14', 'atr_pct_14',
  'ema_slope_20', 'ema_slope_50',
  'breakout_20', 'rvol_20', 'session',
  'log_ret_1', 'log_ret_5', 'log_ret_20',
]

const barKeys = ['open', 'high', 'low', 'close', 'volume', 'timestamp_utc']

const lineCount = computed(() => {
  if (!props.modelValue) return 1
  return props.modelValue.split('\n').length
})

const validationBorderClass = computed(() => {
  if (props.validationState === 'valid') return 'code-editor__textarea-wrap--valid'
  if (props.validationState === 'invalid') return 'code-editor__textarea-wrap--invalid'
  return ''
})

function onInput(e) {
  emit('update:modelValue', e.target.value)
}

/** Insert 4 spaces on Tab keypress — standard Python indent */
function onTab(e) {
  const el = e.target
  const start = el.selectionStart
  const end = el.selectionEnd
  const newVal = props.modelValue.substring(0, start) + '    ' + props.modelValue.substring(end)
  emit('update:modelValue', newVal)
  // Re-position cursor after the inserted spaces
  requestAnimationFrame(() => {
    el.selectionStart = start + 4
    el.selectionEnd = start + 4
  })
}
</script>

<style scoped>
.code-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.code-editor__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.code-editor__hint-toggle {
  font-size: 11px;
  padding: 5px 12px;
}

.code-editor__hint-toggle--open {
  border-color: #4FC3F7;
  color: #4FC3F7;
}

/* Hints panel */
.code-hints-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 12px;
}

.code-hints-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.code-hints-items {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.code-hints-method {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.code-hints-method__sig {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #4FC3F7;
  word-break: break-all;
}

.code-hints-method__desc {
  font-family: var(--font-mono);
  font-size: 11px;
  padding-left: 2px;
}

.code-hints-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.code-hints-chip {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--clr-text);
  background: var(--clr-panel-alt);
  border: 1px solid var(--clr-border);
  padding: 2px 8px;
}

.code-hints-chip--bar {
  color: var(--clr-orange);
  border-color: rgba(255, 107, 0, 0.3);
  background: rgba(255, 107, 0, 0.06);
}

.code-hints-note {
  margin-top: 4px;
}

/* Textarea with line numbers */
.code-editor__textarea-wrap {
  display: flex;
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb);
  background: #0a0a0a;
  transition: border-color 150ms, box-shadow 150ms;
  overflow: hidden;
}

.code-editor__textarea-wrap--valid {
  border-color: var(--clr-green);
  box-shadow: var(--shadow-nb-green);
}

.code-editor__textarea-wrap--invalid {
  border-color: var(--clr-red);
  box-shadow: var(--shadow-nb-red);
}

.code-editor__line-nums {
  display: flex;
  flex-direction: column;
  padding: 14px 10px;
  background: #111;
  border-right: 1px solid var(--clr-border);
  user-select: none;
  min-width: 44px;
  text-align: right;
  align-items: flex-end;
}

.code-editor__line-num {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--clr-text-dim);
}

.code-editor__textarea {
  flex: 1;
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
  tab-size: 4;
  white-space: pre;
  overflow-x: auto;
}

.code-editor__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10px;
}
</style>
