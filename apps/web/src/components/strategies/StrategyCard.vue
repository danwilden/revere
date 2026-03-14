<template>
  <div class="strategy-card" :class="typeClass">
    <div class="strategy-card__header">
      <div class="strategy-card__meta">
        <span
          class="strategy-card__type-badge status-chip"
          :class="strategy.strategy_type === 'rules' ? 'type-badge--rules' : 'type-badge--code'"
        >
          {{ strategy.strategy_type === 'rules' ? 'RULES' : 'CODE' }}
        </span>
        <span v-if="strategy.is_active" class="strategy-card__active-flag">
          <span class="status-dot" style="background: var(--clr-green);" />
          ACTIVE
        </span>
      </div>
      <span class="nb-label strategy-card__date">
        {{ formatDate(strategy.created_at) }}
      </span>
    </div>

    <h3 class="strategy-card__name nb-heading nb-heading--md">{{ strategy.name }}</h3>

    <div class="strategy-card__id nb-label text-dim">
      ID: {{ strategy.id }}
    </div>

    <div class="strategy-card__actions">
      <button class="nb-btn strategy-card__btn" @click="emit('view', strategy)">
        VIEW
      </button>
      <button class="nb-btn strategy-card__btn" @click="emit('edit', strategy)">
        EDIT
      </button>
      <button
        class="nb-btn strategy-card__btn"
        :class="{ 'nb-btn--primary': true }"
        @click="emit('validate', strategy)"
      >
        VALIDATE
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** Strategy record from GET /api/strategies */
  strategy: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['view', 'edit', 'validate'])

const typeClass = computed(() =>
  props.strategy.strategy_type === 'rules'
    ? 'strategy-card--rules'
    : 'strategy-card--code'
)

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-GB', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}
</script>

<style scoped>
.strategy-card {
  background: var(--clr-surface);
  border: 2px solid var(--clr-border);
  box-shadow: var(--shadow-nb);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color 100ms;
}

.strategy-card--rules {
  border-left: 4px solid var(--clr-yellow);
}

.strategy-card--code {
  border-left: 4px solid #4FC3F7;
}

.strategy-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.strategy-card__meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.strategy-card__type-badge {
  font-size: 10px;
  letter-spacing: 0.12em;
}

.type-badge--rules {
  border-color: var(--clr-yellow);
  color: var(--clr-yellow);
  background: rgba(255, 230, 0, 0.08);
}

.type-badge--code {
  border-color: #4FC3F7;
  color: #4FC3F7;
  background: rgba(79, 195, 247, 0.08);
}

.strategy-card__active-flag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  color: var(--clr-green);
}

.strategy-card__date {
  font-size: 10px;
}

.strategy-card__name {
  margin: 0;
  line-height: 1.2;
}

.strategy-card__id {
  font-size: 10px;
  letter-spacing: 0.05em;
  word-break: break-all;
}

.strategy-card__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 6px;
  border-top: 1px solid var(--clr-border);
}

.strategy-card__btn {
  font-size: 11px;
  padding: 6px 14px;
}
</style>
