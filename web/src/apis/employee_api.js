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
  listEmployees: (params) => apiGet(`/api/employees${encodeQuery(params)}`),
  createEmployee: (payload) => apiPost('/api/employees', payload),
  updateEmployee: (employeePk, payload) => apiPatch(`/api/employees/${employeePk}`, payload),
  deleteEmployee: (employeePk) => apiDelete(`/api/employees/${employeePk}`)
}
