import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const contentTypeApi = {
  listContentTypes: (params) => apiGet(`/api/content-types${encodeQuery(params)}`),
  createContentType: (payload) => apiPost('/api/content-types', payload),
  updateContentType: (typePk, payload) => apiPatch(`/api/content-types/${typePk}`, payload),
  deleteContentType: (typePk) => apiDelete(`/api/content-types/${typePk}`)
}
