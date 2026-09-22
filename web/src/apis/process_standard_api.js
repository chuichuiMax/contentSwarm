import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const processStandardApi = {
  listProcessStandards: (params) => apiGet(`/api/content-process-standards${encodeQuery(params)}`),
  createProcessStandard: (payload) => apiPost('/api/content-process-standards', payload),
  updateProcessStandard: (itemId, payload) => apiPatch(`/api/content-process-standards/${itemId}`, payload),
  deleteProcessStandard: (itemId) => apiDelete(`/api/content-process-standards/${itemId}`)
}
