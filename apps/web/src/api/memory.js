import axios from 'axios'

const BASE = '/api/memories'

export const listMemories = (params = {}) =>
  axios.get(BASE, { params }).then(r => r.data)

export const getMemory = (memoryId) =>
  axios.get(`${BASE}/${memoryId}`).then(r => r.data)

export const createMemory = (payload) =>
  axios.post(BASE, payload).then(r => r.data)

export const getMemoryGraph = () =>
  axios.get(`${BASE}/graph`).then(r => r.data)
