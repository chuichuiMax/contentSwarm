import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const businessVariableApi = {
  listBusinessVariables: (params) => apiGet(`/api/content-business-variables${encodeQuery(params)}`),
  createBusinessVariable: (payload) => apiPost('/api/content-business-variables', payload),
  updateBusinessVariable: (itemId, payload) => apiPatch(`/api/content-business-variables/${itemId}`, payload),
  deleteBusinessVariable: (itemId) => apiDelete(`/api/content-business-variables/${itemId}`)
}
