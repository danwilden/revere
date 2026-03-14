<template>
  <div class="empty-state">
    <div class="empty-state__icon">[ ]</div>
    <span class="nb-label empty-state__message">{{ message }}</span>
  </div>
</template>

<script setup>
defineProps({
  message: {
    type: String,
    default: 'NO DATA',
  },
})
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  gap: 12px;
}

.empty-state__icon {
  font-family: var(--font-mono);
  font-size: 36px;
  color: var(--clr-text-dim);
  line-height: 1;
}

.empty-state__message {
  letter-spacing: 0.15em;
  color: var(--clr-text-muted);
}
</style>
