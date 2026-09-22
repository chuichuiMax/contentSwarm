import { apiDelete, apiGet, apiPatch, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const residentPopulationApi = {
  listResidentPopulations: (params) => apiGet(`/api/content-resident-populations${encodeQuery(params)}`),
  createResidentPopulation: (payload) => apiPost('/api/content-resident-populations', payload),
  updateResidentPopulation: (itemId, payload) =>
    apiPatch(`/api/content-resident-populations/${itemId}`, payload),
  deleteResidentPopulation: (itemId) => apiDelete(`/api/content-resident-populations/${itemId}`)
}
