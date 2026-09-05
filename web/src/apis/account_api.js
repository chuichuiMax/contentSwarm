import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const accountApi = {
  listAccounts: (params) => apiGet(`/api/accounts${encodeQuery(params)}`),
  createAccount: (payload) => apiPost('/api/accounts', payload),
  updateAccount: (accountPk, payload) => apiPatch(`/api/accounts/${accountPk}`, payload),
  deleteAccount: (accountPk) => apiDelete(`/api/accounts/${accountPk}`)
}
