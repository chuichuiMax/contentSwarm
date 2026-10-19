import { apiDelete, apiGet } from './base'

export const customerLeadApi = {
  list: (page = 1, pageSize = 20) => apiGet(`/api/customer-leads?page=${page}&page_size=${pageSize}`),
  avatar: (id) => apiGet(`/api/customer-leads/${encodeURIComponent(id)}/avatar`, {}, true, 'blob'),
  delete: (id) => apiDelete(`/api/customer-leads/${encodeURIComponent(id)}`)
}
