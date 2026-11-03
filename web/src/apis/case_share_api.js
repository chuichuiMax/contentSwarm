import { apiDelete, apiGet } from './base'

function query(params = {}) {
  const values = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') values.set(key, String(value))
  }
  return values.toString()
}

export const caseShareApi = {
  list: (params) => apiGet(`/api/case-shares?${query(params)}`),
  summary: () => apiGet('/api/case-shares/summary'),
  export: (params) => apiGet(`/api/case-shares/export?${query(params)}`, {}, true, 'blob'),
  delete: (id) => apiDelete(`/api/case-shares/${encodeURIComponent(id)}`)
}
