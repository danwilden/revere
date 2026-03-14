<template>
  <div v-if="message" class="nb-banner nb-banner--error error-banner">
    <span class="error-banner__prefix">ERR //</span>
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
.error-banner {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.error-banner__prefix {
  color: var(--clr-red);
  font-weight: 700;
  white-space: nowrap;
  flex-shrink: 0;
}
</style>
