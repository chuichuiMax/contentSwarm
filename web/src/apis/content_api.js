import { apiDelete, apiGet, apiPatch, apiPost, apiPut, apiRequest } from './base'

const encodeQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const contentApi = {
  getBootstrap: () => apiGet('/api/content/bootstrap'),
  createTask: (payload) => apiPost('/api/content/tasks', payload),
  listTasks: (params) => apiGet(`/api/content/tasks${encodeQuery(params)}`),
  getTask: (taskId) => apiGet(`/api/content/tasks/${taskId}`),
  updateTask: (taskId, payload) => apiPatch(`/api/content/tasks/${taskId}`, payload),
  deleteTask: (taskId) => apiDelete(`/api/content/tasks/${taskId}`),
  duplicateTask: (taskId) => apiPost(`/api/content/tasks/${taskId}/duplicate`),
  createOcrResult: (taskId, file) => {
    const form = new FormData()
    form.append('file', file)
    return apiPost(`/api/content/tasks/${taskId}/ocr-results`, form)
  },
  listOcrResults: (taskId) => apiGet(`/api/content/tasks/${taskId}/ocr-results`),
  getOcrResult: (resultId) => apiGet(`/api/content/ocr-results/${resultId}`),
  updateOcrResult: (resultId, payload) =>
    apiPatch(`/api/content/ocr-results/${resultId}`, payload),
  retryOcrResult: (resultId) => apiPost(`/api/content/ocr-results/${resultId}/retry`),
  getOcrImage: (resultId) =>
    apiGet(`/api/content/ocr-results/${resultId}/image`, {}, true, 'blob'),
  getCoverBootstrap: () => apiGet('/api/content/covers/bootstrap'),
  listHyCanvasTemplates: () => apiGet('/api/content/covers/hycanvas/templates'),
  getHyCanvasTemplatePreview: (templateId) =>
    apiGet(
      `/api/content/covers/hycanvas/templates/${encodeURIComponent(templateId)}/render.png`,
      {},
      true,
      'blob'
    ),
  getHyCanvasCompositePreview: (templateId, imageItemId) =>
    apiPost(
      `/api/content/covers/hycanvas/templates/${encodeURIComponent(templateId)}/preview.png`,
      { image_item_id: imageItemId },
      {},
      true,
      'blob'
    ),
  createHyCanvasDesign: (payload) => apiPost('/api/content/covers/hycanvas/designs', payload),
  syncHyCanvasDesign: (designId, artifactId) =>
    apiPost(`/api/content/covers/hycanvas/designs/${encodeURIComponent(designId)}/sync`, {
      artifact_id: artifactId
    }),
  createHyCanvasEditorSession: (designId, payload) =>
    apiPost(
      `/api/content/covers/hycanvas/designs/${encodeURIComponent(designId)}/editor-session`,
      payload
    ),
  createHyCanvasWorkspaceSession: () =>
    apiPost('/api/content/covers/hycanvas/workspace-session'),
  updateCoverImage2Config: (payload) => apiPut('/api/content/covers/image2-config', payload),
  testCoverImage2Config: (payload) => apiPost('/api/content/covers/image2-config/test', payload),
  previewCoverTemplateReplication: (payload) =>
    apiPost('/api/content/covers/template-replication/preview', payload),
  importCoverPosterTemplates: (files, category) => {
    const form = new FormData()
    Array.from(files).forEach((file) => form.append('files', file))
    form.append('category', category)
    return apiPost('/api/content/covers/poster-templates/import', form)
  },
  listCoverPosterTemplates: (params = {}) =>
    apiGet(`/api/content/covers/poster-templates${encodeQuery(params)}`),
  getCoverPosterTemplate: (templateId) =>
    apiGet(`/api/content/covers/poster-templates/${templateId}`),
  updateCoverPosterTemplate: (templateId, payload) =>
    apiPatch(`/api/content/covers/poster-templates/${templateId}`, payload),
  deleteCoverPosterTemplate: (templateId) =>
    apiDelete(`/api/content/covers/poster-templates/${templateId}`),
  analyzeCoverPosterTemplate: (templateId) =>
    apiPost(`/api/content/covers/poster-templates/${templateId}/analyze`),
  reviewCoverPosterTemplate: (templateId, payload) =>
    apiPut(`/api/content/covers/poster-templates/${templateId}/review`, payload),
  previewCoverPosterBillboard: (payload) =>
    apiPost('/api/content/covers/poster-billboard/preview', payload),
  generateCoverPosterBillboard: (payload) =>
    apiPost('/api/content/covers/poster-billboard/generate', payload),
  uploadCoverAsset: (file, role = 'source', contentTaskId = null) => {
    const form = new FormData()
    form.append('file', file)
    form.append('role', role)
    if (contentTaskId) form.append('content_task_id', contentTaskId)
    return apiPost('/api/content/covers/assets', form)
  },
  deleteCoverAsset: (assetId) => apiDelete(`/api/content/covers/assets/${assetId}`),
  getCoverAssetFile: (assetId) =>
    apiGet(`/api/content/covers/assets/${assetId}/file`, {}, true, 'blob'),
  createCoverEditorProject: (payload) =>
    apiPost('/api/content/covers/editor-projects', payload),
  getCoverEditorProject: (projectId) =>
    apiGet(`/api/content/covers/editor-projects/${projectId}`),
  updateCoverEditorProject: (projectId, payload) =>
    apiPatch(`/api/content/covers/editor-projects/${projectId}`, payload),
  renderCoverEditorProject: (projectId, payload) =>
    apiPost(`/api/content/covers/editor-projects/${projectId}/render`, payload),
  composeCover: (payload) => apiPost('/api/content/covers/compose', payload),
  generateCover: (payload) => apiPost('/api/content/covers/generate', payload),
  listCoverJobs: (params = {}) => apiGet(`/api/content/covers/jobs${encodeQuery(params)}`),
  getCoverJob: (jobId) => apiGet(`/api/content/covers/jobs/${jobId}`),
  streamCoverJobEvents: (jobId, afterSeq = '0-0', options = {}) =>
    apiRequest(
      `/api/content/covers/jobs/${jobId}/events?after_seq=${encodeURIComponent(afterSeq)}`,
      { method: 'GET', ...options },
      true,
      'raw'
    ),
  retryCoverJob: (jobId, payload) =>
    apiPost(`/api/content/covers/jobs/${jobId}/retry`, payload),
  cancelCoverJob: (jobId) => apiPost(`/api/content/covers/jobs/${jobId}/cancel`),
  setCurrentCover: (jobId, assetId = null) =>
    apiPost(`/api/content/covers/jobs/${jobId}/set-current`, { asset_id: assetId }),
  saveBrief: (taskId, brief) => apiPut(`/api/content/tasks/${taskId}/brief`, { brief }),
  compileBrief: (taskId, brief) =>
    apiPost(`/api/content/tasks/${taskId}/compile-brief`, { brief }),
  getRuleBundle: (versionId) =>
    apiGet(`/api/content/rule-versions/${versionId}/bundle`),
  createRun: (taskId, payload) => apiPost(`/api/content/tasks/${taskId}/runs`, payload),
  getRun: (runId) => apiGet(`/api/content/runs/${runId}`),
  resumeRun: (runId, payload) => apiPost(`/api/content/runs/${runId}/resume`, payload),
  cancelRun: (runId) => apiPost(`/api/content/runs/${runId}/cancel`),
  retryNode: (runId, payload) => apiPost(`/api/content/runs/${runId}/retry-node`, payload),
  streamRunEvents: (runId, afterSeq = '0-0', options = {}) =>
    apiRequest(
      `/api/content/runs/${runId}/events?after_seq=${encodeURIComponent(afterSeq)}`,
      { method: 'GET', ...options },
      true,
      'raw'
    ),
  getTaskArtifact: (taskId) => apiGet(`/api/content/tasks/${taskId}/artifact`),
  getArtifactViralReference: (artifactId) =>
    apiGet(`/api/content/artifacts/${artifactId}/viral-reference`),
  updateArtifact: (artifactId, payload) =>
    apiPatch(`/api/content/artifacts/${artifactId}`, payload),
  aiEditArtifact: (artifactId, payload) =>
    apiPost(`/api/content/artifacts/${artifactId}/ai-edit`, payload),
  reviewArtifact: (artifactId, payload = {}) =>
    apiPost(`/api/content/artifacts/${artifactId}/review`, payload),
  finalizeArtifact: (artifactId, payload = {}) =>
    apiPost(`/api/content/artifacts/${artifactId}/finalize`, payload),
  regenerateArtifact: (artifactId, payload) =>
    apiPost(`/api/content/artifacts/${artifactId}/regenerate`, payload),
  listArtifactVersions: (artifactId) =>
    apiGet(`/api/content/artifacts/${artifactId}/versions`),
  listXiaohongshuAccounts: () => apiGet('/api/content/xiaohongshu/accounts'),
  createXiaohongshuAccount: (payload) =>
    apiPost('/api/content/xiaohongshu/accounts', payload),
  updateXiaohongshuAccount: (accountId, payload) =>
    apiPatch(`/api/content/xiaohongshu/accounts/${accountId}`, payload),
  deleteXiaohongshuAccount: (accountId) =>
    apiDelete(`/api/content/xiaohongshu/accounts/${accountId}`),
  loginXiaohongshuAccount: (accountId) =>
    apiPost(`/api/content/xiaohongshu/accounts/${accountId}/login`),
  checkXiaohongshuAccount: (accountId) =>
    apiPost(`/api/content/xiaohongshu/accounts/${accountId}/check`),
  openXiaohongshuBrowserSession: (accountId, payload = {}) =>
    apiPost(`/api/content/xiaohongshu/accounts/${accountId}/browser-session`, payload),
  getXiaohongshuBrowserSession: (accountId) =>
    apiGet(`/api/content/xiaohongshu/accounts/${accountId}/browser-session`),
  heartbeatXiaohongshuBrowserSession: (accountId) =>
    apiPost(`/api/content/xiaohongshu/accounts/${accountId}/browser-session/heartbeat`),
  claimXiaohongshuBrowserSession: (accountId) =>
    apiPost(`/api/content/xiaohongshu/accounts/${accountId}/browser-session/claim`),
  actXiaohongshuBrowserSession: (accountId, payload) =>
    apiPost(`/api/content/xiaohongshu/accounts/${accountId}/browser-session/action`, payload),
  getXiaohongshuBrowserScreenshot: (accountId) =>
    apiGet(`/api/content/xiaohongshu/accounts/${accountId}/browser-session/screenshot`, {}, true, 'blob'),
  closeXiaohongshuBrowserSession: (accountId) =>
    apiDelete(`/api/content/xiaohongshu/accounts/${accountId}/browser-session`),
  getXiaohongshuLoginSession: (sessionId) =>
    apiGet(`/api/content/xiaohongshu/login-sessions/${sessionId}`),
  createDistribution: (artifactId, payload) =>
    apiPost(`/api/content/artifacts/${artifactId}/distributions`, payload),
  listDistributions: (artifactId) =>
    apiGet(`/api/content/artifacts/${artifactId}/distributions`),
  getDistribution: (jobId) => apiGet(`/api/content/distributions/${jobId}`),
  getDistributionScreenshot: (resultId) =>
    apiGet(`/api/content/distribution-results/${resultId}/screenshot`, {}, true, 'blob'),
  listRuleVersions: () => apiGet('/api/content/admin/rules'),
  getAdminRuleBundle: (versionId) =>
    apiGet(`/api/content/admin/rules/${versionId}/bundle`),
  createRuleDraft: (payload) => apiPost('/api/content/admin/rules/drafts', payload),
  saveRuleDraft: (versionId, payload) =>
    apiPut(`/api/content/admin/rules/${versionId}/bundle`, payload),
  discardRuleDraft: (versionId) => apiDelete(`/api/content/admin/rules/${versionId}`),
  publishRuleVersion: (versionId, payload = {}) =>
    apiPost(`/api/content/admin/rules/${versionId}/publish`, payload),
  rollbackRuleVersion: (versionId, payload = {}) =>
    apiPost(`/api/content/admin/rules/${versionId}/rollback`, payload),
  listIndustryTemplates: () => apiGet('/api/content/admin/industry-templates'),
  listIndustryPacks: () => apiGet('/api/content/admin/industry-packs'),
  validateIndustryPack: (versionId) =>
    apiPost(`/api/content/admin/industry-packs/${versionId}/validate`),
  transitionIndustryPack: (versionId, payload) =>
    apiPost(`/api/content/admin/industry-packs/${versionId}/transition`, payload),
  submitIndustryPackRegression: (versionId, payload) =>
    apiPost(`/api/content/admin/industry-packs/${versionId}/regression`, payload),
  listWorkflowTemplates: () => apiGet('/api/content/admin/workflow-templates'),
  publishWorkflowVersion: (versionId, payload = {}) =>
    apiPost(`/api/content/admin/workflows/${versionId}/publish`, payload),
  rollbackWorkflowVersion: (versionId, payload = {}) =>
    apiPost(`/api/content/admin/workflows/${versionId}/rollback`, payload)
}
