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
  listCategories: (materialType, employeeId = '') =>
    apiGet(`/api/material-library/categories${encodeQuery({ material_type: materialType, employee_id: employeeId })}`),
  createCategory: (payload, employeeId = '') =>
    apiPost(`/api/material-library/categories${encodeQuery({ employee_id: employeeId })}`, payload),
  updateCategory: (materialType, categoryId, payload, employeeId = '') =>
    apiPatch(`/api/material-library/categories/${categoryId}${encodeQuery({ material_type: materialType, employee_id: employeeId })}`, payload),
  deleteCategory: (materialType, categoryId, targetCategoryId = null, employeeId = '') =>
    apiDelete(`/api/material-library/categories/${categoryId}${encodeQuery({ material_type: materialType, employee_id: employeeId })}`, {
      body: JSON.stringify({ target_category_id: targetCategoryId })
    }),
  listGalleries: (industrySlug = '', employeeId = '') =>
    apiGet(`/api/material-library/galleries${encodeQuery({ industry_slug: industrySlug, employee_id: employeeId })}`),
  listPersonalFolders: () => apiGet('/api/material-library/my-materials/folders'),
  renamePersonalFolder: (folder, name) =>
    apiPatch(`/api/material-library/my-materials/folders/${encodeURIComponent(folder)}`, { name }),
  deletePersonalFolder: (folder) =>
    apiDelete(`/api/material-library/my-materials/folders/${encodeURIComponent(folder)}`),
  listPersonalItems: (folder, page = 1, pageSize = 24) =>
    apiGet(`/api/material-library/my-materials/${encodeURIComponent(folder)}${encodeQuery({ page, page_size: pageSize })}`),
  getWorkFile: (assetId) =>
    apiGet(`/api/content/covers/assets/${encodeURIComponent(assetId)}/file`, {}, true, 'blob'),
  getRemoteConfig: () => apiGet('/api/material-library/remote-config'),
  saveRemoteConfig: (payload) => apiPut('/api/material-library/remote-config', payload),
  syncRemote: () => apiPost('/api/material-library/remote-sync', {}),
  getRemoteSyncStatus: (jobId = '') =>
    apiGet(`/api/material-library/remote-sync/status${encodeQuery({ job_id: jobId })}`),
  createShare: (itemIds, employeeId = '') =>
    apiPost(`/api/material-library/shares${encodeQuery({ employee_id: employeeId })}`, { item_ids: itemIds }),
  importImages: (files, category, designStyle, onProgress, employeeId = '') => {
    const form = new FormData()
    Array.from(files).forEach((file) => form.append('files', file))
    form.append('category', category)
    if (designStyle) form.append('design_style', designStyle)
    return apiPostFormWithProgress(
      `/api/material-library/images/import${encodeQuery({ employee_id: employeeId })}`,
      form,
      { onProgress }
    )
  },
  updateItem: (itemId, payload, employeeId = '') =>
    apiPatch(`/api/material-library/items/${itemId}${encodeQuery({ employee_id: employeeId })}`, payload),
  deleteItem: (itemId, employeeId = '') =>
    apiDelete(`/api/material-library/items/${itemId}${encodeQuery({ employee_id: employeeId })}`),
  getItemFile: (itemId, employeeId = '') =>
    apiGet(`/api/material-library/items/${itemId}/file${encodeQuery({ employee_id: employeeId })}`, {}, true, 'blob'),
  getItemThumbnail: (itemId, employeeId = '') =>
    apiGet(`/api/material-library/items/${itemId}/thumbnail${encodeQuery({ employee_id: employeeId })}`, {}, true, 'blob')
}
