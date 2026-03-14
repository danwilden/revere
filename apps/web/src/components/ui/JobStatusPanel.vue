<template>
  <div class="nb-panel job-status-panel">
    <div class="job-status-panel__header">
      <span class="nb-label">{{ title }}</span>
      <StatusBadge v-if="job?.status" :status="job.status" />
    </div>

    <!-- Progress bar -->
    <div v-if="showProgress" class="job-status-panel__progress">
      <div
        :class="['nb-progress', isRunning && 'nb-progress--indeterminate']"
      >
        <div
          v-if="!isRunning && progressPct !== null"
          class="nb-progress__bar"
          :style="{ width: `${progressPct}%` }"
        />
        <div
          v-else-if="isRunning"
          class="nb-progress__bar"
        />
      </div>
      <span class="nb-label" style="margin-top: 6px">
        {{ progressLabel }}
      </span>
    </div>

    <!-- Stage label -->
    <div v-if="job?.stage" class="job-status-panel__stage">
      <span class="nb-label">STAGE: </span>
      <span class="font-mono" style="font-size: 12px; color: var(--clr-yellow)">
        {{ job.stage }}
      </span>
    </div>

    <!-- Cancel button — visible while job is queued or running -->
    <div v-if="canCancel" style="margin-top: 12px">
      <button
        class="nb-btn nb-btn--danger"
        :disabled="cancelling"
        @click="emit('cancel')"
      >
        {{ cancelling ? 'CANCELLING...' : 'CANCEL JOB' }}
      </button>
    </div>

    <!-- Error message -->
    <div v-if="isError && job?.error_message" class="job-status-panel__error nb-banner nb-banner--error" style="margin-top: 12px">
      {{ job.error_message }}
    </div>

    <!-- Slot for additional content (completion summary etc.) -->
    <slot />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  job: {
    type: Object,
    default: null,
  },
  title: {
    type: String,
    default: 'JOB STATUS',
  },
  cancelling: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['cancel'])

const status = computed(() => props.job?.status?.toLowerCase())
const isRunning = computed(() => status.value === 'running')
const isQueued = computed(() => status.value === 'queued')
const isError = computed(() => ['failed', 'cancelled'].includes(status.value))
const showProgress = computed(() => !!props.job && !['succeeded'].includes(status.value))
const canCancel = computed(() => isRunning.value || isQueued.value)

const progressPct = computed(() => {
  if (props.job?.progress_pct !== undefined && props.job.progress_pct !== null) {
    return Math.min(100, Math.max(0, props.job.progress_pct))
  }
  return null
})

const progressLabel = computed(() => {
  if (isQueued.value) return 'QUEUED — waiting for worker...'
  if (isRunning.value) {
    if (progressPct.value !== null) return `${progressPct.value.toFixed(0)}% complete`
    return 'RUNNING...'
  }
  if (isError.value) return status.value?.toUpperCase()
  return ''
})
</script>

<style scoped>
.job-status-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.job-status-panel__progress {
  display: flex;
  flex-direction: column;
  margin-bottom: 10px;
}

.job-status-panel__stage {
  margin-top: 8px;
}
</style>
