import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const targetAudienceApi = {
  listTargetAudiences: (params) => apiGet(`/api/content-target-audiences${encodeQuery(params)}`),
  createTargetAudience: (payload) => apiPost('/api/content-target-audiences', payload),
  updateTargetAudience: (itemId, payload) => apiPatch(`/api/content-target-audiences/${itemId}`, payload),
  deleteTargetAudience: (itemId) => apiDelete(`/api/content-target-audiences/${itemId}`)
}
