<template>
  <div :class="['nb-panel', alt && 'nb-panel--alt']">
    <div v-if="title" class="nb-panel__header">
      <span class="nb-label">{{ title }}</span>
      <slot name="header-right" />
    </div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  /** Optional section title rendered as monospace label */
  title: {
    type: String,
    default: null,
  },
  /** Use slightly lighter alternate background */
  alt: {
    type: Boolean,
    default: false,
  },
})
</script>

<style scoped>
.nb-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--clr-border);
}
</style>
