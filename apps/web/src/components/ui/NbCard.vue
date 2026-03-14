<template>
  <div
    :class="[
      'nb-card',
      accent === 'yellow' && 'nb-card--accent',
      accent === 'green' && 'nb-card--success',
      accent === 'red' && 'nb-card--error',
    ]"
    :style="accentStyle"
  >
    <div v-if="title" class="nb-card__header">
      <span class="nb-label">{{ title }}</span>
      <slot name="header-right" />
    </div>
    <slot />
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** Optional card title — rendered as a monospace section label */
  title: {
    type: String,
    default: null,
  },
  /**
   * Accent color preset: 'yellow' | 'green' | 'red' | null
   * Or pass any CSS color string for a custom border accent.
   */
  accent: {
    type: String,
    default: null,
  },
})

const PRESETS = new Set(['yellow', 'green', 'red', null])

const accentStyle = computed(() => {
  if (!props.accent || PRESETS.has(props.accent)) return {}
  return {
    borderColor: props.accent,
    boxShadow: `4px 4px 0px ${props.accent}`,
  }
})
</script>

<style scoped>
.nb-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--clr-border);
}
</style>
