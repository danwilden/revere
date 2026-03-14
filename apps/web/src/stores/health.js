import { defineStore } from 'pinia'
import { ref } from 'vue'
import apiClient from '@/api/client.js'

/**
 * healthStore — tracks backend connectivity status.
 * Used by AppShell status bar.
 */
export const useHealthStore = defineStore('health', () => {
  const status = ref('unknown')   // 'unknown' | 'online' | 'offline'
  const lastChecked = ref(null)

  async function check() {
    try {
      await apiClient.get('/health')
      status.value = 'online'
    } catch {
      status.value = 'offline'
    } finally {
      lastChecked.value = new Date().toISOString()
    }
  }

  return { status, lastChecked, check }
})
