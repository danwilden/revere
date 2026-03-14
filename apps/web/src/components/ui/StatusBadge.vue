<template>
  <span :class="['status-chip', `status-chip--${normalizedStatus}`]">
    <span class="status-dot" />
    {{ label }}
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: {
    type: String,
    required: true,
  },
})

const normalizedStatus = computed(() =>
  (props.status || 'unknown').toLowerCase()
)

const LABELS = {
  queued: 'QUEUED',
  running: 'RUNNING',
  succeeded: 'DONE',
  failed: 'FAILED',
  cancelled: 'CANCELLED',
  online: 'ONLINE',
  offline: 'OFFLINE',
  unknown: '—',
}

const label = computed(
  () => LABELS[normalizedStatus.value] ?? normalizedStatus.value.toUpperCase()
)
</script>
