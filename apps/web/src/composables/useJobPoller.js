import { onUnmounted } from 'vue'
import { getJob } from '@/api/jobs.js'

const TERMINAL_STATUSES = ['succeeded', 'failed', 'cancelled']

/**
 * useJobPoller
 *
 * Polls GET /api/jobs/{jobId} at a fixed interval until the job reaches
 * a terminal state (succeeded | failed | cancelled), then stops.
 *
 * @param {string} jobId - ID of the job to poll
 * @param {Object} options
 * @param {Function} options.onProgress - called on every poll tick with the job object
 * @param {Function} options.onComplete - called when job.status === 'succeeded'
 * @param {Function} options.onError   - called when job.status === 'failed' | 'cancelled'
 * @param {number}   options.intervalMs - polling interval in ms (default 2500)
 *
 * @returns {{ stop: Function }} - call stop() to manually cancel polling
 *
 * Note: Job status values from the backend are lowercase:
 *   'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
 */
export function useJobPoller(jobId, { onProgress, onComplete, onError, intervalMs = 2500 } = {}) {
  let timer = null

  const stop = () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  const poll = async () => {
    try {
      const job = await getJob(jobId)

      if (onProgress) onProgress(job)

      if (TERMINAL_STATUSES.includes(job.status?.toLowerCase())) {
        stop()
        if (job.status?.toLowerCase() === 'succeeded') {
          if (onComplete) onComplete(job)
        } else {
          if (onError) onError(job)
        }
      }
    } catch (err) {
      // Surface errors but keep polling — transient failures shouldn't kill the poller
      if (onProgress) {
        onProgress({ _pollError: err.message || 'Poll failed' })
      }
    }
  }

  // Start polling immediately, then on interval
  poll()
  timer = setInterval(poll, intervalMs)

  // Auto-cleanup when the component using this composable unmounts
  onUnmounted(stop)

  return { stop }
}
