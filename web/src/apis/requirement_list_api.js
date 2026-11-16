import { apiDelete, apiGet, apiPost } from './base'

function query(params = {}) {
  const values = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') values.set(key, String(value))
  }
  const text = values.toString()
  return text ? `?${text}` : ''
}

export const requirementListApi = {
  list: (params) => apiGet(`/api/requirement-lists${query(params)}`),
  detail: (id) => apiGet(`/api/requirement-lists/${encodeURIComponent(id)}`),
  create: (payload) => apiPost('/api/requirement-lists', payload),
  submitPublic: (payload) => apiPost('/api/requirement-lists/public', payload, {}, false),
  remove: (id) => apiDelete(`/api/requirement-lists/${encodeURIComponent(id)}`),
  export: (params) => apiGet(`/api/requirement-lists/export${query(params)}`, {}, true, 'blob')
}
