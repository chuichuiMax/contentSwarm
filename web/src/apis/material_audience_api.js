import { apiDelete, apiGet, apiPatch, apiPost } from './base'

export const materialAudienceApi = {
  listAudiences: () => apiGet('/api/material-audiences'),
  createAudience: (payload) => apiPost('/api/material-audiences', payload),
  updateAudience: (audienceId, payload) => apiPatch(`/api/material-audiences/${audienceId}`, payload),
  deleteAudience: (audienceId) => apiDelete(`/api/material-audiences/${audienceId}`)
}
