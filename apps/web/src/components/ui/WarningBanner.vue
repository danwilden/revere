<template>
  <div v-if="message" class="nb-banner nb-banner--warning warning-banner">
    <span class="warning-banner__prefix">WARN //</span>
    {{ message }}
  </div>
</template>

<script setup>
defineProps({
  message: {
    type: String,
    default: null,
  },
})
</script>

<style scoped>
.warning-banner {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.warning-banner__prefix {
  color: var(--clr-orange);
  font-weight: 700;
  white-space: nowrap;
  flex-shrink: 0;
}
</style>
