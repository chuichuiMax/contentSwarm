import { apiDelete, apiGet, apiPatch, apiPost, apiPostFormWithProgress, apiPut } from './base'

const IMAGE_FILE_LIMIT_BYTES = 100 * 1024 * 1024
const IMAGE_REQUEST_LIMIT_BYTES = 90 * 1024 * 1024

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
  getWorkFile: (assetId) =>
    apiGet(`/api/content/covers/assets/${encodeURIComponent(assetId)}/file`, {}, true, 'blob'),
  getRemoteConfig: () => apiGet('/api/material-library/remote-config'),
  saveRemoteConfig: (payload) => apiPut('/api/material-library/remote-config', payload),
  syncRemote: () => apiPost('/api/material-library/remote-sync', {}),
  getRemoteSyncStatus: (jobId = '') =>
    apiGet(`/api/material-library/remote-sync/status${encodeQuery({ job_id: jobId })}`),
  createShare: (itemIds, employeeId = '') =>
    apiPost(`/api/material-library/shares${encodeQuery({ employee_id: employeeId })}`, { item_ids: itemIds }),
  importImages: async (files, category, designStyle, onProgress, employeeId = '') => {
    const selected = Array.from(files || [])
    const oversized = selected.find((file) => file.size > IMAGE_FILE_LIMIT_BYTES)
    if (oversized) {
      throw new Error(`「${oversized.name}」超过 100 MB，请压缩后再上传`)
    }
    const batches = []
    let batch = []
    let batchBytes = 0
    selected.forEach((file) => {
      if (batch.length && batchBytes + file.size > IMAGE_REQUEST_LIMIT_BYTES) {
        batches.push(batch)
        batch = []
        batchBytes = 0
      }
      batch.push(file)
      batchBytes += file.size
    })
    if (batch.length) batches.push(batch)

    const totalBytes = selected.reduce((sum, file) => sum + file.size, 0)
    const items = []
    let created = 0
    let queued = 0
    let sentBytes = 0
    for (const current of batches) {
      const currentBytes = current.reduce((sum, file) => sum + file.size, 0)
      const form = new FormData()
      current.forEach((file) => form.append('files', file))
      form.append('category', category)
      if (designStyle) form.append('design_style', designStyle)
      let response
      try {
        response = await apiPostFormWithProgress(
          `/api/material-library/images/import${encodeQuery({ employee_id: employeeId })}`,
          form,
          {
            onProgress: (event) => {
              if (event.phase === 'done') return
              const loaded = sentBytes + (event.loaded || 0)
              onProgress?.({
                loaded,
                total: totalBytes,
                percent: totalBytes ? Math.min(99, Math.round((loaded / totalBytes) * 100)) : 0,
                phase: event.phase || 'sending'
              })
            }
          }
        )
      } catch (error) {
        const reason = error?.message || '素材上传失败'
        if (!items.length) throw error
        const failed = new Error(`已上传 ${items.length} 张，其余上传失败：${reason}`)
        failed.uploadedCount = items.length
        throw failed
      }
      items.push(...(response?.items || []))
      created += response?.summary?.created || response?.items?.length || 0
      queued += response?.summary?.queued || 0
      sentBytes += currentBytes
    }
    onProgress?.({ loaded: totalBytes, total: totalBytes, percent: 100, phase: 'done' })
    return { items, summary: { total: items.length, created, queued } }
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
