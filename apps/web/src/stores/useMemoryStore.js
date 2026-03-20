import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { listMemories, getMemory, createMemory, getMemoryGraph } from '@/api/memory.js'

export const useMemoryStore = defineStore('memory', () => {
  const memories = ref([])
  const graphData = ref({ nodes: [], edges: [], stats: {} })
  const selectedMemoryId = ref(null)
  const filters = ref({ instrument: null, outcome: null, tags: [] })
  const loading = ref(false)
  const error = ref(null)

  const selectedMemory = computed(() =>
    memories.value.find(m => m.id === selectedMemoryId.value) || null
  )

  async function fetchMemories(params = {}) {
    loading.value = true
    error.value = null
    try {
      const data = await listMemories(params)
      memories.value = Array.isArray(data) ? data : (data.memories || [])
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  async function fetchGraph() {
    loading.value = true
    error.value = null
    try {
      graphData.value = await getMemoryGraph()
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  async function saveManualMemory(payload) {
    const memory = await createMemory(payload)
    memories.value.unshift(memory)
    return memory
  }

  function selectNode(nodeId) {
    // nodeId may be "mem_<uuid>" or "exp_<uuid>"
    if (nodeId && nodeId.startsWith('mem_')) {
      selectedMemoryId.value = nodeId.replace('mem_', '')
    } else {
      selectedMemoryId.value = null
    }
  }

  return {
    memories, graphData, selectedMemoryId, selectedMemory,
    filters, loading, error,
    fetchMemories, fetchGraph, saveManualMemory, selectNode,
  }
})
