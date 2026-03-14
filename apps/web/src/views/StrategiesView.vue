<template>
  <div class="view-container">
    <header class="view-header">
      <div class="view-header__title">
        <span class="nb-label">MODULE</span>
        <h1 class="nb-heading nb-heading--xl">STRATEGIES</h1>
      </div>
      <div class="view-header__right">
        <span v-if="store.editorMode !== 'list'" class="view-header__mode-badge">
          {{ modeBadgeLabel }}
        </span>
        <span class="view-header__badge">PHASE 6C</span>
      </div>
    </header>

    <!-- Loading state on initial fetch -->
    <div v-if="store.loading && store.strategies.length === 0 && store.editorMode === 'list'" class="view-body">
      <LoadingState message="LOADING STRATEGIES..." />
    </div>

    <!-- Top-level fetch error -->
    <ErrorBanner
      v-else-if="store.error && store.editorMode === 'list'"
      :message="store.error"
    />

    <div v-else class="view-body">
      <!-- LIST MODE -->
      <template v-if="store.editorMode === 'list'">
        <NbCard title="SAVED STRATEGIES">
          <StrategyList
            :strategies="store.strategies"
            @create="handleCreate"
            @view="handleView"
            @edit="handleEdit"
            @validate="handleValidateFromList"
          />
        </NbCard>

        <!-- Inline validation result on list page (after validating from a card) -->
        <div
          v-if="listValidationResult"
          class="list-validation nb-card"
          :class="listValidationResult.valid ? 'nb-card--success' : 'nb-card--error'"
        >
          <div class="list-validation__header">
            <div class="list-validation__title">
              <span class="nb-label">VALIDATION — {{ listValidationTarget?.name }}</span>
              <span
                class="status-chip"
                :class="listValidationResult.valid ? 'status-chip--succeeded' : 'status-chip--failed'"
              >
                <span class="status-dot" />
                {{ listValidationResult.valid ? 'VALID' : 'INVALID' }}
              </span>
            </div>
            <button class="nb-btn" style="font-size: 10px; padding: 4px 10px;" @click="listValidationResult = null">
              DISMISS
            </button>
          </div>
          <ul v-if="listValidationResult.errors?.length" class="list-validation__errors">
            <li
              v-for="(err, i) in listValidationResult.errors"
              :key="i"
              class="list-validation__error-item font-mono text-red"
            >
              {{ err }}
            </li>
          </ul>
          <span v-else-if="listValidationResult.valid" class="nb-label text-green">
            All checks passed.
          </span>
        </div>
      </template>

      <!-- CREATE / EDIT MODE -->
      <template v-else-if="store.editorMode === 'create' || store.editorMode === 'edit'">
        <StrategyEditor
          :strategy="store.activeStrategy"
          @back="handleBackToList"
          @saved="handleSaved"
        />
      </template>

      <!-- DETAIL / READ-ONLY MODE -->
      <template v-else-if="store.editorMode === 'detail'">
        <StrategyDetail
          :strategy="store.activeStrategy"
          @back="handleBackToList"
          @edit="handleEdit(store.activeStrategy)"
        />
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useStrategiesStore } from '@/stores/strategies.js'
import NbCard from '@/components/ui/NbCard.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ErrorBanner from '@/components/ui/ErrorBanner.vue'
import StrategyList from '@/components/strategies/StrategyList.vue'
import StrategyEditor from '@/components/strategies/StrategyEditor.vue'
import StrategyDetail from '@/components/strategies/StrategyDetail.vue'

const store = useStrategiesStore()

// ---------------------------------------------------------------------------
// List-page validation state (when user clicks Validate from a StrategyCard)
// ---------------------------------------------------------------------------
const listValidationResult = ref(null)
const listValidationTarget = ref(null)
const isValidatingFromList = ref(false)

// ---------------------------------------------------------------------------
// Mode badge label
// ---------------------------------------------------------------------------
const modeBadgeLabel = computed(() => {
  const map = {
    create: 'NEW',
    edit: 'EDITING',
    detail: 'VIEWING',
  }
  return map[store.editorMode] ?? store.editorMode.toUpperCase()
})

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
onMounted(() => {
  store.fetchStrategies()
})

// ---------------------------------------------------------------------------
// Navigation handlers
// ---------------------------------------------------------------------------

function handleCreate() {
  store.setEditorMode('create')
}

function handleEdit(strategy) {
  store.activeStrategy = strategy
  store.strategyType = strategy.strategy_type
  store.setEditorMode('edit')
}

function handleView(strategy) {
  store.activeStrategy = strategy
  store.setEditorMode('detail')
}

function handleBackToList() {
  store.setEditorMode('list')
  store.clearError()
  // Refresh list to pick up any saves
  store.fetchStrategies()
}

function handleSaved(strategy) {
  // Stay in edit mode so user can validate; the editor shows the success banner
  store.activeStrategy = strategy
  store.setEditorMode('edit')
}

// ---------------------------------------------------------------------------
// Validate-from-list — runs validation without entering edit mode
// ---------------------------------------------------------------------------
async function handleValidateFromList(strategy) {
  listValidationResult.value = null
  listValidationTarget.value = strategy
  isValidatingFromList.value = true
  try {
    const result = await store.validateStrategy(strategy.id)
    listValidationResult.value = result
  } catch {
    listValidationResult.value = { valid: false, errors: [store.error ?? 'Validation failed'] }
  } finally {
    isValidatingFromList.value = false
  }
}
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

.view-header__right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.view-header__mode-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.15em;
  color: var(--clr-yellow);
  border: 1px solid var(--clr-yellow);
  padding: 4px 10px;
  text-transform: uppercase;
  background: rgba(255, 230, 0, 0.06);
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
}

/* List-page validation result */
.list-validation {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.list-validation__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.list-validation__title {
  display: flex;
  align-items: center;
  gap: 12px;
}

.list-validation__errors {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.list-validation__error-item {
  font-size: 12px;
  line-height: 1.5;
}
</style>
