<template>
  <div class="strategy-list">
    <div class="strategy-list__toolbar">
      <div class="strategy-list__count nb-label">
        {{ strategies.length }} STRATEG{{ strategies.length === 1 ? 'Y' : 'IES' }}
      </div>
      <button class="nb-btn nb-btn--primary strategy-list__create-btn" @click="emit('create')">
        + NEW STRATEGY
      </button>
    </div>

    <EmptyState
      v-if="strategies.length === 0"
      message="No strategies saved. Create your first strategy to get started."
    />

    <div v-else class="strategy-list__grid">
      <StrategyCard
        v-for="strategy in strategies"
        :key="strategy.id"
        :strategy="strategy"
        @view="emit('view', $event)"
        @edit="emit('edit', $event)"
        @validate="emit('validate', $event)"
      />
    </div>
  </div>
</template>

<script setup>
import EmptyState from '@/components/ui/EmptyState.vue'
import StrategyCard from './StrategyCard.vue'

defineProps({
  /** Array of strategy records from the store */
  strategies: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['create', 'view', 'edit', 'validate'])
</script>

<style scoped>
.strategy-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.strategy-list__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.strategy-list__create-btn {
  font-size: 12px;
  padding: 8px 18px;
}

.strategy-list__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 14px;
}
</style>
