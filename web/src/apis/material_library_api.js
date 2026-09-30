import { apiDelete, apiGet, apiPatch, apiPost, apiPostFormWithProgress, apiPut } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const materialLibraryApi = {
  listItems: (params) => apiGet(`/api/material-library/items${encodeQuery(params)}`),
  listCategories: (materialType) => apiGet(`/api/material-library/categories?material_type=${materialType}`),
  createCategory: (payload) => apiPost('/api/material-library/categories', payload),
  updateCategory: (materialType, categoryId, payload) =>
    apiPatch(`/api/material-library/categories/${categoryId}?material_type=${materialType}`, payload),
  deleteCategory: (materialType, categoryId, targetCategoryId = null) =>
    apiDelete(`/api/material-library/categories/${categoryId}?material_type=${materialType}`, {
      body: JSON.stringify({ target_category_id: targetCategoryId })
    }),
  listGalleries: (industrySlug = '') =>
    apiGet(`/api/material-library/galleries${encodeQuery({ industry_slug: industrySlug })}`),
  listPersonalFolders: () => apiGet('/api/material-library/my-materials/folders'),
  listPersonalItems: (folder, page = 1, pageSize = 24) =>
    apiGet(`/api/material-library/my-materials/${encodeURIComponent(folder)}${encodeQuery({ page, page_size: pageSize })}`),
  getWorkFile: (assetId) =>
    apiGet(`/api/content/covers/assets/${encodeURIComponent(assetId)}/file`, {}, true, 'blob'),
  getRemoteConfig: () => apiGet('/api/material-library/remote-config'),
  saveRemoteConfig: (payload) => apiPut('/api/material-library/remote-config', payload),
  syncRemote: () => apiPost('/api/material-library/remote-sync', {}),
  getRemoteSyncStatus: (jobId = '') =>
    apiGet(`/api/material-library/remote-sync/status${encodeQuery({ job_id: jobId })}`),
  createShare: (itemIds) => apiPost('/api/material-library/shares', { item_ids: itemIds }),
  importImages: (files, category, designStyle, onProgress) => {
    const form = new FormData()
    Array.from(files).forEach((file) => form.append('files', file))
    form.append('category', category)
    if (designStyle) form.append('design_style', designStyle)
    return apiPostFormWithProgress('/api/material-library/images/import', form, { onProgress })
  },
  updateItem: (itemId, payload) => apiPatch(`/api/material-library/items/${itemId}`, payload),
  deleteItem: (itemId) => apiDelete(`/api/material-library/items/${itemId}`),
  getItemFile: (itemId) =>
    apiGet(`/api/material-library/items/${itemId}/file`, {}, true, 'blob'),
  getItemThumbnail: (itemId) =>
    apiGet(`/api/material-library/items/${itemId}/thumbnail`, {}, true, 'blob')
}
