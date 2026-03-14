import axios from 'axios'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30000,
})

// Response interceptor — normalize errors to { message, detail, status }
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const normalized = {
      message: 'An unexpected error occurred.',
      detail: null,
      status: null,
    }

    if (error.response) {
      normalized.status = error.response.status
      const data = error.response.data

      if (data?.detail) {
        // FastAPI validation errors are arrays of objects
        if (Array.isArray(data.detail)) {
          normalized.detail = data.detail.map((e) => e.msg || JSON.stringify(e)).join('; ')
        } else {
          normalized.detail = String(data.detail)
        }
        normalized.message = normalized.detail
      } else if (data?.message) {
        normalized.message = data.message
        normalized.detail = data.message
      } else {
        normalized.message = `HTTP ${normalized.status}`
      }
    } else if (error.request) {
      normalized.message = 'No response from server. Check that the backend is running.'
      normalized.detail = 'Network error or server unreachable.'
    } else {
      normalized.message = error.message || 'Request setup error.'
    }

    const enrichedError = new Error(normalized.message)
    enrichedError.normalized = normalized
    enrichedError.original = error
    return Promise.reject(enrichedError)
  }
)

export default apiClient
