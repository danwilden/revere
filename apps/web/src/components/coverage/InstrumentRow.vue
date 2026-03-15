<template>
  <div class="instrument-row">
    <div class="instr-meta">
      <span class="instr-id">{{ instrumentId.replace('_', '/') }}</span>
      <span :class="['instr-cat', `instr-cat--${category}`]">{{ category.toUpperCase() }}</span>
    </div>
    <div class="instr-content">
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  instrumentId: { type: String, required: true },
  category: { type: String, default: 'minor' },
})
</script>

<style scoped>
.instrument-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-family: var(--font-mono);
}

.instr-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100px;
  flex-shrink: 0;
}

.instr-id {
  font-size: 12px;
  font-weight: 700;
  color: var(--clr-text);
}

.instr-cat {
  font-size: 9px;
  letter-spacing: 0.1em;
  padding: 1px 4px;
  border: 1px solid;
  display: inline-block;
  width: fit-content;
}

.instr-cat--major {
  color: var(--clr-yellow);
  border-color: var(--clr-yellow);
}

.instr-cat--minor {
  color: var(--clr-text-dim);
  border-color: var(--clr-border);
}

.instr-content {
  flex: 1;
}
</style>
