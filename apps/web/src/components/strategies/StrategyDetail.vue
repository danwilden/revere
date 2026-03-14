<template>
  <div class="strategy-detail">
    <!-- Toolbar -->
    <div class="detail-toolbar">
      <div class="detail-toolbar__left">
        <button class="nb-btn" @click="emit('back')">&larr; BACK</button>
        <span class="nb-heading nb-heading--md">{{ strategy.name }}</span>
      </div>
      <div class="detail-toolbar__right">
        <span
          class="status-chip"
          :class="strategy.strategy_type === 'rules' ? 'type-badge--rules' : 'type-badge--code'"
        >
          {{ strategy.strategy_type === 'rules' ? 'RULES' : 'CODE' }}
        </span>
        <button class="nb-btn nb-btn--primary" @click="emit('edit', strategy)">
          EDIT
        </button>
      </div>
    </div>

    <!-- Meta panel -->
    <div class="nb-panel detail-meta">
      <div class="detail-meta__row">
        <div class="detail-meta__field">
          <span class="nb-label">ID</span>
          <span class="nb-value nb-value--sm font-mono">{{ strategy.id }}</span>
        </div>
        <div class="detail-meta__field">
          <span class="nb-label">CREATED</span>
          <span class="nb-value nb-value--sm font-mono">{{ formatDate(strategy.created_at) }}</span>
        </div>
        <div class="detail-meta__field">
          <span class="nb-label">STATUS</span>
          <span class="nb-value nb-value--sm font-mono" :class="strategy.is_active ? 'text-green' : 'text-muted'">
            {{ strategy.is_active ? 'ACTIVE' : 'INACTIVE' }}
          </span>
        </div>
      </div>
    </div>

    <!-- Definition panel -->
    <NbCard :title="strategy.strategy_type === 'rules' ? 'RULES DEFINITION JSON' : 'PYTHON CODE'">
      <pre class="detail-definition font-mono">{{ formattedDefinition }}</pre>
    </NbCard>
  </div>
</template>

<script setup>
import NbCard from '@/components/ui/NbCard.vue'

const props = defineProps({
  /** Full strategy record */
  strategy: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['back', 'edit'])

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

const formattedDefinition = (() => {
  const def = props.strategy.definition_json
  if (!def) return '—'
  if (props.strategy.strategy_type === 'code') {
    return def.code ?? JSON.stringify(def, null, 2)
  }
  return JSON.stringify(def, null, 2)
})()
</script>

<style scoped>
.strategy-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 14px;
  border-bottom: 2px solid var(--clr-border);
}

.detail-toolbar__left {
  display: flex;
  align-items: center;
  gap: 14px;
}

.detail-toolbar__right {
  display: flex;
  align-items: center;
  gap: 10px;
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

.detail-meta {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-meta__row {
  display: flex;
  gap: 32px;
  flex-wrap: wrap;
}

.detail-meta__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-definition {
  background: #0a0a0a;
  border: 1px solid var(--clr-border);
  padding: 16px;
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--clr-text);
  overflow-x: auto;
  white-space: pre;
  max-height: 520px;
  overflow-y: auto;
}
</style>
