<template>
  <div class="training-progress">
    <!-- Header row: label + status badge -->
    <div class="training-progress__header">
      <span class="nb-label">TRAINING JOB</span>
      <StatusBadge v-if="job?.status" :status="job.status" />
    </div>

    <!-- Progress bar — hidden when succeeded or no job -->
    <div v-if="showProgressBar" class="training-progress__bar-wrap">
      <div :class="['nb-progress', isRunning && 'nb-progress--indeterminate']">
        <div
          v-if="!isRunning && progressPct !== null"
          class="nb-progress__bar"
          :style="{ width: `${progressPct}%` }"
        />
        <div v-else-if="isRunning" class="nb-progress__bar" />
      </div>
      <div class="training-progress__bar-footer">
        <span class="nb-label" style="color: var(--clr-text-dim)">{{ progressLabel }}</span>
        <span v-if="progressPct !== null && isRunning" class="nb-value nb-value--sm nb-value--accent">
          {{ progressPct.toFixed(0) }}%
        </span>
      </div>
    </div>

    <!-- Stage label -->
    <div v-if="job?.stage_label" class="training-progress__stage">
      <span class="nb-label">STAGE&nbsp;</span>
      <span class="font-mono" style="font-size: 12px; color: var(--clr-yellow)">
        {{ job.stage_label.toUpperCase() }}
      </span>
    </div>

    <!-- Job ID (queued / running state) -->
    <div v-if="job?.id && !isSucceeded" class="training-progress__job-id">
      <span class="nb-label">JOB ID&nbsp;</span>
      <span class="font-mono text-dim" style="font-size: 11px">{{ job.id }}</span>
    </div>

    <!-- Success state -->
    <div v-if="isSucceeded" class="training-progress__success nb-banner nb-banner--info" style="margin-top: 12px">
      <div class="training-progress__success-row">
        <span class="text-green font-mono" style="font-size: 13px; font-weight: 700">TRAINING COMPLETE</span>
      </div>
      <div v-if="job?.result_ref" class="training-progress__model-id" style="margin-top: 6px">
        <span class="nb-label">MODEL ID&nbsp;</span>
        <span class="font-mono nb-value--accent" style="font-size: 12px">{{ job.result_ref }}</span>
      </div>
    </div>

    <!-- Error state -->
    <div
      v-if="isError && job?.error_message"
      class="nb-banner nb-banner--error"
      style="margin-top: 12px"
    >
      <div style="font-weight: 700; margin-bottom: 4px">TRAINING FAILED</div>
      {{ job.error_message }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

const props = defineProps({
  /** Job object — shape: { id, status, progress_pct, stage_label, error_message, result_ref } */
  job: {
    type: Object,
    default: null,
  },
})

const status = computed(() => props.job?.status?.toLowerCase())
const isQueued = computed(() => status.value === 'queued')
const isRunning = computed(() => status.value === 'running')
const isSucceeded = computed(() => status.value === 'succeeded')
const isError = computed(() => ['failed', 'cancelled'].includes(status.value))

const showProgressBar = computed(() => !!props.job && !isSucceeded.value)

const progressPct = computed(() => {
  const p = props.job?.progress_pct
  if (p !== undefined && p !== null) return Math.min(100, Math.max(0, p))
  return null
})

const progressLabel = computed(() => {
  if (isQueued.value) return 'QUEUED — WAITING FOR WORKER'
  if (isRunning.value) return 'RUNNING'
  if (isError.value) return status.value?.toUpperCase()
  return ''
})
</script>

<style scoped>
.training-progress {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.training-progress__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.training-progress__bar-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.training-progress__bar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.training-progress__stage {
  padding: 6px 0;
  border-top: 1px solid var(--clr-border);
}

.training-progress__job-id {
  padding: 4px 0;
}

.training-progress__success-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.training-progress__model-id {
  word-break: break-all;
}
</style>
