import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const employeeApi = {
  exportEmployees: () => apiGet('/api/employees/export', {}, true, 'blob'),
  getMyEmployee: () => apiGet('/api/employees/me'),
  listEmployees: (params) => apiGet(`/api/employees${encodeQuery(params)}`),
  listTrafficStats: (params) => apiGet(`/api/employees/traffic${encodeQuery(params)}`),
  exportTrafficStats: (params) => apiGet(`/api/employees/traffic/export${encodeQuery(params)}`, {}, true, 'blob'),
  createEmployee: (payload) => apiPost('/api/employees', payload),
  updateEmployee: (employeePk, payload) => apiPatch(`/api/employees/${employeePk}`, payload),
  deleteEmployee: (employeePk) => apiDelete(`/api/employees/${employeePk}`)
}
