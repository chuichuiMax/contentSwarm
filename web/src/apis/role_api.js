import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const roleApi = {
  listRoles: (params) => apiGet(`/api/roles${encodeQuery(params)}`),
  listRoleEmployees: (rolePk, params) => apiGet(`/api/roles/${rolePk}/employees${encodeQuery(params)}`),
  getRolePermissions: (rolePk) => apiGet(`/api/roles/${rolePk}/permissions`),
  updateRolePermissions: (rolePk, payload) => apiPut(`/api/roles/${rolePk}/permissions`, payload),
  createRole: (payload) => apiPost('/api/roles', payload),
  updateRole: (rolePk, payload) => apiPatch(`/api/roles/${rolePk}`, payload),
  deleteRole: (rolePk) => apiDelete(`/api/roles/${rolePk}`)
}
