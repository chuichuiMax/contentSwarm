import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const creativeStyleApi = {
  listCreativeStyles: (params) => apiGet(`/api/creative-styles${encodeQuery(params)}`),
  createCreativeStyle: (payload) => apiPost('/api/creative-styles', payload),
  updateCreativeStyle: (styleId, payload) => apiPatch(`/api/creative-styles/${styleId}`, payload),
  deleteCreativeStyle: (styleId) => apiDelete(`/api/creative-styles/${styleId}`)
}
