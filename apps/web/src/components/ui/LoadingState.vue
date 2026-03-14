<template>
  <div class="loading-state">
    <div class="loading-state__inner">
      <div class="loading-state__bars">
        <span v-for="i in 8" :key="i" class="loading-bar" :style="barStyle(i)" />
      </div>
      <span class="nb-label loading-state__label">{{ message }}</span>
    </div>
  </div>
</template>

<script setup>
defineProps({
  message: {
    type: String,
    default: 'LOADING...',
  },
})

function barStyle(i) {
  return {
    animationDelay: `${(i - 1) * 0.1}s`,
  }
}
</script>

<style scoped>
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  width: 100%;
}

.loading-state__inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.loading-state__bars {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 32px;
}

.loading-bar {
  display: block;
  width: 6px;
  background: var(--clr-yellow);
  animation: bar-bounce 0.8s ease-in-out infinite alternate;
}

@keyframes bar-bounce {
  from { height: 6px; opacity: 0.3; }
  to { height: 32px; opacity: 1; }
}

.loading-state__label {
  letter-spacing: 0.2em;
}
</style>
