<template>
  <div class="strategy-editor">
    <!-- Editor toolbar -->
    <div class="editor-toolbar">
      <div class="editor-toolbar__left">
        <button class="nb-btn editor-toolbar__back" @click="emit('back')">
          &larr; BACK
        </button>
        <span class="nb-heading nb-heading--md editor-toolbar__title">
          {{ isNew ? 'NEW STRATEGY' : 'EDIT STRATEGY' }}
        </span>
      </div>

      <div class="editor-toolbar__right">
        <button
          class="nb-btn"
          :disabled="!canValidate || isSaving"
          @click="handleValidate"
        >
          VALIDATE
        </button>
        <button
          class="nb-btn nb-btn--primary"
          :disabled="!canSave || isSaving"
          @click="handleSave"
        >
          {{ isSaving ? 'SAVING...' : 'SAVE STRATEGY' }}
        </button>
      </div>
    </div>

    <!-- Error banner -->
    <ErrorBanner :message="store.error" />

    <!-- Save success banner -->
    <div v-if="saveSuccess" class="nb-banner nb-banner--info save-success">
      STRATEGY SAVED — ID: {{ savedId }}
    </div>

    <!-- Name + type selector row -->
    <div class="editor-meta nb-panel">
      <div class="editor-meta__name">
        <label class="nb-label editor-meta__label">STRATEGY NAME</label>
        <input
          v-model="nameInput"
          class="editor-meta__input font-mono"
          type="text"
          placeholder="e.g. RSI Oversold + ADX Trend Filter"
          maxlength="120"
        />
        <span v-if="nameError" class="editor-meta__field-error nb-label text-red">
          {{ nameError }}
        </span>
      </div>

      <div class="editor-meta__type">
        <span class="nb-label editor-meta__label">STRATEGY TYPE</span>
        <div class="type-toggle">
          <button
            class="nb-btn type-toggle__btn"
            :class="{ 'type-toggle__btn--active type-toggle__btn--rules': currentType === 'rules_engine' }"
            @click="switchType('rules_engine')"
          >
            RULES
          </button>
          <button
            class="nb-btn type-toggle__btn"
            :class="{ 'type-toggle__btn--active type-toggle__btn--code': currentType === 'python' }"
            @click="switchType('python')"
          >
            CODE
          </button>
        </div>
      </div>
    </div>

    <!-- Validation result panel -->
    <div v-if="store.validationResult" class="validation-result" :class="validationResultClass">
      <div class="validation-result__header">
        <span
          class="status-chip"
          :class="store.validationResult.valid ? 'status-chip--succeeded' : 'status-chip--failed'"
        >
          <span class="status-dot" />
          {{ store.validationResult.valid ? 'VALID' : 'INVALID' }}
        </span>
        <button class="nb-btn validation-result__dismiss" @click="store.clearValidation()">
          DISMISS
        </button>
      </div>
      <ul v-if="store.validationResult.errors.length" class="validation-result__errors">
        <li
          v-for="(err, i) in store.validationResult.errors"
          :key="i"
          class="validation-result__error-item font-mono text-red"
        >
          {{ err }}
        </li>
      </ul>
      <span v-else-if="store.validationResult.valid" class="nb-label text-green">
        All checks passed. Strategy definition is structurally valid.
      </span>
    </div>

    <!-- Validate-without-save notice -->
    <div v-if="isNew && !savedId" class="nb-banner nb-banner--warning validate-notice">
      WARN // Save first to run backend validation. JSON parse errors are shown inline.
    </div>

    <!-- Editor body -->
    <div class="editor-body">
      <RulesEditor
        v-if="currentType === 'rules_engine'"
        v-model="rulesJson"
        :validation-state="editorValidationState"
      />
      <CodeEditor
        v-else
        v-model="codeText"
        :validation-state="editorValidationState"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useStrategiesStore } from '@/stores/strategies.js'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import RulesEditor from './RulesEditor.vue'
import CodeEditor from './CodeEditor.vue'

const DEFAULT_RULES_JSON = JSON.stringify(
  {
    entry_long: { all: [{ field: 'rsi_14', op: 'lt', value: 30 }] },
    entry_short: { any: [{ field: 'adx_14', op: 'gt', value: 25 }] },
    exit: { field: 'rsi_14', op: 'gt', value: 60 },
    stop_atr_multiplier: 1.5,
    take_profit_atr_multiplier: 2.0,
    position_size_units: 10000,
    named_conditions: {},
  },
  null,
  2
)

const DEFAULT_CODE = `class MyStrategy(BaseStrategy):
    def should_enter_long(self, bar, features, state, equity):
        return features.get('rsi_14', 50) < 30

    def should_enter_short(self, bar, features, state, equity):
        return False

    def should_exit(self, bar, features, state, equity):
        return features.get('rsi_14', 50) > 60
`

