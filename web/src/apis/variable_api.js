import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const variableApi = {
  listVariables: (params) => apiGet(`/api/content-variables${encodeQuery(params)}`),
  createVariable: (payload) => apiPost('/api/content-variables', payload),
  updateVariable: (variablePk, payload) => apiPatch(`/api/content-variables/${variablePk}`, payload),
  deleteVariable: (variablePk) => apiDelete(`/api/content-variables/${variablePk}`)
}
