import { apiDelete, apiGet } from './base'

function query(params = {}) {
  const values = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') values.set(key, String(value))
  }
  return values.toString()
}

export const customerLeadApi = {
  list: (params) => apiGet(`/api/customer-leads?${query(params)}`),
  summary: () => apiGet('/api/customer-leads/summary'),
  phone: (id) => apiGet(`/api/customer-leads/${encodeURIComponent(id)}/phone`),
  export: (params) => apiGet(`/api/customer-leads/export?${query(params)}`, {}, true, 'blob'),
  delete: (id) => apiDelete(`/api/customer-leads/${encodeURIComponent(id)}`)
}
