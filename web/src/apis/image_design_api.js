import { apiDelete, apiGet, apiPost } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const imageDesignApi = {
  getBootstrap: () => apiGet('/api/image-design/bootstrap'),
  listShowcase: (category) => apiGet(`/api/image-design/showcase${encodeQuery({ category })}`),
  createShowcase: (payload) => apiPost('/api/image-design/showcase', payload),
  deleteShowcase: (showcaseId) => apiDelete(`/api/image-design/showcase/${showcaseId}`),
  createAnalysis: (materialItemId, role) =>
    apiPost('/api/image-design/analyses', { material_item_id: materialItemId, role }),
  getAnalysis: (analysisId) => apiGet(`/api/image-design/analyses/${analysisId}`),
  createRefinement: (payload) => apiPost('/api/image-design/refinements', payload),
  getRefinement: (refinementId) => apiGet(`/api/image-design/refinements/${refinementId}`),
  generate: (payload) => {
    const controller = new AbortController()
    const timeoutId = globalThis.setTimeout(() => controller.abort(), 30000)
    return apiPost('/api/image-design/generate', payload, { signal: controller.signal })
      .finally(() => globalThis.clearTimeout(timeoutId))
  },
  getJob: (jobId) => apiGet(`/api/image-design/generate/status${encodeQuery({ job_id: jobId })}`),
  listJobs: (clientId) => apiGet(`/api/image-design/jobs${encodeQuery({ client_id: clientId })}`),
  listResults: (clientId) => apiGet(`/api/image-design/results${encodeQuery({ client_id: clientId })}`),
  getResultFile: (assetId) => apiGet(`/api/image-design/results/${assetId}/file`, {}, true, 'blob'),
  deleteResult: (assetId) => apiDelete(`/api/image-design/results/${assetId}`)
}