const props = defineProps({
  /**
   * Strategy to edit. Null when creating new.
   * Shape: { id, name, strategy_type, definition_json, ... }
   */
  strategy: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['back', 'saved'])

const store = useStrategiesStore()

// ---------------------------------------------------------------------------
// Local form state
// ---------------------------------------------------------------------------

const nameInput = ref('')
const nameError = ref(null)
const currentType = ref('rules_engine')
const rulesJson = ref(DEFAULT_RULES_JSON)
const codeText = ref(DEFAULT_CODE)
const isSaving = ref(false)
const saveSuccess = ref(false)
const savedId = ref(null)

const isNew = computed(() => !props.strategy)

// ---------------------------------------------------------------------------
// Populate form when editing an existing strategy
// ---------------------------------------------------------------------------

onMounted(() => {
  if (props.strategy) {
    nameInput.value = props.strategy.name ?? ''
    currentType.value = props.strategy.strategy_type ?? 'rules_engine'
    const def = props.strategy.definition_json ?? {}
    if (currentType.value === 'rules_engine') {
      rulesJson.value = JSON.stringify(def, null, 2)
    } else {
      codeText.value = def.code ?? DEFAULT_CODE
    }
  }
})

// ---------------------------------------------------------------------------
// Type toggle
// ---------------------------------------------------------------------------

function switchType(type) {
  if (type === currentType.value) return
  currentType.value = type
  store.clearValidation()
  store.clearError()
  // Reset to defaults when switching — prevents stale DSL leaking into wrong editor
  if (type === 'rules_engine') {
    rulesJson.value = DEFAULT_RULES_JSON
  } else {
    codeText.value = DEFAULT_CODE
  }
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

const canSave = computed(() => nameInput.value.trim().length > 0)

function buildPayload() {
  let definition_json
  if (currentType.value === 'rules_engine') {
    try {
      definition_json = JSON.parse(rulesJson.value)
    } catch {
      return null
    }
  } else {
    definition_json = { code: codeText.value }
  }
  return {
    name: nameInput.value.trim(),
    strategy_type: currentType.value,
    definition_json,
  }
}

async function handleSave() {
  nameError.value = null
  store.clearError()

  if (!nameInput.value.trim()) {
    nameError.value = 'Name is required'
    return
  }

  const payload = buildPayload()
  if (!payload) {
    store.error = 'Rules JSON is not valid — fix parse errors before saving'
    return
  }

  isSaving.value = true
  saveSuccess.value = false
  try {
    const created = await store.saveStrategy(payload)
    savedId.value = created.id
    saveSuccess.value = true
    emit('saved', created)
  } catch {
    // store.error is already set by the store action
  } finally {
    isSaving.value = false
  }
}

// ---------------------------------------------------------------------------
// Validate
// ---------------------------------------------------------------------------

// Can only validate if strategy is already saved (has an id)
const canValidate = computed(() => {
  const id = props.strategy?.id ?? savedId.value
  return !!id
})

async function handleValidate() {
  const id = props.strategy?.id ?? savedId.value
  if (!id) return
  store.clearError()
  try {
    await store.validateStrategy(id)
  } catch {
    // store.error set by store
  }
}

// ---------------------------------------------------------------------------
// Validation state for editor border styling
// ---------------------------------------------------------------------------

const editorValidationState = computed(() => {
  if (!store.validationResult) return null
  return store.validationResult.valid ? 'valid' : 'invalid'
})

const validationResultClass = computed(() => {
  if (!store.validationResult) return ''
  return store.validationResult.valid
    ? 'validation-result--valid'
    : 'validation-result--invalid'
})

// Clear success banner after 6 seconds
watch(saveSuccess, (val) => {
  if (val) setTimeout(() => { saveSuccess.value = false }, 6000)
})
</script>

<style scoped>
.strategy-editor {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* Toolbar */
.editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 14px;
  border-bottom: 2px solid var(--clr-border);
}

.editor-toolbar__left {
  display: flex;
  align-items: center;
  gap: 14px;
}

.editor-toolbar__back {
  font-size: 12px;
  padding: 6px 14px;
}

.editor-toolbar__title {
  margin: 0;
}

.editor-toolbar__right {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* Meta row */
.editor-meta {
  display: flex;
  align-items: flex-end;
  gap: 24px;
  flex-wrap: wrap;
}

.editor-meta__name {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 280px;
}

.editor-meta__label {
  font-size: 10px;
}

.editor-meta__input {
  background: #0a0a0a;
  border: 2px solid var(--clr-border);
  color: var(--clr-text);
  font-size: 14px;
  padding: 9px 12px;
  box-shadow: var(--shadow-nb-sm);
  outline: none;
  transition: border-color 100ms;
  width: 100%;
}

.editor-meta__input:focus {
  border-color: var(--clr-yellow);
}

.editor-meta__input::placeholder {
  color: var(--clr-text-dim);
}

.editor-meta__field-error {
  font-size: 11px;
}

.editor-meta__type {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* Type toggle */
.type-toggle {
  display: flex;
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb-sm);
}

.type-toggle__btn {
  border: none;
  box-shadow: none;
  font-size: 12px;
  padding: 8px 20px;
  border-radius: 0;
}

.type-toggle__btn--active.type-toggle__btn--rules {
  background: rgba(255, 230, 0, 0.12);
  color: var(--clr-yellow);
  border-bottom: 2px solid var(--clr-yellow);
}

.type-toggle__btn--active.type-toggle__btn--code {
  background: rgba(79, 195, 247, 0.1);
  color: #4FC3F7;
  border-bottom: 2px solid #4FC3F7;
}

/* Validation result */
.validation-result {
  padding: 12px 16px;
  border: 2px solid var(--clr-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.validation-result--valid {
  border-color: var(--clr-green);
  background: rgba(0, 255, 65, 0.05);
  box-shadow: var(--shadow-nb-green);
}

.validation-result--invalid {
  border-color: var(--clr-red);
  background: rgba(255, 34, 34, 0.05);
  box-shadow: var(--shadow-nb-red);
}

.validation-result__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.validation-result__dismiss {
  font-size: 10px;
  padding: 4px 10px;
  opacity: 0.7;
}

.validation-result__errors {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.validation-result__error-item {
  font-size: 12px;
  line-height: 1.5;
}

/* Validate notice */
.validate-notice {
  font-size: 11px;
}

/* Save success */
.save-success {
  font-family: var(--font-mono);
  font-size: 12px;
}

/* Editor body */
.editor-body {
  /* RulesEditor and CodeEditor are full-width */
}
</style>
