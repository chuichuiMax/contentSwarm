<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import ContentPhotoComposition from '@/components/content/ContentPhotoComposition.vue'
import {
  ArrowLeft,
  BookOpenCheck,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock3,
  Copy,
  ExternalLink,
  FileClock,
  Folder,
  FileText,
  FolderOpen,
  Image,
  LayoutTemplate,
  LoaderCircle,
  PencilLine,
  Play,
  RefreshCw,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Tags,
  WandSparkles,
  X,
  ZoomIn
} from 'lucide-vue-next'
import AgentInputArea from '@/components/AgentInputArea.vue'
import ContentStudioToolbar from '@/components/content/ContentStudioToolbar.vue'
import ContentOcrDrawer from '@/components/content/ContentOcrDrawer.vue'
import ContentWorkflowStrategyPanel from '@/components/content/ContentWorkflowStrategyPanel.vue'
import ContentStrategyDecision from '@/components/content/ContentStrategyDecision.vue'
import XiaohongshuAccountPublishModal from '@/components/content/XiaohongshuAccountPublishModal.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { contentApi } from '@/apis/content_api'
import { materialLibraryApi } from '@/apis/material_library_api'
import { useContentStudioStore } from '@/stores/contentStudio'
import { useUserStore } from '@/stores/user'
import { formatEvidenceReference, hasSelectedViralReference } from '@/utils/contentEvidencePresentation'
import { formatDateTime } from '@/utils/time'
import {
  appendContentNarrativeText,
  buildContentEvidenceUsageSnapshot,
  buildContentNarrativeCodeLabels,
  buildContentNarrativeStream,
  buildKnowledgeEvidenceGroups,
  buildContentStrategyPresentation,
  buildContentWorkflowGroups,
  findContentStrategyNarrativeAnchor
} from '@/utils/contentWorkflowPresentation'

const route = useRoute()
const router = useRouter()
const store = useContentStudioStore()
const userStore = useUserStore()

const stage = ref(1)
const creation = reactive({
  industry_template_id: '',
  mode: 'pro',
  creation_mode: 'viral_rewrite',
  content_goal: '',
  content_type_code: undefined,
  name: ''
})
const formValues = reactive({})
const selectedAngleId = ref('')
const selectedTitleId = ref('')
const selectedTitleFormulaCode = ref('')
const selectedBodyFormulaCode = ref('')
const selectedCoverAssetId = ref('')
let autoResumedCoverKey = ''
const confirmedEvidenceIds = ref([])
const approvalNote = ref('')
const modelSpec = ref('')
const editor = reactive({ title: '', body: '', topics: [] })
const aiEditInstruction = ref('')
const pendingAiEdit = ref(null)
const aiEditHistoryElement = ref(null)
const versionDrawerOpen = ref(false)
const resultDetailOpen = ref(false)
const resultPreviewTab = ref('cover')
const viralReferenceLoading = ref(false)
const viralReference = ref(null)
const publishModalOpen = ref(false)
const expandedResultIds = ref(new Set())
const ocrModalOpen = ref(false)
const coverUrl = ref('')
const coverLoading = ref(false)
const resultVersionCoverUrls = ref({})
const resultVersionCoverLoading = ref({})
const resultGallerySourceUrls = ref({})
const resultGallerySourceLoading = ref(false)
const resultCompositionUrl = ref('')
const resultCompositionLoading = ref(false)
const resultGalleryIndex = ref(0)
const coverCandidateUrls = ref({})
const coverCandidatesLoading = ref(false)
const coverCandidates = computed(() => {
  const interrupt = store.interrupt
  if (interrupt?.interrupt_type !== 'cover_selection') return []
  // 历史中断的可选 ID 受旧审核过滤，使用当前任务实际生成成功的资产恢复选择。
  const job = store.runAudit?.external_wait
  const assetIds = job?.status === 'succeeded'
    ? job.result?.asset_ids || []
    : interrupt.asset_ids || []
  return assetIds.map(assetId => ({ assetId }))
})
const coverSelectionAllowed = computed(() =>
  coverCandidates.value.some(item => item.assetId === selectedCoverAssetId.value)
)
const materialGalleries = ref([])
const photoLayouts = ref([])
const activeGalleryId = ref('')
const galleryImages = ref([])
const galleryModalOpen = ref(false)
const pendingImageItems = ref([])
const posterTemplates = ref([])
const selectedImageItemId = ref('')
const photoComposition = ref(null)
const compositionSlotIndex = ref(null)
const compositionInsertIndexes = computed(() => {
  if (compositionSlotIndex.value === null || !photoComposition.value) return []
  return [
    compositionSlotIndex.value,
    ...photoComposition.value.slots
      .map((slot, index) => ({ slot, index }))
      .filter(({ slot, index }) => index !== compositionSlotIndex.value && !slot.image_item_id)
      .map(({ index }) => index)
  ]
})
const pendingImageLimit = computed(() => compositionSlotIndex.value === null ? 9 : compositionInsertIndexes.value.length)
let compositionPreviewTimer = null
const selectedImageGalleryId = ref('')
const selectedImageSummary = ref(null)
const selectedImagePreviewUrl = ref('')
const selectedImagePreviewLoading = ref(false)
const hycanvasCompositePreviewUrl = ref('')
const hycanvasCompositePreviewLoading = ref(false)
const imagePreviewOpen = ref(false)
const imagePreviewSrc = ref('')
const imagePreviewTitle = ref('')
const imagePreviewAlt = ref('')
const selectedPosterTemplateId = ref('')
const materialImageUrls = ref({})
const posterTemplateUrls = ref({})
const materialSelectorLoading = ref(false)
const galleryImagesLoading = ref(false)
const posterTemplatesRefreshing = ref(false)
const hycanvasTemplates = ref([])
const hycanvasTemplateUrls = ref({})
const hycanvasTemplatesLoading = ref(false)
const selectedHyCanvasTemplateId = ref('')
const hycanvasFields = reactive({})
const hycanvasCreating = ref(false)
const hycanvasDesign = ref(null)
const hycanvasImageFile = ref(null)
const studioPageElement = ref(null)
const followWorkflowOutput = ref(true)
const accumulatedWorkflowNarrative = ref([])
const streamedWorkflowNarrative = ref('')
const posterTemplateSyncIntervalMs = 10_000
let draftSaveTimer = null
let posterTemplateSyncTimer = null
let workflowNarrativeTimer = null
let coverLoadGeneration = 0
let resultVersionCoverGeneration = 0
let resultGalleryLoadGeneration = 0
let resultCompositionGeneration = 0
let coverCandidateLoadGeneration = 0
let materialPreviewGeneration = 0
let selectedImagePreviewGeneration = 0
let hycanvasCompositePreviewGeneration = 0
let posterPreviewGeneration = 0
let hycanvasTemplateLoadGeneration = 0
let posterTemplateSignature = ''

const materialGalleryMap = computed(() => new Map(materialGalleries.value.map((item) => [item.id, item])))
const rootMaterialGalleries = computed(() => materialGalleries.value.filter((item) => !item.parent_id))
const activeMaterialGallery = computed(() => materialGalleryMap.value.get(activeGalleryId.value) || null)
const activeMaterialGalleryParent = computed(() => (
  materialGalleryMap.value.get(activeMaterialGallery.value?.parent_id) || null
))
const activeMaterialGalleryChildren = computed(() => (
  activeGalleryId.value
    ? materialGalleries.value.filter((item) => item.parent_id === activeGalleryId.value)
    : rootMaterialGalleries.value
))
const activeMaterialGalleryPath = computed(() => (
  activeMaterialGalleryParent.value
    ? `${activeMaterialGalleryParent.value.name} / ${activeMaterialGallery.value?.name || ''}`
    : activeMaterialGallery.value?.name || '图库'
))
const selectedImageGallery = computed(() => materialGalleryMap.value.get(selectedImageGalleryId.value) || null)
const selectedImageRootGalleryId = computed(() => (
  selectedImageGallery.value?.parent_id || selectedImageGallery.value?.id || ''
))
const selectedHyCanvasTemplate = computed(() =>
  hycanvasTemplates.value.find((item) => item.id === selectedHyCanvasTemplateId.value) || null
)
const hasViralReference = computed(() => hasSelectedViralReference(store.artifact))
const resultVisualMaterial = computed(() => (
  store.artifact?.runtime_config_snapshot?.visual_material ||
  store.task?.runtime_config_snapshot?.visual_material ||
  store.task?.brief?.visual_material ||
  {}
))
const resultPhotoComposition = computed(() => {
  const composition = resultVisualMaterial.value?.photo_composition
  return composition?.slots?.length ? composition : null
})
const resultGallerySourceIds = computed(() => {
  if (resultPhotoComposition.value) {
    return [...new Set(resultPhotoComposition.value.slots.map(slot => slot.image_item_id).filter(Boolean))]
  }
  const visual = resultVisualMaterial.value
  const selectedIds = visual.photo_composition?.slots
    ?.map(slot => slot.image_item_id)
    .filter(Boolean) || [visual.image_item_id].filter(Boolean)
  const uniqueIds = [...new Set(selectedIds)]
  return store.artifact?.cover_asset_id
    ? uniqueIds.filter(id => id !== visual.image_item_id)
    : uniqueIds
})
const resultGalleryItems = computed(() => {
  const items = []
  if (store.artifact?.cover_asset_id) {
    items.push({
      id: `cover:${store.artifact.cover_asset_id}`,
      label: '生成封面',
      url: coverUrl.value,
      loading: coverLoading.value
    })
  }
  if (resultPhotoComposition.value) {
    items.push({
      id: 'composition',
      label: '图片组合',
      url: resultCompositionUrl.value,
      loading: resultCompositionLoading.value
    })
  } else {
    resultGallerySourceIds.value.forEach((id, index) => {
      items.push({
        id: `material:${id}`,
        label: `内容图片 ${index + 1}`,
        url: resultGallerySourceUrls.value[id] || '',
        loading: resultGallerySourceLoading.value && !resultGallerySourceUrls.value[id]
      })
    })
  }
  return items
})
const currentResultGalleryItem = computed(() => resultGalleryItems.value[resultGalleryIndex.value] || null)


watch(
  () => store.artifact?.id,
  () => {
    resultPreviewTab.value = 'cover'
    resultGalleryIndex.value = 0
    pendingAiEdit.value = null
    viralReference.value = null
  }
)

watch(resultDetailOpen, open => {
  if (open) resultGalleryIndex.value = 0
})

watch(() => resultGalleryItems.value.length, count => {
  if (resultGalleryIndex.value >= count) resultGalleryIndex.value = Math.max(0, count - 1)
})

const suggestedHyCanvasValue = (field) => {
  const label = field.label || ''
  const body = String(editor.body || '').replace(/[#*_>`\n]+/g, ' ').replace(/\s+/g, ' ').trim()
  const facts = store.task?.brief?.form_values || formValues
  const semanticValues = {
    title: editor.title || store.artifact?.title || '',
    subtitle: editor.topics?.[0] || '',
    body_excerpt: body.slice(0, field.constraints?.maxChars || 80),
    project_name: facts.project_name || facts.community_name || '',
    project_name_en: facts.project_name_en || facts.community_name_en || '',
    project_area: String(facts.project_area || facts.area || facts.area_sqm || '').match(/\d+(?:\.\d+)?/)?.[0] || '',
    designer: facts.designer || facts.designer_name || '',
    completion_year: String(facts.completion_year || facts.year || '').match(/(?:19|20)\d{2}/)?.[0] || '',
    brand_name: formValues.brand_name || '',
  }
  if (field.semanticRole && semanticValues[field.semanticRole] !== undefined) return semanticValues[field.semanticRole]
  if (label.includes('标题') || label.includes('语录')) return editor.title || store.artifact?.title || ''
  if (label.includes('账号')) return `@${formValues.brand_name || '品牌账号'}`
  if (label.includes('产品') || label.includes('店名') || label.includes('名称')) {
    return formValues.brand_name || editor.title || ''
  }
  return body.slice(0, 80)
}

const initializeHyCanvasFields = () => {
  Object.keys(hycanvasFields).forEach((key) => delete hycanvasFields[key])
  for (const field of selectedHyCanvasTemplate.value?.fillable_fields?.filter((item) => item.kind === 'text' && item.semanticRole !== 'label') || []) {
    hycanvasFields[field.label] = suggestedHyCanvasValue(field)
  }
}

const selectHyCanvasImage = (event) => {
  hycanvasImageFile.value = event.target.files?.[0] || null
}

const loadHyCanvasTemplates = async () => {
  if (hycanvasTemplatesLoading.value || hycanvasTemplates.value.length) return
  const generation = ++hycanvasTemplateLoadGeneration
  hycanvasTemplatesLoading.value = true
  try {
    const response = await contentApi.listHyCanvasTemplates()
    const templates = response.templates || []
    const nextUrls = {}
    await Promise.all(
      templates.map(async (item) => {
        const previewUrl = item.preview_urls?.[0] || ''
        if (!previewUrl.startsWith('/api/content/covers/hycanvas/templates/')) {
          nextUrls[item.id] = previewUrl
          return
        }
        try {
          const file = await contentApi.getHyCanvasTemplatePreview(item.id)
          nextUrls[item.id] = URL.createObjectURL(await file.blob())
        } catch {
          nextUrls[item.id] = ''
        }
      })
    )
    if (generation !== hycanvasTemplateLoadGeneration) {
      Object.values(nextUrls).forEach((url) => {
        if (url?.startsWith('blob:')) URL.revokeObjectURL(url)
      })
      return
    }
    Object.values(hycanvasTemplateUrls.value).forEach((url) => {
      if (url?.startsWith('blob:')) URL.revokeObjectURL(url)
    })
    hycanvasTemplates.value = templates
    hycanvasTemplateUrls.value = nextUrls
    selectedHyCanvasTemplateId.value =
      store.task?.brief?.visual_material?.hycanvas_template_id ||
      store.artifact?.hycanvas_design_snapshot?.template_id ||
      ''
    initializeHyCanvasFields()
  } catch (error) {
    if (error?.response?.data?.detail?.code !== 'hycanvas_not_configured') {
      message.warning(error.message || '小红书模板专区加载失败')
    }
  } finally {
    if (generation === hycanvasTemplateLoadGeneration) hycanvasTemplatesLoading.value = false
  }
}

const createHyCanvasDesign = async () => {
  if (!selectedHyCanvasTemplateId.value) {
    message.warning('请选择小红书模板')
    return
  }
  hycanvasCreating.value = true
  try {
    let imageAssetId = null
    if (hycanvasImageFile.value) {
      const uploaded = await contentApi.uploadCoverAsset(hycanvasImageFile.value, 'source', store.task?.id)
      imageAssetId = uploaded.asset.id
    }
    hycanvasDesign.value = await contentApi.createHyCanvasDesign({
      artifact_id: store.artifact.id,
      template_id: selectedHyCanvasTemplateId.value,
      title: editor.title || selectedHyCanvasTemplate.value?.title || '小红书视觉稿',
      fields: { ...hycanvasFields },
      image_asset_id: imageAssetId
    })
    store.artifact = hycanvasDesign.value.artifact
    message.success('视觉稿已创建，并自动绑定为当前版本封面')
    await openCoverEditor()
  } catch (error) {
    message.error(error.message || '创建 HyCanvas 视觉稿失败')
  } finally {
    hycanvasCreating.value = false
  }
}

const taskId = computed(() => route.params.taskId)
const creationTemplates = computed(() => store.templates.filter((item) => item.slug === 'decoration'))
const selectedTemplate = computed(() =>
  store.templates.find((item) => item.id === creation.industry_template_id)
)
const selectedIndustrySlug = computed(() => store.template?.slug || selectedTemplate.value?.slug || '')
const needsContentDirection = computed(() => selectedTemplate.value?.strategy_mode === 'direction_scoped')
const selectedIndustryPack = computed(() => (store.bootstrap?.industry_packs || []).find(item => item.slug === selectedIndustrySlug.value && item.status === 'published'))
const scopedDirectionRules = computed(() => (store.ruleBundle?.combination_rules || []).filter((item) => {
  const industryScope = item.industry_scope || []
  const goalScope = item.content_goal_codes || []
  return item.enabled !== false
    && (!industryScope.length || industryScope.includes(selectedIndustrySlug.value))
    && (!goalScope.length || goalScope.includes(creation.content_goal))
}))
const scopedDirectionCodes = computed(() => new Set(
  scopedDirectionRules.value.flatMap(item => item.content_type_codes || [])
))
const directionOptions = computed(() => (store.ruleBundle?.content_types || [])
  .filter(item => item.enabled !== false && (
    scopedDirectionCodes.value.size
      ? scopedDirectionCodes.value.has(item.code)
      : (item.supported_goals || []).includes(creation.content_goal)
  ))
  .map(item => ({ ...item, name: selectedIndustryPack.value?.content_type_aliases?.[item.code] || item.name })))
const activeFields = computed(() => {
  if (!store.task) {
    if (!selectedTemplate.value) return []
    return creation.mode === 'quick'
      ? selectedTemplate.value.quick_form_schema
      : selectedTemplate.value.pro_form_schema
  }
  return store.task.mode === 'quick'
    ? store.template?.quick_form_schema || []
    : store.template?.pro_form_schema || []
})
const isQuickMode = computed(() => (store.task ? store.task.mode : creation.mode) === 'quick')
const titleOptions = computed(
  () => store.interrupt?.options || store.task?.title_candidates || []
)
const evidenceFieldLabels = computed(() =>
  Object.fromEntries(
    [...(store.template?.quick_form_schema || []), ...(store.template?.pro_form_schema || [])]
      .filter((field) => field.key && field.label)
      .map((field) => [field.key, field.label])
  )
)
const evidenceItemsById = computed(
  () =>
    new Map(
      [...(store.evidence?.items || []), ...(store.interrupt?.evidence_items || [])]
        .filter((item) => item?.id)
        .map((item) => [item.id, item])
    )
)
const evidenceReferenceText = (evidenceId, index = 0) =>
  formatEvidenceReference(
    evidenceItemsById.value.get(evidenceId),
    evidenceFieldLabels.value,
    index
  )
const angleOptions = computed(() =>
  store.interrupt?.interrupt_type === 'content_direction' ? store.interrupt.options || [] : []
)
const workflowNodeEvents = computed(() => {
  const nodes = new Map((store.runAudit?.nodes || []).map((item) => [item.node_id, item]))
  for (const item of store.runEvents) nodes.set(item.node_id, item)
  return [...nodes.values()]
})
const workflowGroups = computed(() =>
  buildContentWorkflowGroups(
    workflowNodeEvents.value,
    store.runAudit?.events || []
  )
)
const activeWorkflowGroup = computed(() => {
  const incompleteGroups = workflowGroups.value.filter((group) => group.status !== 'completed')
  return (
    incompleteGroups.find((group) => ['failed', 'running', 'active'].includes(group.status)) ||
    incompleteGroups[0] ||
    null
  )
})
const workflowNarrativeActivities = computed(() => {
  const activities = workflowGroups.value.flatMap((group) =>
    group.nodes.flatMap((node) => node.activities || [])
  )
  const uniqueActivities = new Map(activities.map((activity) => [activity.id, activity]))
  return [...uniqueActivities.values()].sort((left, right) =>
    String(left.order || '').localeCompare(String(right.order || ''))
  )
})
const workflowNarrativeCodeLabels = computed(() =>
  buildContentNarrativeCodeLabels(store.ruleBundle)
)
const workflowStrategyPresentation = computed(() =>
  buildContentStrategyPresentation(
    workflowNarrativeActivities.value,
    workflowNarrativeCodeLabels.value,
    store.artifact?.strategy_snapshot || store.task?.strategy || {}
  )
)
const workflowEvidenceOnlyPresentation = computed(() => ({
  ...workflowStrategyPresentation.value,
  formulaCodes: []
}))
const workflowEvidenceUsageSnapshot = computed(() => {
  const persisted = store.artifact?.evidence_usage_snapshot
  if (persisted?.items?.length) return persisted
  const generatedActivity = [...workflowNarrativeActivities.value]
    .reverse()
    .find((item) => item?.nodeId === 'generate_content' && item?.outputPreview)
  return buildContentEvidenceUsageSnapshot({
    ...(generatedActivity?.outputPreview || {}),
    selected_title: generatedActivity?.outputPreview?.title || store.task?.selected_title || {}
  })
})
const workflowEvidenceGroups = computed(() =>
  buildKnowledgeEvidenceGroups(store.evidence, workflowEvidenceUsageSnapshot.value)
)
const activeWorkflowNarrative = computed(() =>
  buildContentNarrativeStream(
    workflowNarrativeActivities.value,
    workflowNarrativeCodeLabels.value
  )
)
const activeWorkflowNarrativeText = computed(() =>
  accumulatedWorkflowNarrative.value.join('\n\n')
)
const workflowStrategyNarrativeAnchor = ref(null)
const workflowStrategyPanelVisible = computed(
  () =>
    workflowStrategyPresentation.value.formulaCodes.length > 0 &&
    workflowStrategyNarrativeAnchor.value !== null &&
    streamedWorkflowNarrative.value.length >= workflowStrategyNarrativeAnchor.value
)
const streamedWorkflowNarrativeBeforeStrategy = computed(() => {
  const anchor = workflowStrategyNarrativeAnchor.value
  if (anchor === null) return streamedWorkflowNarrative.value
  return streamedWorkflowNarrative.value.slice(0, anchor)
})
const streamedWorkflowNarrativeAfterStrategy = computed(() => {
  const anchor = workflowStrategyNarrativeAnchor.value
  if (anchor === null || streamedWorkflowNarrative.value.length <= anchor) return ''
  return streamedWorkflowNarrative.value.slice(anchor).trimStart()
})
const workflowNarrativeActive = computed(
  () =>
    streamedWorkflowNarrative.value.length < activeWorkflowNarrativeText.value.length ||
    ['running', 'active'].includes(activeWorkflowGroup.value?.status)
)
const reviewChecks = computed(() => store.artifact?.review_snapshot?.checks || store.task?.review?.checks || [])
const canFinalize = computed(() => ['passed', 'warning'].includes(store.artifact?.review_snapshot?.status))
const approvalAllowed = computed(() => {
  if (store.interrupt?.interrupt_type !== 'content_approval') return false
  return (
    store.interrupt.approval_allowed === true &&
    ['passed', 'warning'].includes(store.interrupt.validation_report?.status) &&
    ['passed', 'warning'].includes(store.interrupt.review_report?.status)
  )
})
const correctionChecks = computed(() => [
  ...(store.interrupt?.title_validation_report?.items || []).flatMap((item) => item.checks || []),
  ...(store.interrupt?.validation_report?.checks || []),
  ...(store.interrupt?.review_report?.checks || [])
].filter((item) => item.status === 'blocked' || item.level === 'error'))
const latestTitleValidationReport = computed(() => {
  const nodes = [...(store.runAudit?.nodes || [])].reverse()
  return nodes.find((item) => item.node_id === 'validate_title_candidates')
    ?.output_snapshot?.title_validation_report || null
})
const blockedTitleCandidates = computed(() =>
  (latestTitleValidationReport.value?.items || []).filter((item) => item.status === 'blocked')
)
const runFailed = computed(() =>
  ['failed', 'cancelled'].includes(store.currentRun?.status) || store.task?.status === 'failed'
)
const workflowRunStatus = computed(
  () => store.currentRun?.status || store.runAudit?.run?.status || ''
)
const workflowCompleted = computed(
  () => !!store.artifact && String(workflowRunStatus.value).toLowerCase() === 'completed'
)
const aiEditReady = computed(
  () =>
    workflowCompleted.value &&
    !!store.artifact &&
    !store.interrupt &&
    !store.loading.running &&
    !runFailed.value
)
const aiEditPlaceholder = computed(() =>
  aiEditReady.value ? '告诉 AI 需要怎样调整标题、正文或话题' : '工作流成功完成后才能修改内容'
)
const aiEditHistory = computed(() => {
  const history = [...store.versions]
    .reverse()
    .flatMap((version) => {
      const metadata = (version.edit_diff_snapshot || []).find((item) => item.type === 'ai_edit')
      if (!metadata) return []
      return [
        { id: `${version.id}-user`, role: 'user', content: metadata.instruction },
        {
          id: `${version.id}-assistant`,
          role: 'assistant',
          content: `${metadata.reply} 当前版本 v${version.version}`
        }
      ]
    })
  if (pendingAiEdit.value) {
    history.push(
      {
        id: `${pendingAiEdit.value.id}-user`,
        role: 'user',
        content: pendingAiEdit.value.instruction
      },
      {
        id: `${pendingAiEdit.value.id}-assistant`,
        role: 'assistant',
        status: pendingAiEdit.value.status,
        content: pendingAiEdit.value.message
      }
    )
  }
  return history
})
const completionResults = computed(() => {
  const versions = [...store.versions]
  if (
    store.artifact &&
    !versions.some((item) => item.version === store.artifact.current_version)
  ) {
    versions.unshift({
      ...store.artifact,
      id: `${store.artifact.id}-current`,
      version: store.artifact.current_version,
      source_type: 'generated'
    })
  }
  return versions.slice(0, 4).map((item) => ({
    ...item,
    isCurrent: item.version === store.artifact?.current_version
  }))
})
const resultCategoryLabel = computed(() => {
  if (store.template?.name) return store.template.name
  const strategy = store.artifact?.strategy_snapshot || store.task?.strategy || {}
  const contentTypeCode = strategy.content_direction || store.task?.content_type_code || ''
  const contentType = (store.ruleBundle?.content_types || []).find(
    (item) => item.code === contentTypeCode
  )
  return contentType?.name || contentTypeCode || '生成内容'
})
const resultLocation = computed(() => {
  const brief = store.task?.brief || {}
  const value = brief.form_values?.location ?? brief.business_variables?.location ?? brief.location
  if (value && typeof value === 'object') {
    return [value.province, value.city, value.district].filter(Boolean).join(' · ')
  }
  return String(value || '').trim()
})
const resultTime = (item) => formatDateTime(item.created_at || store.task?.updated_at)
const resultCoverUrl = (item) => (
  item.isCurrent ? coverUrl.value : resultVersionCoverUrls.value[item.id] || ''
)
const resultCoverLoading = (item) => (
  item.isCurrent ? coverLoading.value : Boolean(resultVersionCoverLoading.value[item.id])
)
const isResultExpanded = (id) => expandedResultIds.value.has(id)
const toggleResultExpanded = (id) => {
  const next = new Set(expandedResultIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedResultIds.value = next
}
const externalWaitProgress = computed(() =>
  Math.min(100, Math.max(0, Number(store.interrupt?.progress || 0)))
)
const externalWaitStatusLabel = computed(() => {
  const labels = {
    queued: '封面任务已进入队列',
    running: '封面服务正在生成图片',
    submitting: '正在提交封面生成请求',
    polling: '正在等待封面生成结果',
    downloading: '正在下载封面结果',
    saving: '正在保存封面资产',
    succeeded: '封面生成完成，正在自动继续工作流',
    failed: '封面生成失败，正在同步失败结果',
    cancelled: '封面任务已取消，正在同步任务状态'
  }
  return labels[store.interrupt?.status] || '正在等待封面服务'
})
const failedNodeId = computed(
  () => [...store.runEvents].reverse().find((item) => item.status === 'failed')?.node_id || null
)
const saveStatusLabel = computed(() => {
  if (store.saveStatus === 'saving') return '正在自动保存…'
  if (store.saveStatus === 'saved') return '草稿已自动保存'
  if (store.saveStatus === 'error') return '自动保存失败'
  return ''
})

const stageFromTask = (task) => {
  if (!task) return 1
  if (task.current_stage === 'review') return task.latest_run_id ? 2 : 3
  if (['generation', 'strategy'].includes(task.current_stage)) return 2
  return 1
}

const initializeFormValues = () => {
  Object.keys(formValues).forEach((key) => delete formValues[key])
  const saved = store.task?.brief?.form_values || {}
  const hasSavedValues = Object.values(saved).some(
    (value) => value !== undefined && value !== null && value !== '' && (!Array.isArray(value) || value.length)
  )
  activeFields.value.forEach((field) => {
    if (field.type === 'channel') formValues[field.key] = store.task?.channel_profile_version_id || ''
    else if (hasSavedValues && saved[field.key] !== undefined) formValues[field.key] = saved[field.key]
    else if (field.type === 'tags') formValues[field.key] = []
    else formValues[field.key] = ''
  })
}

const revokePreviewUrls = (urls) => {
  Object.values(urls || {}).forEach((url) => {
    if (url) URL.revokeObjectURL(url)
  })
}

const loadSelectedImagePreview = async (itemId) => {
  const generation = ++selectedImagePreviewGeneration
  if (selectedImagePreviewUrl.value) URL.revokeObjectURL(selectedImagePreviewUrl.value)
  selectedImagePreviewUrl.value = ''
  selectedImagePreviewLoading.value = Boolean(itemId)
  if (!itemId) return

  try {
    const file = await materialLibraryApi.getItemFile(itemId)
    const previewUrl = URL.createObjectURL(await file.blob())
    if (generation !== selectedImagePreviewGeneration) {
      URL.revokeObjectURL(previewUrl)
      return
    }
    selectedImagePreviewUrl.value = previewUrl
  } catch (error) {
    if (generation === selectedImagePreviewGeneration) {
      message.warning(error.message || '已选图片预览加载失败')
    }
  } finally {
    if (generation === selectedImagePreviewGeneration) selectedImagePreviewLoading.value = false
  }
}

const loadHyCanvasCompositePreview = async (imageItemId, templateId) => {
  const generation = ++hycanvasCompositePreviewGeneration
  if (hycanvasCompositePreviewUrl.value) URL.revokeObjectURL(hycanvasCompositePreviewUrl.value)
  hycanvasCompositePreviewUrl.value = ''
  hycanvasCompositePreviewLoading.value = Boolean(imageItemId && templateId)
  if (!imageItemId || !templateId) {
    hycanvasCompositePreviewLoading.value = false
    return
  }

  try {
    const file = await contentApi.getHyCanvasCompositePreview(templateId, imageItemId)
    const previewUrl = URL.createObjectURL(await file.blob())
    if (generation !== hycanvasCompositePreviewGeneration) {
      URL.revokeObjectURL(previewUrl)
      return
    }
    hycanvasCompositePreviewUrl.value = previewUrl
  } catch (error) {
    if (generation === hycanvasCompositePreviewGeneration) {
      message.warning(error.message || '模板合成预览加载失败')
    }
  } finally {
    if (generation === hycanvasCompositePreviewGeneration) hycanvasCompositePreviewLoading.value = false
  }
}

const openImagePreview = (src, title, alt) => {
  if (!src) return
  imagePreviewSrc.value = src
  imagePreviewTitle.value = title
  imagePreviewAlt.value = alt || title
  imagePreviewOpen.value = true
}

const initializeVisualSelection = () => {
  const saved = store.task?.brief?.visual_material || {}
  selectedImageItemId.value = store.task?.selected_image_item_id || saved.image_item_id || ''
  selectedPosterTemplateId.value =
    store.task?.selected_poster_template_id || saved.poster_template_id || ''
  selectedHyCanvasTemplateId.value = saved.hycanvas_template_id || ''
  photoComposition.value = saved.photo_composition || null
}

const loadGalleryImages = async () => {
  const generation = ++materialPreviewGeneration
  revokePreviewUrls(materialImageUrls.value)
  materialImageUrls.value = {}
  galleryImages.value = []
  if (!activeGalleryId.value) return
  galleryImagesLoading.value = true
  try {
    const response = await materialLibraryApi.listItems({
      material_type: 'image',
      category: activeGalleryId.value,
      status: 'enabled',
      page: 1,
      page_size: 100,
      sort: 'newest'
    })
    if (generation !== materialPreviewGeneration) return
    galleryImages.value = response.items || []
    const selectedItem = galleryImages.value.find((item) => item.id === selectedImageItemId.value)
    if (selectedItem) {
      selectedImageGalleryId.value = activeGalleryId.value
      selectedImageSummary.value = selectedItem
    }
    // Show the grid as soon as metadata arrives. Thumbnails then fill in
    // progressively with bounded concurrency instead of blocking the whole
    // dialog on 20+ full-resolution image downloads and decodes.
    galleryImagesLoading.value = false
    const items = [...galleryImages.value]
    let cursor = 0
    const loadNext = async () => {
      while (cursor < items.length) {
        const item = items[cursor++]
        try {
          const file = await materialLibraryApi.getItemThumbnail(item.id)
          const url = URL.createObjectURL(await file.blob())
          if (generation !== materialPreviewGeneration) {
            URL.revokeObjectURL(url)
            return
          }
          materialImageUrls.value = { ...materialImageUrls.value, [item.id]: url }
        } catch {
          if (generation === materialPreviewGeneration) {
            materialImageUrls.value = { ...materialImageUrls.value, [item.id]: '' }
          }
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(4, items.length) }, () => loadNext()))
  } catch (error) {
    if (generation === materialPreviewGeneration) {
      message.warning(error.message || '图库图片加载失败')
    }
  } finally {
    if (generation === materialPreviewGeneration) galleryImagesLoading.value = false
  }
}

const openGallery = async (galleryId) => {
  const openingModal = !galleryModalOpen.value
  activeGalleryId.value = galleryId
  if (openingModal) {
    const selectedIds = compositionSlotIndex.value === null
      ? photoComposition.value?.slots.map(slot => slot.image_item_id).filter(Boolean) || [selectedImageItemId.value].filter(Boolean)
      : [photoComposition.value.slots[compositionSlotIndex.value].image_item_id].filter(Boolean)
    pendingImageItems.value = [...new Set(selectedIds)].map(id => (
      id === selectedImageItemId.value && selectedImageSummary.value
        ? selectedImageSummary.value
        : { id }
    ))
  }
  galleryModalOpen.value = true
  await loadGalleryImages()
}

const togglePendingImage = (item) => {
  const index = pendingImageItems.value.findIndex(selected => selected.id === item.id)
  if (index !== -1) {
    pendingImageItems.value = pendingImageItems.value.filter(selected => selected.id !== item.id)
    return
  }
  if (pendingImageItems.value.length >= pendingImageLimit.value) {
    message.warning(`当前最多选择 ${pendingImageLimit.value} 张图片`)
    return
  }
  pendingImageItems.value = [...pendingImageItems.value, item]
}

const confirmGalleryImages = () => {
  if (compositionSlotIndex.value !== null) {
    const targetIndexes = compositionInsertIndexes.value
    const replacesPrimary = targetIndexes.some(index => photoComposition.value.slots[index].image_item_id === selectedImageItemId.value)
    const nextSlots = photoComposition.value.slots.map((slot, index) => {
      const selectedIndex = targetIndexes.indexOf(index)
      if (selectedIndex === -1) return slot
      const selectedId = pendingImageItems.value[selectedIndex]?.id || null
      return { image_item_id: selectedId, focal_x: 0.5, focal_y: 0.5 }
    })
    photoComposition.value = { ...photoComposition.value, slots: nextSlots }
    if (replacesPrimary && !nextSlots.some(slot => slot.image_item_id === selectedImageItemId.value)) {
      selectedImageItemId.value = nextSlots.find(slot => slot.image_item_id)?.image_item_id || ''
      if (!selectedImageItemId.value) photoComposition.value = null
    }
    compositionSlotIndex.value = null
    galleryModalOpen.value = false
    return
  }
  const selectedItems = pendingImageItems.value
  const primaryItem = selectedItems[0]
  selectedImageItemId.value = primaryItem?.id || ''
  if (primaryItem) {
    selectedImageGalleryId.value = primaryItem.category || selectedImageGalleryId.value
    selectedImageSummary.value = primaryItem.name
      ? primaryItem
      : primaryItem.id === selectedImageSummary.value?.id ? selectedImageSummary.value : null
  } else {
    selectedImageGalleryId.value = ''
    selectedImageSummary.value = null
  }
  if (selectedItems.length > 1) {
    const currentSlots = new Map(
      (photoComposition.value?.slots || [])
        .filter(slot => slot.image_item_id)
        .map(slot => [slot.image_item_id, slot])
    )
    const layout = photoLayouts.value
      .filter(item => item.cells.length >= selectedItems.length)
      .sort((left, right) => left.cells.length - right.cells.length)[0]
    photoComposition.value = {
      layout_id: layout.id,
      slots: layout.cells.map((_, index) => {
        const imageItemId = selectedItems[index]?.id || null
        return currentSlots.get(imageItemId) || { image_item_id: imageItemId, focal_x: 0.5, focal_y: 0.5 }
      })
    }
  } else {
    photoComposition.value = null
  }
  galleryModalOpen.value = false
}

const clearSelectedGalleryImage = () => {
  selectedImageItemId.value = ''
  selectedImageGalleryId.value = ''
  selectedImageSummary.value = null
  photoComposition.value = null
}

const selectCompositionImage = (index) => {
  compositionSlotIndex.value = index
  void openGallery('')
}
watch(galleryModalOpen, open => { if (!open) compositionSlotIndex.value = null })

const listAllMaterialPosterTemplates = async () => {
  const templates = []
  let page = 1
  while (true) {
    const response = await materialLibraryApi.listItems({
      material_type: 'cover_template',
      page,
      page_size: 100,
      sort: 'newest'
    })
    const pageItems = response.items || []
    templates.push(...pageItems)
    if (!pageItems.length || templates.length >= (response.total || templates.length)) break
    page += 1
  }
  return templates
}

const refreshPosterTemplates = async ({ notifySelectionReset = true } = {}) => {
  if (!store.task || stage.value !== 1 || posterTemplatesRefreshing.value) return
  posterTemplatesRefreshing.value = true
  const generation = ++posterPreviewGeneration
  try {
    const nextTemplates = await listAllMaterialPosterTemplates()
    if (generation !== posterPreviewGeneration) return
    const nextSignature = JSON.stringify(
      nextTemplates.map((item) => [
        item.id,
        item.asset_id,
        item.poster_template_id,
        item.name,
        item.category,
        item.category_name,
        item.sha256,
        item.status,
        item.template_status,
        item.template_version,
        item.selectable
      ])
    )
    if (nextSignature !== posterTemplateSignature) {
      const previousById = new Map(posterTemplates.value.map((item) => [item.id, item]))
      const nextUrls = {}
      await Promise.all(
        nextTemplates.map(async (item) => {
          const previous = previousById.get(item.id)
          if (
            previous?.asset_id === item.asset_id &&
            previous?.sha256 === item.sha256 &&
            posterTemplateUrls.value[item.id]
          ) {
            nextUrls[item.id] = posterTemplateUrls.value[item.id]
            return
          }
          try {
            const file = await materialLibraryApi.getItemFile(item.id)
            nextUrls[item.id] = URL.createObjectURL(await file.blob())
          } catch {
            nextUrls[item.id] = ''
          }
        })
      )
      if (generation !== posterPreviewGeneration) {
        const retainedUrls = new Set(Object.values(posterTemplateUrls.value))
        Object.values(nextUrls).forEach((url) => {
          if (url && !retainedUrls.has(url)) URL.revokeObjectURL(url)
        })
        return
      }
      const nextUrlSet = new Set(Object.values(nextUrls))
      Object.values(posterTemplateUrls.value).forEach((url) => {
        if (url && !nextUrlSet.has(url)) URL.revokeObjectURL(url)
      })
      posterTemplates.value = nextTemplates
      posterTemplateUrls.value = nextUrls
      posterTemplateSignature = nextSignature
    }
    if (
      selectedPosterTemplateId.value &&
      !nextTemplates.some(
        (item) => item.poster_template_id === selectedPosterTemplateId.value
      )
    ) {
      selectedPosterTemplateId.value = ''
      if (notifySelectionReset) message.warning('原封面模板已停用、移除或尚未完成标注，已切换为系统自动生成')
    }
  } catch (error) {
    if (generation === posterPreviewGeneration) {
      message.warning(error.message || '封面模板同步失败')
    }
  } finally {
    if (generation === posterPreviewGeneration) posterTemplatesRefreshing.value = false
  }
}

const syncPosterTemplatesWhenVisible = () => {
  if (document.visibilityState === 'visible') void refreshPosterTemplates()
}

const loadVisualMaterials = async () => {
  if (!store.task) return
  materialSelectorLoading.value = true
  try {
    const [galleryResponse, layoutResponse] = await Promise.all([
      materialLibraryApi.listGalleries(selectedIndustrySlug.value),
      contentApi.getPhotoLayouts(),
      loadHyCanvasTemplates()
    ])
    materialGalleries.value = galleryResponse.galleries || []
    photoLayouts.value = layoutResponse.layouts || []
    const savedCategoryId =
      store.task?.runtime_config_snapshot?.visual_material?.image_category_id ||
      store.task?.brief?.visual_material?.image_category_id
    activeGalleryId.value =
      (savedCategoryId && materialGalleries.value.some((item) => item.id === savedCategoryId)
        ? savedCategoryId
        : activeGalleryId.value) || rootMaterialGalleries.value[0]?.id || ''
    selectedImageGalleryId.value = selectedImageItemId.value ? savedCategoryId || '' : ''
    if (selectedImageItemId.value && activeGalleryId.value) await loadGalleryImages()
  } catch (error) {
    message.warning(error.message || '视觉素材加载失败')
  } finally {
    materialSelectorLoading.value = false
  }
}

const syncEditor = () => {
  editor.title = store.artifact?.title || ''
  editor.body = store.artifact?.body || ''
  editor.topics = [...(store.artifact?.topics || [])]
}

const trackWorkflowScroll = () => {
  const page = studioPageElement.value
  followWorkflowOutput.value = page.scrollHeight - page.scrollTop - page.clientHeight <= 80
}

const scrollWorkflowToEnd = () => {
  if (studioPageElement.value && followWorkflowOutput.value) {
    studioPageElement.value.scrollTop = studioPageElement.value.scrollHeight
  }
}

watch(
  () => store.task,
  (task) => {
    if (!task) return
    stage.value = stageFromTask(task)
    syncEditor()
  },
  { deep: true }
)

watch(
  workflowCompleted,
  async (completed, wasCompleted) => {
    if (!completed) return
    void store.loadVersions()
    if (wasCompleted !== false) return
    await nextTick()
    scrollWorkflowToEnd()
  },
  { immediate: true }
)

watch(
  taskId,
  () => {
    window.clearTimeout(workflowNarrativeTimer)
    followWorkflowOutput.value = true
    accumulatedWorkflowNarrative.value = []
    streamedWorkflowNarrative.value = ''
    workflowStrategyNarrativeAnchor.value = null
  }
)

watch(
  activeWorkflowNarrative,
  (narrative) => {
    const nextNarrative = appendContentNarrativeText(
      accumulatedWorkflowNarrative.value,
      narrative
    )
    if (nextNarrative.length !== accumulatedWorkflowNarrative.value.length) {
      accumulatedWorkflowNarrative.value = nextNarrative
    }
  },
  { immediate: true }
)

watch(
  [activeWorkflowNarrativeText, workflowCompleted],
  ([targetText, completed]) => {
    window.clearTimeout(workflowNarrativeTimer)
    if (completed) {
      streamedWorkflowNarrative.value = targetText
      void nextTick(scrollWorkflowToEnd)
      return
    }
    if (!targetText.startsWith(streamedWorkflowNarrative.value)) {
      streamedWorkflowNarrative.value = ''
    }
    const appendChunk = async () => {
      const remaining = targetText.length - streamedWorkflowNarrative.value.length
      if (remaining <= 0) return
      const chunkSize = remaining > 500 ? 12 : remaining > 160 ? 6 : 3
      streamedWorkflowNarrative.value += targetText.slice(
        streamedWorkflowNarrative.value.length,
        streamedWorkflowNarrative.value.length + chunkSize
      )
      await nextTick()
      scrollWorkflowToEnd()
      workflowNarrativeTimer = window.setTimeout(appendChunk, 18)
    }
    void appendChunk()
  },
  { immediate: true }
)

watch(
  () => store.interrupt,
  (interrupt) => {
    selectedAngleId.value = ''
    selectedTitleId.value = ''
    selectedTitleFormulaCode.value = ''
    selectedBodyFormulaCode.value = ''
    selectedCoverAssetId.value = ''
    confirmedEvidenceIds.value = ['high_risk_facts', 'strategy_product_facts'].includes(
      interrupt?.interrupt_type
    )
      ? (interrupt.node_id === 'confirm_strategy_prices' ? [] : [...(interrupt.evidence_ids || [])])
      : []
    approvalNote.value = ''
  },
  { deep: true }
)

watch(
  () => coverCandidates.value.map((item) => item.assetId).join(','),
  async (assetKey) => {
    const interrupt = store.interrupt
    if (interrupt?.interrupt_type !== 'cover_selection') return
    const assetIds = assetKey ? assetKey.split(',').filter(Boolean) : []
    if (assetIds.length !== 1) return
    const resumeKey = `${interrupt.run_id}:${assetIds[0]}`
    if (autoResumedCoverKey === resumeKey) return
    autoResumedCoverKey = resumeKey
    try {
      await store.resumeRun({
        run_id: interrupt.run_id,
        node_id: interrupt.node_id,
        expected_state_version: interrupt.expected_state_version,
        asset_id: assetIds[0]
      })
    } catch (error) {
      autoResumedCoverKey = ''
      message.error(error.message || '自动确认封面失败')
    }
  },
  { immediate: true }
)

watch(
  () => store.artifact?.cover_asset_id,
  async (assetId) => {
    const generation = ++coverLoadGeneration
    if (coverUrl.value) {
      URL.revokeObjectURL(coverUrl.value)
      coverUrl.value = ''
    }
    if (!assetId) return
    coverLoading.value = true
    try {
      const response = await contentApi.getCoverAssetFile(assetId)
      const nextUrl = URL.createObjectURL(await response.blob())
      if (generation !== coverLoadGeneration) {
        URL.revokeObjectURL(nextUrl)
        return
      }
      coverUrl.value = nextUrl
    } catch (error) {
      if (generation === coverLoadGeneration) {
        message.warning(error.message || '当前封面暂时无法预览')
      }
    } finally {
      if (generation === coverLoadGeneration) coverLoading.value = false
    }
  },
  { immediate: true }
)

watch(
  [workflowStrategyPresentation, activeWorkflowNarrativeText, workflowNarrativeActivities],
  ([presentation, narrativeText, activities]) => {
    if (
      workflowStrategyNarrativeAnchor.value === null &&
      presentation.formulaCodes.length
    ) {
      workflowStrategyNarrativeAnchor.value =
        findContentStrategyNarrativeAnchor(
          activities,
          workflowNarrativeCodeLabels.value
        ) ?? narrativeText.length
    }
  },
  { immediate: true, flush: 'post' }
)

watch(
  () => completionResults.value
    .filter(item => !item.isCurrent && item.cover_asset_id)
    .map(item => `${item.id}:${item.cover_asset_id}`)
    .join('|'),
  async () => {
    const generation = ++resultVersionCoverGeneration
    Object.values(resultVersionCoverUrls.value).filter(Boolean).forEach(URL.revokeObjectURL)
    resultVersionCoverUrls.value = {}
    resultVersionCoverLoading.value = {}

    const items = completionResults.value.filter(item => !item.isCurrent && item.cover_asset_id)
    if (!items.length) return

    resultVersionCoverLoading.value = Object.fromEntries(items.map(item => [item.id, true]))
    const entries = await Promise.all(items.map(async item => {
      try {
        const response = await contentApi.getCoverAssetFile(item.cover_asset_id)
        return [item.id, URL.createObjectURL(await response.blob())]
      } catch {
        return [item.id, '']
      }
    }))

    if (generation !== resultVersionCoverGeneration) {
      entries.map(([, url]) => url).filter(Boolean).forEach(URL.revokeObjectURL)
      return
    }
    resultVersionCoverUrls.value = Object.fromEntries(entries)
    resultVersionCoverLoading.value = Object.fromEntries(items.map(item => [item.id, false]))
  },
  { immediate: true }
)

watch(
  () => resultGallerySourceIds.value.join('|'),
  async () => {
    const generation = ++resultGalleryLoadGeneration
    Object.values(resultGallerySourceUrls.value).filter(Boolean).forEach(URL.revokeObjectURL)
    resultGallerySourceUrls.value = {}
    const ids = resultGallerySourceIds.value
    if (!ids.length) {
      resultGallerySourceLoading.value = false
      return
    }
    resultGallerySourceLoading.value = true
    const entries = await Promise.all(ids.map(async id => {
      try {
        const response = await materialLibraryApi.getItemThumbnail(id)
        return [id, URL.createObjectURL(await response.blob())]
      } catch {
        return [id, '']
      }
    }))
    if (generation !== resultGalleryLoadGeneration) {
      entries.map(([, url]) => url).filter(Boolean).forEach(URL.revokeObjectURL)
      return
    }
    resultGallerySourceUrls.value = Object.fromEntries(entries)
    resultGallerySourceLoading.value = false
  },
  { immediate: true }
)

const loadCompositionImage = (url) => new Promise((resolve, reject) => {
  const image = new window.Image()
  image.onload = () => resolve(image)
  image.onerror = reject
  image.src = url
})

const renderResultComposition = async (composition, sourceUrls) => {
  const slots = composition?.slots || []
  const cols = 2
  const rows = Math.max(1, Math.ceil(slots.length / cols))
  const gap = Math.max(0, Number(composition?.gap) || 0)
  const images = await Promise.all(slots.map(slot => {
    const url = sourceUrls[slot.image_item_id]
    return url ? loadCompositionImage(url) : null
  }))
  const canvasWidth = 1200
  const trackWidth = (canvasWidth - gap) / cols
  const trackHeight = trackWidth
  const canvasHeight = Math.max(1, Math.round(trackHeight * rows + gap * (rows - 1)))
  const canvas = document.createElement('canvas')
  canvas.width = canvasWidth
  canvas.height = canvasHeight
  const context = canvas.getContext('2d')
  if (!context) throw new Error('无法创建组合图片预览')
  context.fillStyle = '#f5f6f8'
  context.fillRect(0, 0, canvasWidth, canvasHeight)

  slots.forEach((slot, index) => {
    const image = images[index]
    if (!image) return
    const x = (index % cols) * (trackWidth + gap)
    const y = Math.floor(index / cols) * (trackHeight + gap)
    const width = trackWidth
    const height = trackHeight
    const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight)
    const drawWidth = image.naturalWidth * scale
    const drawHeight = image.naturalHeight * scale
    const focalX = Math.min(1, Math.max(0, Number(slot.focal_x) || 0.5))
    const focalY = Math.min(1, Math.max(0, Number(slot.focal_y) || 0.5))
    const offsetX = (width - drawWidth) * focalX
    const offsetY = (height - drawHeight) * focalY
    context.save()
    context.beginPath()
    context.rect(x, y, width, height)
    context.clip()
    context.drawImage(image, x + offsetX, y + offsetY, drawWidth, drawHeight)
    context.restore()
  })
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (!blob) {
        reject(new Error('组合图片生成失败'))
        return
      }
      resolve(URL.createObjectURL(blob))
    }, 'image/png')
  })
}

watch(
  () => [
    JSON.stringify(resultPhotoComposition.value || null),
    resultGallerySourceIds.value.join('|'),
    ...resultGallerySourceIds.value.map(id => resultGallerySourceUrls.value[id] || '')
  ].join('|'),
  async () => {
    const generation = ++resultCompositionGeneration
    if (resultCompositionUrl.value) URL.revokeObjectURL(resultCompositionUrl.value)
    resultCompositionUrl.value = ''
    const composition = resultPhotoComposition.value
    if (!composition) {
      resultCompositionLoading.value = false
      return
    }
    if (resultGallerySourceLoading.value || resultGallerySourceIds.value.some(id => !resultGallerySourceUrls.value[id])) {
      resultCompositionLoading.value = true
      return
    }
    resultCompositionLoading.value = true
    try {
      const nextUrl = await renderResultComposition(composition, resultGallerySourceUrls.value)
      if (generation !== resultCompositionGeneration) {
        URL.revokeObjectURL(nextUrl)
        return
      }
      resultCompositionUrl.value = nextUrl
    } catch (error) {
      if (generation === resultCompositionGeneration) message.warning(error.message || '图片组合预览生成失败')
    } finally {
      if (generation === resultCompositionGeneration) resultCompositionLoading.value = false
    }
  },
  { immediate: true }
)

watch(
  () => coverCandidates.value.map(item => item.assetId).join('|'),
  async (assetKey) => {
    const generation = ++coverCandidateLoadGeneration
    Object.values(coverCandidateUrls.value).forEach((url) => URL.revokeObjectURL(url))
    coverCandidateUrls.value = {}
    if (!assetKey) return

    coverCandidatesLoading.value = true
    const nextUrls = {}
    await Promise.all(
      assetKey.split('|').map(async (assetId) => {
        try {
          const response = await contentApi.getCoverAssetFile(assetId)
          nextUrls[assetId] = URL.createObjectURL(await response.blob())
        } catch {
          nextUrls[assetId] = ''
        }
      })
    )
    if (generation !== coverCandidateLoadGeneration) {
      Object.values(nextUrls).forEach((url) => {
        if (url) URL.revokeObjectURL(url)
      })
      return
    }
    coverCandidateUrls.value = nextUrls
    coverCandidatesLoading.value = false
  },
  { immediate: true }
)

watch(
  () => creation.industry_template_id,
  () => {
    const template = selectedTemplate.value
    creation.content_type_code = undefined
    if (template && !creation.content_goal) creation.content_goal = template.default_goal
    if (!store.task) initializeFormValues()
  }
)

watch(selectedHyCanvasTemplateId, initializeHyCanvasFields)
watch(directionOptions, (options) => {
  if (!options.some((item) => item.code === creation.content_type_code)) creation.content_type_code = undefined
})
watch(selectedImageItemId, (itemId) => void loadSelectedImagePreview(itemId))
watch(
  [selectedImageItemId, selectedHyCanvasTemplateId],
  ([imageItemId, templateId]) => {
    window.clearTimeout(compositionPreviewTimer)
    compositionPreviewTimer = window.setTimeout(() => void loadHyCanvasCompositePreview(imageItemId, templateId), 350)
  },
  { deep: true }
)
watch(
  () => store.artifact?.hycanvas_design_snapshot,
  (snapshot) => {
    hycanvasDesign.value = snapshot?.design_id ? snapshot : null
  },
  { immediate: true, deep: true }
)
watch(
  () => store.artifact?.id,
  (artifactId) => {
    if (artifactId) void loadHyCanvasTemplates()
  },
  { immediate: true }
)

onMounted(async () => {
  window.addEventListener('focus', syncPosterTemplatesWhenVisible)
  document.addEventListener('visibilitychange', syncPosterTemplatesWhenVisible)
  posterTemplateSyncTimer = window.setInterval(syncPosterTemplatesWhenVisible, posterTemplateSyncIntervalMs)
  try {
    await store.loadBootstrap()
    if (taskId.value) {
      await store.loadTask(taskId.value)
      if (route.query.hycanvasReturn === '1' && route.query.designId && store.artifact?.id) {
        try {
          const synced = await contentApi.syncHyCanvasDesign(
            String(route.query.designId),
            store.artifact.id
          )
          store.artifact = synced.artifact
          message.success('HyCanvas 编辑结果已更新为当前封面')
        } catch (error) {
          message.error(error.message || 'HyCanvas 编辑结果同步失败')
        } finally {
          const nextQuery = { ...route.query, resultDetail: '1' }
          delete nextQuery.hycanvasReturn
          delete nextQuery.designId
          await router.replace({ query: nextQuery })
        }
      }
      initializeFormValues()
      initializeVisualSelection()
      syncEditor()
      if (route.query.resultDetail === '1' && store.artifact) resultDetailOpen.value = true
      if (stage.value === 1) await loadVisualMaterials()
      if (
        store.task?.latest_run_id &&
        [
          'queued',
          'running',
          'waiting_human',
          'waiting_external',
          'failed',
          'cancelled'
        ].includes(store.task.status)
      ) {
        void store.recoverRun(store.task.latest_run_id)
      } else if (store.task?.latest_run_id) {
        const audit = await store.loadRunAudit(store.task.latest_run_id)
        store.currentRun = {
          run_id: audit.run.id,
          status: audit.run.status,
          request_id: audit.run.request_id
        }
      }
    } else {
      store.resetCurrentTask()
      creation.industry_template_id = creationTemplates.value[0]?.id || ''
      creation.content_goal = selectedTemplate.value?.default_goal || 'acquire'
      initializeFormValues()
    }
  } catch (error) {
    message.error(error.message || '内容工作台加载失败')
  }
})

const createTask = async () => {
  if (!creation.industry_template_id || !creation.content_goal) {
    message.warning('装修与家居模板尚未就绪，请刷新后重试')
    return
  }
  if (needsContentDirection.value && !creation.content_type_code) {
    message.warning('请选择创作类型')
    return
  }
  try {
    const task = await store.createTask({ ...creation, creation_mode: 'viral_rewrite' })
    await router.replace(`/content/tasks/${task.id}`)
    initializeFormValues()
    initializeVisualSelection()
    await loadVisualMaterials()
    message.success('内容任务已创建')
  } catch (error) {
    message.error(error.message || '创建任务失败')
  }
}

const buildBrief = () => ({
  user_request: String(formValues.user_request || '').trim(),
  brand: {},
  audience: [],
  business_variables: {},
  persona: {},
  required_terms: [],
  forbidden_terms: [],
  attachments: [],
  locked_fields: [],
  form_values: { user_request: String(formValues.user_request || '').trim() },
  visual_material: selectedImageItemId.value || selectedHyCanvasTemplateId.value
    ? {
        image_item_id: selectedImageItemId.value || null,
        poster_template_id: null,
        hycanvas_template_id: selectedHyCanvasTemplateId.value,
        photo_composition: photoComposition.value
      }
    : null
})

const scheduleBriefSave = () => {
  if (!store.task || stage.value !== 1) return
  window.clearTimeout(draftSaveTimer)
  draftSaveTimer = window.setTimeout(() => {
    store.saveBrief(buildBrief()).catch(() => {})
  }, 800)
}

watch(formValues, scheduleBriefSave, { deep: true })
watch([selectedImageItemId, selectedHyCanvasTemplateId, photoComposition], scheduleBriefSave, { deep: true })
onBeforeUnmount(() => {
  window.clearTimeout(draftSaveTimer)
  window.clearTimeout(compositionPreviewTimer)
  window.clearTimeout(workflowNarrativeTimer)
  window.clearInterval(posterTemplateSyncTimer)
  window.removeEventListener('focus', syncPosterTemplatesWhenVisible)
  document.removeEventListener('visibilitychange', syncPosterTemplatesWhenVisible)
  coverLoadGeneration += 1
  resultVersionCoverGeneration += 1
  resultGalleryLoadGeneration += 1
  resultCompositionGeneration += 1
  coverCandidateLoadGeneration += 1
  materialPreviewGeneration += 1
  selectedImagePreviewGeneration += 1
  hycanvasCompositePreviewGeneration += 1
  posterPreviewGeneration += 1
  hycanvasTemplateLoadGeneration += 1
  if (coverUrl.value) URL.revokeObjectURL(coverUrl.value)
  Object.values(resultVersionCoverUrls.value).filter(Boolean).forEach(URL.revokeObjectURL)
  Object.values(resultGallerySourceUrls.value).filter(Boolean).forEach(URL.revokeObjectURL)
  if (resultCompositionUrl.value) URL.revokeObjectURL(resultCompositionUrl.value)
  Object.values(coverCandidateUrls.value).forEach((url) => URL.revokeObjectURL(url))
  revokePreviewUrls(materialImageUrls.value)
  if (selectedImagePreviewUrl.value) URL.revokeObjectURL(selectedImagePreviewUrl.value)
  if (hycanvasCompositePreviewUrl.value) URL.revokeObjectURL(hycanvasCompositePreviewUrl.value)
  revokePreviewUrls(posterTemplateUrls.value)
  Object.values(hycanvasTemplateUrls.value).forEach((url) => {
    if (url?.startsWith('blob:')) URL.revokeObjectURL(url)
  })
})

const compileBrief = async () => {
  if (!String(formValues.user_request || '').trim()) {
    message.warning('请填写内容需求')
    return
  }
  if (photoComposition.value?.slots.some(slot => !slot.image_item_id)) {
    message.warning('请填满图片组合的所有位置')
    return
  }
  if (!selectedHyCanvasTemplateId.value) {
    message.warning('请选择一个 HyCanvas 小红书模板')
    return
  }
  try {
    window.clearTimeout(draftSaveTimer)
    await store.compileBrief(buildBrief())
    stage.value = 2
    message.success('业务简报已形成，可启动 V3 内容工作流')
  } catch (error) {
    const missingFields = error.response?.data?.detail?.error?.fields
    message.error(missingFields?.length
      ? `请补充：${missingFields.map(field => field.label || field.field).join('、')}`
      : error.message || '请补充必填业务信息')
  }
}

const startGeneration = async () => {
  try {
    await store.startRun(modelSpec.value)
  } catch (error) {
    message.error(error.message || '启动内容生成失败')
  }
}

const retryFailedRun = async () => {
  try {
    await store.retryNode(failedNodeId.value, modelSpec.value)
    message.success('已从失败 checkpoint 继续执行')
  } catch (error) {
    message.error(error.message || '失败节点重试失败')
  }
}

const submitHumanReview = async () => {
  try {
    const metadata = {
      run_id: store.interrupt?.run_id,
      node_id: store.interrupt?.node_id,
      expected_state_version: store.interrupt?.expected_state_version
    }
    if (store.interrupt?.interrupt_type === 'content_direction') {
      const selected = angleOptions.value.find((item) => item.direction_code === selectedAngleId.value)
      if (!selected) {
        message.warning('请选择一个内容方向')
        return
      }
      await store.resumeRun({
        ...metadata,
        direction_code: selected.direction_code
      })
      return
    }
    if (['high_risk_facts', 'strategy_product_facts'].includes(store.interrupt?.interrupt_type)) {
      await store.resumeRun({
        ...metadata,
        confirmed_evidence_ids: confirmedEvidenceIds.value
      })
      return
    }
    if (store.interrupt?.interrupt_type === 'formula_selection') {
      if (!selectedTitleFormulaCode.value || !selectedBodyFormulaCode.value) {
        message.warning('请各选择一个标题公式和正文公式')
        return
      }
      await store.resumeRun({
        ...metadata,
        title_formula_code: selectedTitleFormulaCode.value,
        body_formula_code: selectedBodyFormulaCode.value
      })
      return
    }
    if (store.interrupt?.interrupt_type === 'content_correction') {
      await store.resumeRun({ ...metadata, decision: 'revise' })
      return
    }
    if (store.interrupt?.interrupt_type === 'cover_selection') {
      if (!coverSelectionAllowed.value) {
        message.warning('请选择一张封面')
        return
      }
      await store.resumeRun({ ...metadata, asset_id: selectedCoverAssetId.value })
      return
    }
    if (!selectedTitleId.value) {
      message.warning('请选择一个标题')
      return
    }
    await store.resumeRun({
      ...metadata,
      title_id: selectedTitleId.value
    })
  } catch (error) {
    message.error(error.message || '恢复工作流失败')
  }
}

const submitHumanApproval = async (approved) => {
  try {
    await store.resumeRun({
      run_id: store.interrupt?.run_id,
      node_id: store.interrupt?.node_id,
      expected_state_version: store.interrupt?.expected_state_version,
      decision: approved ? 'approved' : 'rejected',
      note: approvalNote.value.trim() || null
    })
  } catch (error) {
    message.error(error.message || '提交人工审批失败')
  }
}

const saveArtifact = async () => {
  try {
    await store.saveArtifact({ ...editor })
    syncEditor()
    message.success('已保存新的内容版本')
  } catch (error) {
    message.error(error.message || '保存内容失败')
  }
}

const scrollAiEditHistoryToEnd = () => {
  void nextTick(() => {
    aiEditHistoryElement.value?.lastElementChild?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
}

const submitAiEdit = async () => {
  const instruction = aiEditInstruction.value.trim()
  if (!instruction || !aiEditReady.value || store.loading.refining) return
  const pending = {
    id: `ai-edit-${Date.now()}`,
    instruction,
    status: 'running',
    message: '正在根据你的要求修改内容...'
  }
  aiEditInstruction.value = ''
  pendingAiEdit.value = pending
  scrollAiEditHistoryToEnd()
  try {
    const response = await store.aiEditArtifact(instruction, modelSpec.value)
    pendingAiEdit.value = null
    syncEditor()
    message.success(response.reply)
  } catch (error) {
    if (error.response?.status === 409) {
      await store.loadTask(store.task.id)
      syncEditor()
    }
    const errorMessage = error.message || 'AI 修改内容失败'
    pendingAiEdit.value = { ...pending, status: 'failed', message: `修改失败：${errorMessage}` }
    aiEditInstruction.value = instruction
    message.error(errorMessage)
  } finally {
    scrollAiEditHistoryToEnd()
  }
}

const handleAiEditKeydown = (event) => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void submitAiEdit()
}

const copyResultText = async (value, label) => {
  try {
    await navigator.clipboard.writeText(value)
    message.success(`${label}已复制`)
  } catch {
    message.error(`${label}复制失败，请稍后重试`)
  }
}

const showPreviousResultImage = () => {
  resultGalleryIndex.value = Math.max(0, resultGalleryIndex.value - 1)
}

const showNextResultImage = () => {
  resultGalleryIndex.value = Math.min(resultGalleryItems.value.length - 1, resultGalleryIndex.value + 1)
}

const openViralReference = async () => {
  if (!store.artifact?.id || !hasViralReference.value) return
  resultPreviewTab.value = 'viral-reference'
  if (viralReference.value?.artifactId === store.artifact.id) return
  viralReferenceLoading.value = true
  try {
    const response = await contentApi.getArtifactViralReference(store.artifact.id)
    viralReference.value = { ...response.reference, artifactId: store.artifact.id }
  } catch (error) {
    resultPreviewTab.value = 'cover'
    message.error(error.message || '爆款原文加载失败')
  } finally {
    viralReferenceLoading.value = false
  }
}

const openCoverEditor = async () => {
  const snapshot = store.artifact?.hycanvas_design_snapshot
  if (snapshot?.design_id && snapshot?.editor_url) {
    const returnRoute = router.resolve({
      path: route.path,
      query: {
        ...route.query,
        resultDetail: '1',
        hycanvasReturn: '1',
        designId: snapshot.design_id
      }
    })
    const sessionKey = crypto.randomUUID()
    sessionStorage.setItem(
      `hycanvas-editor:${sessionKey}`,
      JSON.stringify({
        designId: snapshot.design_id,
        artifactId: store.artifact.id,
        returnUrl: new URL(returnRoute.href, window.location.origin).toString(),
        returnLabel: '返回内容结果'
      })
    )
    await router.push({ name: 'HyCanvasWorkspace', query: { session: sessionKey } })
    return
  }
  const assetId = store.artifact?.cover_asset_id
  if (!assetId) {
    message.warning('当前内容还没有可编辑封面')
    return
  }
  resultDetailOpen.value = false
  await router.push({
    name: 'ContentCoverEditor',
    params: { assetId },
    query: {
      taskId: store.task?.id || '',
      artifactId: store.artifact?.id || ''
    }
  })
}

const reviewArtifact = async () => {
  try {
    await store.reviewArtifact(modelSpec.value)
    syncEditor()
    message.success('内容审核完成')
  } catch (error) {
    message.error(error.message || '内容审核失败')
  }
}

const finalizeArtifact = async () => {
  try {
    await store.finalizeArtifact()
    await router.push(`/content/results/${store.task.id}`)
    message.success('已保存为正式内容资产')
  } catch (error) {
    message.error(error.message || '保存正式版本失败')
  }
}

const openVersions = async () => {
  await store.loadVersions()
  versionDrawerOpen.value = true
}
</script>

<template>
  <div
    ref="studioPageElement"
    class="content-studio-page"
    :class="{ 'studio-production': stage === 2 }"
    @scroll.passive="trackWorkflowScroll"
  >
    <header class="studio-header">
      <div>
        <div class="header-kicker">Yuxi Content Strategy Studio</div>
        <h1>{{ store.task?.name || '新建内容任务' }}</h1>
        <p>规则、事实和知识同源，关键节点由人确认。</p>
      </div>
    </header>

    <main v-if="!store.loading.bootstrap" class="studio-main">
      <ContentStudioToolbar
        v-if="stage !== 2 || (!store.currentRun && !store.interrupt)"
        :has-task="Boolean(store.task)"
        :is-admin="userStore.isAdmin"
        @recognize-image="ocrModalOpen = true"
      />
      <section v-if="stage === 1" class="stage-panel">
        <div class="panel-heading">
          <div><span>阶段 1</span><h2>业务素材与事实简报</h2></div>
          <p>先确定企业真实业务资料，后续标题与正文共享同一份事实。</p>
        </div>

        <template v-if="!store.task">
          <div class="setup-grid">
            <div class="field-block">
              <span id="creation-mode-label">创作模式</span>
              <div class="creation-mode-options" role="status">
                <span class="creation-mode-card selected">爆款仿写</span>
              </div>
              <small>系统比较已准备的完整文章参考，复用选中结构，业务事实来自本次真实资料。</small>
            </div>
            <p v-if="selectedTemplate?.blueprint_first" class="auto-strategy-hint">选择创作类型并填写资料后，系统自动匹配参考结构与创作方式。</p>
            <p v-else-if="selectedTemplate && !selectedTemplate.blueprint_first">根据本次资料评分选择该行业的公式和创作手法。</p>
          </div>

          <div class="template-grid">
            <button
              v-for="item in creationTemplates"
              :key="item.id"
              type="button"
              class="template-card"
              :class="{ selected: creation.industry_template_id === item.id }"
              @click="creation.industry_template_id = item.id; creation.content_goal = item.default_goal"
            >
              <strong>{{ item.name }}</strong>
              <span>{{ item.description }}</span>
            </button>
          </div>

          <div v-if="needsContentDirection" class="field-block creation-type-field">
            <span id="creation-type-label">创作类型</span>
            <a-radio-group v-model:value="creation.content_type_code" aria-labelledby="creation-type-label">
              <a-radio-button v-for="item in directionOptions" :key="item.code" :value="item.code">
                {{ item.name }}
              </a-radio-button>
            </a-radio-group>
          </div>

          <div class="stage-actions">
            <a-button type="primary" :loading="store.loading.saving" @click="createTask">
              创建任务并填写素材
            </a-button>
          </div>
        </template>

        <template v-else>
          <div class="brief-layout">
            <div class="form-card">
              <div class="mode-row">
                <span class="mode-badge">{{ isQuickMode ? '简化版' : '专业版' }}</span>
                <span class="mode-badge">
                  爆款仿写
                </span>
                <span>{{ store.template?.name }}</span>
                <small v-if="saveStatusLabel" :class="{ 'save-error': store.saveStatus === 'error' }">{{ saveStatusLabel }}</small>
              </div>
              <div class="dynamic-form">
                <label class="field-block">
                  <span>内容需求</span>
                  <a-textarea
                    v-model:value="formValues.user_request"
                    :rows="6"
                    placeholder="请直接描述想要生成的内容、业务信息和特殊要求"
                  />
                </label>
              </div>
            </div>
          </div>
          <section class="visual-material-card">
            <div class="visual-material-heading">
              <div>
                <span class="section-kicker">视觉素材</span>
                <h3>选择图库与封面模板</h3>
                <p>可选图库图片作为 HyCanvas 封面主图；内容生成并审核通过后，系统会按所选模板生成可继续编辑的封面。</p>
              </div>
              <a-button @click="router.push('/materials/images')">
                <FolderOpen :size="15" />管理素材库
              </a-button>
            </div>

            <a-spin :spinning="materialSelectorLoading">
              <div class="material-selector-block">
                <div class="material-selector-title">
                  <div><Image :size="18" /><strong>选择图库图片</strong><em>可选 · 多选</em></div>
                  <small>如需使用图库素材可选择，首张图片作为封面原图，最多可同时选择 9 张。</small>
                </div>
                <div v-if="rootMaterialGalleries.length" class="gallery-folder-grid" aria-label="素材图库">
                  <button
                    v-for="gallery in rootMaterialGalleries"
                    :key="gallery.id"
                    type="button"
                    class="gallery-folder-card"
                    :class="{ selected: selectedImageItemId && selectedImageRootGalleryId === gallery.id }"
                    @click="openGallery(gallery.id)"
                  >
                    <span class="gallery-folder-icon"><Folder :size="30" /></span>
                    <span class="gallery-folder-copy">
                      <strong>{{ gallery.name }}</strong>
                      <small>{{ gallery.count }} 张图片素材</small>
                    </span>
                    <span v-if="selectedImageItemId && selectedImageRootGalleryId === gallery.id" class="gallery-selected-badge">
                      已选择
                    </span>
                  </button>
                </div>
                <a-empty v-else description="素材库中还没有图库" />
                <ContentPhotoComposition v-model="photoComposition" v-model:primary-image-id="selectedImageItemId" :layouts="photoLayouts" @select="selectCompositionImage" />
                <div v-if="selectedImageItemId" class="selected-gallery-image">
                  <div class="selected-gallery-preview-grid" aria-label="封面预览">
                    <div class="selected-gallery-preview-card">
                      <span class="selected-gallery-preview-media">
                        <LoaderCircle v-if="selectedImagePreviewLoading" class="spin" :size="18" />
                        <button
                          v-else-if="selectedImagePreviewUrl"
                          type="button"
                          class="selected-gallery-preview-trigger"
                          aria-label="放大查看封面原图"
                          @click="openImagePreview(
                            selectedImagePreviewUrl,
                            '封面原图',
                            selectedImageSummary?.name || '封面原图预览'
                          )"
                        >
                          <img
                            :src="selectedImagePreviewUrl"
                            :alt="selectedImageSummary?.name || '封面原图预览'"
                          />
                          <span class="selected-gallery-preview-zoom" aria-hidden="true">
                            <ZoomIn :size="20" />
                          </span>
                        </button>
                        <Image v-else :size="20" />
                      </span>
                      <strong>封面原图</strong>
                    </div>
                    <div class="selected-gallery-preview-card">
                      <span class="selected-gallery-preview-media">
                        <LoaderCircle v-if="hycanvasCompositePreviewLoading" class="spin" :size="18" />
                        <button
                          v-else-if="hycanvasCompositePreviewUrl"
                          type="button"
                          class="selected-gallery-preview-trigger"
                          aria-label="放大查看模板叠加效果"
                          @click="openImagePreview(
                            hycanvasCompositePreviewUrl,
                            '模板叠加效果',
                            `${selectedHyCanvasTemplate?.title || '模板'}合成效果`
                          )"
                        >
                          <img
                            :src="hycanvasCompositePreviewUrl"
                            :alt="`${selectedHyCanvasTemplate?.title || '模板'}合成效果`"
                          />
                          <span class="selected-gallery-preview-zoom" aria-hidden="true">
                            <ZoomIn :size="20" />
                          </span>
                        </button>
                        <LayoutTemplate v-else :size="22" />
                      </span>
                      <strong>模板叠加效果</strong>
                      <small>{{ selectedHyCanvasTemplate?.title || '选择模板后生成' }}</small>
                    </div>
                  </div>
                  <a-button
                    v-if="selectedImageGalleryId"
                    type="link"
                    @click="openGallery(selectedImageGalleryId)"
                  >
                    更换
                  </a-button>
                  <a-button type="link" danger @click="clearSelectedGalleryImage">清除</a-button>
                </div>
              </div>

              <div class="material-selector-block template-selector-block">
                <div class="material-selector-title">
                  <div>
                    <LayoutTemplate :size="18" /><strong>HyCanvas 小红书模板专区</strong><em>必选 · 单选</em>
                  </div>
                  <small>标题、副标题和图库原图将在内容生成后自动填入，并保留可编辑设计稿。</small>
                </div>
                <div class="poster-choice-grid">
                  <button
                    v-for="item in hycanvasTemplates"
                    :key="item.id"
                    type="button"
                    class="poster-choice"
                    :class="{ selected: selectedHyCanvasTemplateId === item.id }"
                    :aria-pressed="selectedHyCanvasTemplateId === item.id"
                    @click="selectedHyCanvasTemplateId = item.id"
                  >
                    <span class="poster-preview">
                      <img v-if="hycanvasTemplateUrls[item.id]" :src="hycanvasTemplateUrls[item.id]" :alt="item.title" />
                      <LayoutTemplate v-else :size="24" />
                    </span>
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.format?.width }} × {{ item.format?.height }}</small>
                    <CheckCircle2
                      v-if="selectedHyCanvasTemplateId === item.id"
                      class="choice-check"
                      :size="20"
                    />
                  </button>
                </div>
                <a-empty v-if="!hycanvasTemplates.length" description="HyCanvas 尚未配置或暂无小红书模板" />
              </div>
            </a-spin>
          </section>
          <div class="stage-actions">
            <a-button type="primary" :loading="store.loading.saving" @click="compileBrief">
              形成事实简报并进入 V3 生产
            </a-button>
          </div>
        </template>
      </section>

      <section
        v-else-if="stage === 2"
        class="stage-panel"
        :class="{ 'completion-stage': workflowCompleted }"
      >
        <div v-if="!store.currentRun && !store.interrupt" class="generation-start">
          <Sparkles :size="30" />
          <h3>事实简报已锁定</h3>
          <p>固定工作流会在动态节点调用 Agent，Agent 再使用 Skill、知识库和工具，关键选择会暂停等待人工确认。</p>
          <a-input v-if="!isQuickMode" v-model:value="modelSpec" placeholder="可选：指定模型 spec；留空使用系统默认模型" />
          <a-button type="primary" size="large" @click="startGeneration"><Play :size="17" />开始生成</a-button>
        </div>

        <div
          v-else
          class="run-layout"
          :class="{
            'completion-layout': workflowCompleted,
            'active-run-layout': !workflowCompleted
          }"
        >
          <template v-if="workflowCompleted">
            <div class="completion-left completion-conversation">
              <div class="workflow-stream">
                <section class="codex-workflow-status completed" aria-live="polite">
                  <div class="workflow-narrative completion-narrative">
                    <div v-if="streamedWorkflowNarrativeBeforeStrategy" class="workflow-narrative-copy-wrap">
                      <MarkdownPreview compact :content="streamedWorkflowNarrativeBeforeStrategy" />
                    </div>
                    <ContentWorkflowStrategyPanel
                      v-if="workflowStrategyPanelVisible"
                      :presentation="workflowStrategyPresentation"
                      :evidence-groups="[]"
                    />
                    <div v-if="streamedWorkflowNarrativeAfterStrategy" class="workflow-narrative-copy-wrap">
                      <MarkdownPreview compact :content="streamedWorkflowNarrativeAfterStrategy" />
                    </div>
                    <ContentWorkflowStrategyPanel
                      :presentation="workflowEvidenceOnlyPresentation"
                      :evidence-groups="workflowEvidenceGroups"
                    />
                    <ContentStrategyDecision :task-id="store.task?.id" :run-status="workflowRunStatus" :field-labels="evidenceFieldLabels" />
                    <div class="workflow-complete-line">
                      <CheckCircle2 :size="16" />
                      <strong>内容生成完成</strong>
                    </div>
                  </div>
                </section>
              </div>
              <div ref="aiEditHistoryElement" v-if="aiEditHistory.length" class="ai-edit-history" aria-live="polite">
                <div
                  v-for="item in aiEditHistory"
                  :key="item.id"
                  class="ai-edit-message"
                  :class="[item.role, item.status]"
                >
                  <template v-if="item.status === 'running'">
                    <LoaderCircle class="spin" :size="15" />
                    <span>{{ item.content }}</span>
                  </template>
                  <template v-else>{{ item.content }}</template>
                </div>
              </div>
              <section class="workflow-chat-panel" aria-label="AI 修改内容">
                <AgentInputArea
                  v-model="aiEditInstruction"
                  :disabled="!aiEditReady || store.loading.refining"
                  :send-button-disabled="!aiEditReady || store.loading.refining || !aiEditInstruction.trim()"
                  :placeholder="store.loading.refining ? 'AI 正在执行修改，请稍候' : aiEditPlaceholder"
                  @send="submitAiEdit"
                  @keydown="handleAiEditKeydown"
                >
                  <template #toolbar>
                    <ContentStudioToolbar :has-task="Boolean(store.task)" :is-admin="userStore.isAdmin" @recognize-image="ocrModalOpen = true" />
                  </template>
                  <template #actions-right-extra>
                    <span v-if="store.loading.refining" class="ai-edit-running" role="status">
                      <LoaderCircle class="spin" :size="15" />正在执行修改
                    </span>
                  </template>
                </AgentInputArea>
              </section>
            </div>

            <aside class="completion-results">
              <div class="completion-results-heading">
                <div>
                  <span class="completion-results-icon"><WandSparkles :size="16" /></span>
                  <strong>查看生成结果</strong>
                </div>
                <span>实时更新</span>
              </div>
              <article
                v-for="item in completionResults"
                :key="item.id"
                class="completion-result-item"
                :class="{ current: item.isCurrent }"
              >
                <div class="completion-result-main">
                  <div class="completion-result-meta">
                    <span>{{ resultCategoryLabel }}</span>
                    <div class="completion-result-meta-details">
                      <small v-if="resultLocation">{{ resultLocation }}</small>
                      <small>{{ resultTime(item) }}</small>
                    </div>
                  </div>
                  <h3>{{ item.title }}</h3>
                  <div class="completion-result-content">
                    <div class="completion-result-copy">
                      <div class="completion-result-body" :class="{ expanded: isResultExpanded(item.id) }">
                        <MarkdownPreview :content="item.body || ''" />
                      </div>
                      <button
                        v-if="item.body"
                        type="button"
                        class="completion-result-expand"
                        @click="toggleResultExpanded(item.id)"
                      >
                        {{ isResultExpanded(item.id) ? '收起全文' : '展开全文' }}
                      </button>
                    </div>
                    <div class="completion-result-media">
                      <img
                        v-if="resultCoverUrl(item)"
                        class="completion-result-cover"
                        :src="resultCoverUrl(item)"
                        :alt="`${item.isCurrent ? '当前' : '历史'}内容封面`"
                      />
                      <div v-else class="completion-result-cover-placeholder">
                        <Image :size="20" />
                        <span>{{ resultCoverLoading(item) ? '封面加载中' : '暂无封面' }}</span>
                      </div>
                    </div>
                  </div>
                </div>
                <div class="completion-result-actions">
                  <template v-if="item.isCurrent">
                    <a-button @click="resultDetailOpen = true">查看详情</a-button>
                    <a-button type="primary" @click="publishModalOpen = true">发布</a-button>
                  </template>
                  <a-button v-else class="completion-version-button" @click="openVersions">版本记录</a-button>
                </div>
              </article>
            </aside>
          </template>

          <template v-else>
            <div class="workflow-stream">
            <Transition name="workflow-list" mode="out-in">
              <section
                v-if="activeWorkflowGroup"
                :key="activeWorkflowGroup.id"
                class="codex-workflow-status"
                :class="activeWorkflowGroup.status"
                aria-live="polite"
              >
                <div class="codex-workflow-heading">
                  <span
                    v-if="!['running', 'active'].includes(activeWorkflowGroup.status)"
                    class="codex-workflow-icon"
                  >
                    <CircleAlert v-if="activeWorkflowGroup.status === 'failed'" :size="17" />
                    <Clock3 v-else :size="17" />
                  </span>
                  <span class="codex-workflow-copy">
                    <strong>{{ activeWorkflowGroup.label }}</strong>
                  </span>
                </div>
                <div class="workflow-narrative" aria-live="polite" aria-atomic="false">
                  <div v-if="streamedWorkflowNarrativeBeforeStrategy" class="workflow-narrative-copy-wrap">
                    <MarkdownPreview compact :content="streamedWorkflowNarrativeBeforeStrategy" />
                  </div>
                  <ContentWorkflowStrategyPanel
                    v-if="workflowStrategyPanelVisible"
                    :presentation="workflowStrategyPresentation"
                    :evidence-groups="[]"
                  />
                  <div v-if="streamedWorkflowNarrativeAfterStrategy" class="workflow-narrative-copy-wrap">
                    <MarkdownPreview compact :content="streamedWorkflowNarrativeAfterStrategy" />
                  </div>
                  <div v-if="workflowNarrativeActive && streamedWorkflowNarrative" class="workflow-thinking-row">
                    <span class="workflow-thinking-indicator" role="status">
                      <LoaderCircle class="spin" :size="14" aria-hidden="true" />
                      <span>正在思考</span>
                      <span class="workflow-thinking-dots" aria-hidden="true">
                        <i></i><i></i><i></i>
                      </span>
                    </span>
                  </div>
                  <div v-else-if="!streamedWorkflowNarrative" class="workflow-awaiting-event">
                    <LoaderCircle class="spin" :size="15" />
                    <span>正在分析现有资料，稍后会在这里持续输出有效信息…</span>
                  </div>
                  <ContentWorkflowStrategyPanel
                    :presentation="workflowEvidenceOnlyPresentation"
                    :evidence-groups="workflowEvidenceGroups"
                  />
                </div>
                  <ContentStrategyDecision :task-id="store.task?.id" :run-status="workflowRunStatus" :field-labels="evidenceFieldLabels" />
              </section>
            </Transition>

          <div v-if="store.interrupt?.interrupt_type === 'content_direction'" class="human-review-card">
            <div class="human-heading"><Sparkles :size="20" /><div><h3>选择本次内容方向</h3><p>Agent 已根据目标、事实和证据生成候选方向，选择后将由固定规则匹配组合组。</p></div></div>
            <a-radio-group v-model:value="selectedAngleId" class="title-options angle-options">
              <a-radio v-for="item in angleOptions" :key="item.direction_code" :value="item.direction_code">
                <strong>{{ item.direction_code }}</strong>
                <span>{{ item.reason }}</span>
                <div v-if="item.evidence_ids?.length" class="evidence-references">
                  <div class="evidence-references-title">引用证据</div>
                  <ol>
                    <li v-for="(evidenceId, index) in item.evidence_ids" :key="evidenceId">
                      {{ evidenceReferenceText(evidenceId, index) }}
                    </li>
                  </ol>
                </div>
              </a-radio>
            </a-radio-group>
            <a-button type="primary" :disabled="!selectedAngleId" @click="submitHumanReview">确认方向并继续</a-button>
          </div>

          <div v-else-if="store.interrupt?.interrupt_type === 'title_selection'" class="human-review-card">
            <div class="human-heading"><Send :size="20" /><div><h3>请选择最终标题</h3><p>选择后 LangGraph 从 checkpoint 恢复，无需重新检索证据。</p></div></div>
            <a-radio-group v-model:value="selectedTitleId" class="title-options">
              <a-radio v-for="item in titleOptions" :key="item.id" :value="item.id">
                <strong>{{ item.text }}</strong>
                <span>{{ item.formula_code }} · 引用 {{ item.evidence_ids?.length || 0 }} 条证据</span>
              </a-radio>
            </a-radio-group>
            <a-button type="primary" @click="submitHumanReview">确认标题并继续生成</a-button>
          </div>

          <div v-else-if="['high_risk_facts', 'strategy_product_facts'].includes(store.interrupt?.interrupt_type)" class="human-review-card">
            <div class="human-heading"><ShieldCheck :size="20" /><div><h3>确认关键事实</h3><p>价格、优惠、效果或高风险表达必须逐项确认后才能进入冻结证据并用于生成。</p></div></div>
            <a-checkbox-group v-model:value="confirmedEvidenceIds" class="fact-options">
              <a-checkbox v-for="item in store.interrupt.evidence_ids" :key="item" :value="item">
                <strong>{{ store.interrupt.node_id === 'confirm_strategy_prices' ? evidenceItemsById.get(item)?.value : evidenceReferenceText(item) }}</strong>
                <div v-if="store.interrupt.node_id === 'confirm_strategy_prices'">
                  <p>{{ evidenceItemsById.get(item)?.metadata?.price_basis === 'standard_unit_price' ? '标准单价参考，不代表本项目实际成交费用' : '本项目报价资料' }}</p>
                  <p>范围：{{ evidenceItemsById.get(item)?.metadata?.scope }} · 单位：{{ evidenceItemsById.get(item)?.metadata?.unit }}</p>
                  <p>来源：{{ evidenceItemsById.get(item)?.metadata?.document_name || evidenceItemsById.get(item)?.metadata?.filename || evidenceItemsById.get(item)?.source_id }}</p>
                </div>
              </a-checkbox>
            </a-checkbox-group>
            <a-button type="primary" :disabled="store.interrupt.node_id === 'confirm_strategy_prices' && confirmedEvidenceIds.length !== store.interrupt.evidence_ids.length" @click="submitHumanReview">确认选中事实并继续</a-button>
          </div>

          <div v-else-if="store.interrupt?.interrupt_type === 'formula_selection'" class="human-review-card">
            <div class="human-heading"><Sparkles :size="20" /><div><h3>选择公式对</h3><p>专业模式下，从当前组合组的合法候选池中各选一个标题公式和正文公式。</p></div></div>
            <label class="field-block"><span>标题公式</span>
              <a-select v-model:value="selectedTitleFormulaCode">
                <a-select-option v-for="code in store.interrupt.title_formula_codes" :key="code" :value="code">{{ code }}</a-select-option>
              </a-select>
            </label>
            <label class="field-block"><span>正文公式</span>
              <a-select v-model:value="selectedBodyFormulaCode">
                <a-select-option v-for="code in store.interrupt.body_formula_codes" :key="code" :value="code">{{ code }}</a-select-option>
              </a-select>
            </label>
            <a-button type="primary" @click="submitHumanReview">锁定公式并继续</a-button>
          </div>

          <div v-else-if="store.interrupt?.interrupt_type === 'content_approval'" class="human-review-card">
            <div class="human-heading"><ShieldCheck :size="20" /><div><h3>最终人工审批</h3><p>请根据审核结果确认是否允许保存内容资产。</p></div></div>
            <div v-if="store.interrupt.review_report?.checks?.length" class="approval-checks">
              <div v-for="check in store.interrupt.review_report.checks" :key="`${check.code}-${check.message}`">
                <strong>{{ check.message }}</strong>
                <span v-if="check.suggestion">{{ check.suggestion }}</span>
              </div>
            </div>
            <a-textarea v-model:value="approvalNote" :rows="3" placeholder="可选：填写审批备注" />
            <div class="approval-actions">
              <a-button danger @click="submitHumanApproval(false)">驳回</a-button>
              <a-button type="primary" :disabled="!approvalAllowed" @click="submitHumanApproval(true)">通过并继续</a-button>
            </div>
          </div>

          <div v-else-if="store.interrupt?.interrupt_type === 'content_correction'" class="human-review-card">
            <div class="human-heading"><CircleAlert :size="20" /><div><h3>内容需要定点回修</h3><p>确定性校验或语义审核发现阻断问题。确认后只重跑建议节点及其下游。</p></div></div>
            <div class="approval-checks">
              <div v-for="check in correctionChecks" :key="`${check.code}-${check.location || 'content'}`">
                <strong>{{ check.message || check.code }}</strong>
                <span v-if="check.suggestion">{{ check.suggestion }}</span>
              </div>
            </div>
            <p class="correction-target">回修原因：{{ store.interrupt.reason_code }} · 目标节点：{{ store.interrupt.suggested_target }}</p>
            <div class="approval-actions">
              <a-button type="primary" @click="submitHumanReview">确认并按建议重新生成</a-button>
            </div>
          </div>

          <div v-else-if="store.interrupt?.interrupt_type === 'cover_selection'" class="human-review-card">
            <div class="human-heading"><Send :size="20" /><div><h3>选择最终封面</h3><p>选择生成的封面，确认后保存。</p></div></div>
            <a-radio-group v-model:value="selectedCoverAssetId" class="title-options cover-options">
              <a-radio v-for="(candidate, index) in coverCandidates" :key="candidate.assetId" :value="candidate.assetId">
                <div class="cover-option">
                  <div class="cover-candidate-preview">
                    <img v-if="coverCandidateUrls[candidate.assetId]" :src="coverCandidateUrls[candidate.assetId]" :alt="`封面候选 ${index + 1}`" />
                    <div v-else class="cover-candidate-placeholder">
                      <LoaderCircle v-if="coverCandidatesLoading" class="spin" :size="22" />
                      <span v-else>封面暂时无法预览</span>
                    </div>
                  </div>
                  <strong>封面候选 {{ index + 1 }}</strong>
                </div>
              </a-radio>
            </a-radio-group>
            <a-button type="primary" :disabled="!coverSelectionAllowed" @click="submitHumanReview">确认封面并保存</a-button>
          </div>

          <div v-else-if="store.interrupt?.interrupt_type === 'external_wait'" class="running-card external-wait-card">
            <LoaderCircle class="spin" :size="26" />
            <h3>封面正在生成</h3>
            <p>这是系统等待节点，无需人工操作；封面服务完成后会自动继续后续审核。</p>
            <a-progress :percent="externalWaitProgress" :show-info="externalWaitProgress > 0" status="active" />
            <small>{{ externalWaitStatusLabel }}</small>
          </div>

          <div v-else-if="runFailed" class="running-card failure-card">
            <CircleAlert :size="26" />
            <h3>工作流执行失败</h3>
            <p>{{ store.task?.error?.message || store.lastError?.message || '已保留完成节点和 checkpoint，可从失败节点恢复。' }}</p>
            <section v-if="blockedTitleCandidates.length" class="title-validation-failures">
              <h4>标题候选校验明细</h4>
              <ol>
                <li v-for="item in blockedTitleCandidates" :key="item.id">
                  <strong>{{ item.text || item.id }}</strong>
                  <ul>
                    <li v-for="check in item.checks" :key="`${item.id}-${check.code}-${check.message}`">
                      <span>{{ check.message }}</span>
                      <small v-if="check.suggestion">{{ check.suggestion }}</small>
                    </li>
                  </ul>
                </li>
              </ol>
            </section>
            <a-button type="primary" @click="retryFailedRun"><RefreshCw :size="15" />从失败节点重试</a-button>
          </div>

          <div v-else-if="store.interrupt" class="running-card failure-card">
            <CircleAlert :size="26" />
            <h3>遇到未支持的人工节点</h3>
            <p>节点类型：{{ store.interrupt.interrupt_type || '未知' }}。请联系管理员检查工作流版本。</p>
          </div>

          <div v-else-if="!store.loading.running" class="running-card">
            <LoaderCircle class="spin" :size="26" />
            <h3>工作流正在执行</h3>
            <p>可以离开页面，任务状态与节点结果会持续保存。</p>
          </div>
            </div>

          <section class="workflow-chat-panel" aria-label="AI 修改内容">
            <AgentInputArea
              v-model="aiEditInstruction"
              disabled
              send-button-disabled
              :placeholder="aiEditPlaceholder"
            >
              <template #toolbar>
                <ContentStudioToolbar :has-task="Boolean(store.task)" :is-admin="userStore.isAdmin" @recognize-image="ocrModalOpen = true" />
              </template>
            </AgentInputArea>
            <p>流程执行完成后，可在这里要求 AI 修改标题、正文或话题。</p>
          </section>
          </template>
        </div>
      </section>

      <section v-else class="stage-panel">
        <div class="panel-heading">
          <div><span>阶段 3</span><h2>审核、编辑与正式版本</h2></div>
          <div class="status-badge" :class="store.artifact?.review_snapshot?.status || 'pending'">
            {{ store.artifact?.review_snapshot?.status || '待审核' }}
          </div>
        </div>

        <div v-if="store.artifact" class="review-layout">
          <div class="content-editor-card">
            <section v-if="store.artifact.cover_asset_id" class="artifact-cover-preview">
              <div class="artifact-cover-heading">
                <div><strong>本次生成封面</strong><span>已与当前内容版本绑定</span></div>
                <small>{{ store.artifact.cover_asset_id }}</small>
              </div>
              <div v-if="coverLoading" class="cover-preview-loading">
                <LoaderCircle class="spin" :size="24" />正在加载封面
              </div>
              <img v-else-if="coverUrl" :src="coverUrl" alt="本次生成封面" />
              <a-empty v-else description="封面暂时无法预览" />
            </section>
            <section class="hycanvas-panel">
              <div class="hycanvas-heading">
                <div>
                  <span class="section-kicker">可编辑视觉稿</span>
                  <h3>小红书模板专区</h3>
                  <p>把当前内容填入 HyCanvas 模板，创建后可继续精细调整并服务端高清出图。</p>
                </div>
                <a-button
                  v-if="hycanvasDesign"
                  @click="openCoverEditor"
                >
                  <ExternalLink :size="15" />继续编辑
                </a-button>
              </div>
              <a-spin :spinning="hycanvasTemplatesLoading">
                <div v-if="hycanvasTemplates.length" class="hycanvas-template-grid">
                  <button
                    v-for="item in hycanvasTemplates"
                    :key="item.id"
                    type="button"
                    :class="{ selected: selectedHyCanvasTemplateId === item.id }"
                    @click="selectedHyCanvasTemplateId = item.id"
                  >
                    <LayoutTemplate :size="20" />
                    <strong>{{ item.title }}</strong>
                    <small>{{ item.format?.width }} × {{ item.format?.height }}</small>
                  </button>
                </div>
                <a-empty v-else description="HyCanvas 尚未配置或暂无小红书模板" />
              </a-spin>
              <div v-if="selectedHyCanvasTemplate" class="hycanvas-fields">
                <label v-for="field in selectedHyCanvasTemplate.fillable_fields.filter((item) => item.kind === 'text' && item.semanticRole !== 'label')" :key="field.nodeId">
                  <span>{{ field.label }}</span>
                  <a-textarea v-model:value="hycanvasFields[field.label]" :rows="2" :maxlength="field.constraints?.maxChars" :show-count="Boolean(field.constraints?.maxChars)" :placeholder="field.hint" />
                </label>
              </div>
              <label
                v-if="selectedHyCanvasTemplate?.fillable_fields?.some((item) => item.kind === 'image')"
                class="hycanvas-image-field"
              >
                <span>替换模板主图（可选）</span>
                <input type="file" accept="image/png,image/jpeg,image/webp" @change="selectHyCanvasImage" />
                <small>{{ hycanvasImageFile?.name || '不选择时保留模板原图区域' }}</small>
              </label>
              <div class="hycanvas-actions">
                <span v-if="hycanvasDesign">设计稿 {{ hycanvasDesign.design_id }} 已绑定为版本 {{ hycanvasDesign.artifact_version || store.artifact.current_version }} 的封面</span>
                <a-button
                  type="primary"
                  :loading="hycanvasCreating"
                  :disabled="!selectedHyCanvasTemplate"
                  @click="createHyCanvasDesign"
                >
                  <WandSparkles :size="15" />创建可编辑视觉稿
                </a-button>
              </div>
            </section>
            <label class="field-block"><span>标题</span><a-input v-model:value="editor.title" /></label>
            <label class="field-block"><span>正文</span><a-textarea v-model:value="editor.body" :rows="18" /></label>
            <label class="field-block">
              <span>话题</span>
              <a-select v-model:value="editor.topics" mode="tags" :token-separators="[',', '，']" />
            </label>
            <div class="editor-actions">
              <a-button :loading="store.loading.saving" @click="saveArtifact"><Save :size="15" />保存编辑版本</a-button>
              <a-button type="primary" :loading="store.loading.reviewing" @click="reviewArtifact"><ShieldCheck :size="15" />重新审核</a-button>
            </div>
          </div>

          <aside class="review-sidebar">
            <section>
              <h3>质量检查</h3>
              <div v-if="!reviewChecks.length" class="empty-checks"><CheckCircle2 :size="20" />暂无阻断问题</div>
              <div v-for="check in reviewChecks" :key="`${check.code}-${check.location}`" class="review-check" :class="check.level">
                <strong>{{ check.message }}</strong><span>{{ check.suggestion }}</span>
              </div>
            </section>
            <section>
              <h3>来源追溯</h3>
              <div class="evidence-list">
                <div v-for="item in store.artifact.evidence_snapshot?.items || []" :key="item.id">
                  <strong>{{ item.key }}</strong>
                  <span>{{ item.source_type }} · {{ item.verified_status }}</span>
                </div>
              </div>
            </section>
            <a-button block @click="openVersions"><FileClock :size="15" />查看版本记录</a-button>
            <a-button type="primary" block :disabled="!canFinalize" @click="finalizeArtifact">保存为正式内容资产</a-button>
          </aside>
        </div>
        <a-empty v-else description="内容资产尚未生成完成" />
      </section>
    </main>

    <div v-else class="page-loading"><LoaderCircle class="spin" :size="28" />正在加载内容工作台</div>

    <a-modal
      v-model:open="resultDetailOpen"
      class="result-detail-modal"
      title="生成结果详情"
      :width="920"
      centered
      destroy-on-close
    >
      <div v-if="store.artifact" class="result-detail-layout">
        <section class="result-detail-cover" aria-label="内容封面">
          <div class="result-detail-section-heading">
            <div class="result-detail-cover-tabs" role="tablist" aria-label="封面操作">
              <button
                type="button"
                :class="{ active: resultPreviewTab === 'cover' }"
                role="tab"
                :aria-selected="resultPreviewTab === 'cover'"
                @click="resultPreviewTab = 'cover'"
              >
                <Image :size="17" />封面
              </button>
              <button
                type="button"
                role="tab"
                aria-selected="false"
                :disabled="!store.artifact.cover_asset_id"
                @click="openCoverEditor"
              >
                <PencilLine :size="16" />编辑
              </button>
              <button
                v-if="hasViralReference"
                type="button"
                :class="{ active: resultPreviewTab === 'viral-reference' }"
                role="tab"
                :aria-selected="resultPreviewTab === 'viral-reference'"
                @click="openViralReference"
              >
                <BookOpenCheck :size="16" />爆款原文
              </button>
            </div>
          </div>
          <div
            class="result-detail-cover-frame"
            :class="{ 'is-composition': currentResultGalleryItem?.id === 'composition' }"
          >
            <div v-if="resultPreviewTab === 'viral-reference'" class="result-detail-viral-reference">
              <div v-if="viralReferenceLoading" class="result-detail-cover-state">
                <LoaderCircle class="spin" :size="24" />
                <span>正在加载本次选中的爆款原文</span>
              </div>
              <template v-else-if="viralReference">
                <div class="result-detail-viral-meta">
                  <div>
                    <span>来源</span>
                    <strong>{{ viralReference.source_name }}</strong>
                    <small v-if="viralReference.knowledge_base_name">{{ viralReference.knowledge_base_name }}</small>
                  </div>
                  <a-button
                    type="text"
                    size="small"
                    @click="copyResultText(viralReference.content || '', '爆款原文')"
                  >
                    <Copy :size="14" />复制
                  </a-button>
                </div>
                <div class="result-detail-viral-body">{{ viralReference.content }}</div>
              </template>
            </div>
            <div v-else class="result-detail-carousel">
              <div v-if="currentResultGalleryItem?.loading" class="result-detail-cover-state">
                <LoaderCircle class="spin" :size="24" />
                <span>正在加载图片</span>
              </div>
              <img
                v-else-if="currentResultGalleryItem?.url"
                :class="{ 'is-composition': currentResultGalleryItem.id === 'composition' }"
                :src="currentResultGalleryItem.url"
                :alt="currentResultGalleryItem.label"
              />
              <div v-else class="result-detail-cover-state empty">
                <Image :size="30" />
                <strong>{{ currentResultGalleryItem ? '图片暂时无法预览' : '暂无封面' }}</strong>
                <span>{{ currentResultGalleryItem ? '请稍后刷新重试。' : '当前内容没有绑定封面，仍可继续发布。' }}</span>
              </div>
              <template v-if="resultGalleryItems.length > 1">
                <button
                  type="button"
                  class="result-detail-carousel-arrow previous"
                  :disabled="resultGalleryIndex === 0"
                  aria-label="查看上一张图片"
                  title="上一张"
                  @click="showPreviousResultImage"
                >
                  <ChevronLeft :size="22" />
                </button>
                <button
                  type="button"
                  class="result-detail-carousel-arrow next"
                  :disabled="resultGalleryIndex === resultGalleryItems.length - 1"
                  aria-label="查看下一张图片"
                  title="下一张"
                  @click="showNextResultImage"
                >
                  <ChevronRight :size="22" />
                </button>
                <span class="result-detail-carousel-count">
                  {{ resultGalleryIndex + 1 }} / {{ resultGalleryItems.length }}
                </span>
              </template>
            </div>
          </div>
        </section>

        <div class="result-detail-content">
          <section class="result-detail-section">
            <div class="result-detail-section-heading">
              <div><FileText :size="18" /><strong>发布标题</strong></div>
              <a-button
                type="text"
                size="small"
                :disabled="!store.artifact.title"
                @click="copyResultText(store.artifact.title || '', '标题')"
              >
                <Copy :size="15" />复制标题
              </a-button>
            </div>
            <h2 class="result-detail-title">{{ store.artifact.title || '暂无标题' }}</h2>
          </section>

          <section class="result-detail-section result-detail-body-section">
            <div class="result-detail-section-heading">
              <div><FileText :size="18" /><strong>正文文案</strong></div>
              <a-button
                type="text"
                size="small"
                :disabled="!store.artifact.body"
                @click="copyResultText(store.artifact.body || '', '正文')"
              >
                <Copy :size="15" />复制正文
              </a-button>
            </div>
            <div v-if="store.artifact.body" class="result-detail-body">
              <MarkdownPreview :content="store.artifact.body" />
            </div>
            <p v-else class="result-detail-empty">暂无正文内容</p>
          </section>

          <section class="result-detail-section result-detail-topics-section">
            <div class="result-detail-section-heading">
              <div><Tags :size="18" /><strong>发布标签</strong></div>
              <a-button
                type="text"
                size="small"
                :disabled="!store.artifact.topics?.length"
                @click="copyResultText((store.artifact.topics || []).map((topic) => `#${topic}`).join(' '), '标签')"
              >
                <Copy :size="15" />复制标签
              </a-button>
            </div>
            <div v-if="store.artifact.topics?.length" class="result-detail-topics">
              <span v-for="topic in store.artifact.topics" :key="topic">#{{ topic }}</span>
            </div>
            <p v-else class="result-detail-empty">暂无发布标签</p>
          </section>
        </div>
      </div>
      <a-empty v-else description="内容资产尚未生成完成" />

      <template #footer>
        <div class="result-detail-footer">
          <a-button @click="resultDetailOpen = false">关闭</a-button>
          <a-button
            type="primary"
            :disabled="!store.artifact"
            @click="resultDetailOpen = false; publishModalOpen = true"
          >
            发布
          </a-button>
        </div>
      </template>
    </a-modal>

    <a-drawer v-model:open="versionDrawerOpen" title="内容版本记录" width="520">
      <a-timeline>
        <a-timeline-item v-for="item in store.versions" :key="item.id">
          <strong>v{{ item.version }} · {{ item.source_type }}</strong>
          <p>{{ item.title }}</p>
          <small>
            {{ item.created_at }} ·
            {{ item.model_spec || (item.source_type === 'generated' ? '系统默认模型' : '人工编辑') }}
          </small>
        </a-timeline-item>
      </a-timeline>
    </a-drawer>
    <a-modal
      v-model:open="galleryModalOpen"
      :title="`选择图片 · ${activeMaterialGalleryPath}`"
      width="920px"
      :footer="null"
    >
      <div class="gallery-modal-content">
        <button
          v-if="activeGalleryId"
          type="button"
          class="gallery-modal-back"
          @click="openGallery(activeMaterialGalleryParent?.id || '')"
        >
          <ArrowLeft :size="15" />返回 {{ activeMaterialGalleryParent?.name || '全部图库' }}
        </button>
        <section v-if="activeMaterialGalleryChildren.length" class="gallery-modal-folders">
          <div class="gallery-modal-section-title">
            <strong>{{ activeGalleryId ? '二级图库' : '选择图库' }}</strong>
            <small>选择所属图库后查看其中的图片</small>
          </div>
          <div class="gallery-folder-grid">
            <button
              v-for="gallery in activeMaterialGalleryChildren"
              :key="gallery.id"
              type="button"
              class="gallery-folder-card compact"
              :class="{ selected: selectedImageItemId && selectedImageGalleryId === gallery.id }"
              @click="openGallery(gallery.id)"
            >
              <span class="gallery-folder-icon"><Folder :size="26" /></span>
              <span class="gallery-folder-copy">
                <strong>{{ gallery.name }}</strong>
                <small>{{ gallery.count }} 张图片素材</small>
              </span>
              <span v-if="selectedImageItemId && selectedImageGalleryId === gallery.id" class="gallery-selected-badge">已选择</span>
            </button>
          </div>
        </section>
        <div class="gallery-modal-heading">
          <p>{{ activeMaterialGalleryChildren.length ? '当前一级图库中的图片' : compositionSlotIndex === null ? '选择图片后统一确认，第一张为封面原图。' : '可多选图片，从当前位置开始依次填入空位。' }}</p>
          <span>已选 {{ pendingImageItems.length }} 张 · 最多 {{ pendingImageLimit }} 张 · 共 {{ galleryImages.length }} 张</span>
        </div>
        <div v-if="galleryImagesLoading" class="material-loading-row">
          <LoaderCircle class="spin" :size="20" />正在加载当前图库
        </div>
        <div v-else-if="galleryImages.length" class="gallery-modal-grid">
          <button
            v-for="item in galleryImages"
            :key="item.id"
            type="button"
            class="image-choice"
            :class="{ selected: pendingImageItems.some(selected => selected.id === item.id) }"
            :aria-pressed="pendingImageItems.some(selected => selected.id === item.id)"
            @click="togglePendingImage(item)"
          >
            <span class="choice-preview">
              <img v-if="materialImageUrls[item.id]" :src="materialImageUrls[item.id]" :alt="item.name" />
              <Image v-else :size="22" />
              <CheckCircle2 v-if="pendingImageItems.some(selected => selected.id === item.id)" class="choice-check" :size="20" />
            </span>
            <strong :title="item.name">{{ item.name }}</strong>
          </button>
        </div>
        <a-empty v-else description="当前文件夹暂无可用图片" />
        <div class="gallery-modal-actions">
          <a-button v-if="pendingImageItems.length" type="link" danger @click="pendingImageItems = []">
            清除选择
          </a-button>
          <span />
          <a-button @click="galleryModalOpen = false">取消</a-button>
          <a-button type="primary" :loading="galleryImagesLoading" @click="confirmGalleryImages">
            确认选择<span v-if="pendingImageItems.length">（{{ pendingImageItems.length }}）</span>
          </a-button>
        </div>
      </div>
    </a-modal>
    <XiaohongshuAccountPublishModal
      v-model:open="publishModalOpen"
      :artifact="store.artifact"
    />
    <a-modal
      v-model:open="imagePreviewOpen"
      class="content-image-preview-modal"
      :aria-label="imagePreviewTitle"
      :width="560"
      :footer="null"
      :closable="false"
      centered
      destroy-on-close
    >
      <div class="content-image-preview-stage">
        <img :src="imagePreviewSrc" :alt="imagePreviewAlt" />
        <button
          type="button"
          class="content-image-preview-close"
          aria-label="关闭图片预览"
          title="关闭"
          @click="imagePreviewOpen = false"
        >
          <X :size="20" />
        </button>
      </div>
    </a-modal>
    <ContentOcrDrawer v-if="store.task" v-model:open="ocrModalOpen" :task-id="store.task.id" />
  </div>
</template>

<style scoped lang="less">
.content-studio-page {
  min-width: 0;
  min-height: 100vh;
  padding: 24px var(--page-padding) 48px;
  background: var(--gray-25);
  color: var(--color-text);
  overflow-x: hidden;
}

.studio-header {
  max-width: 1180px;
  margin: 0 auto 18px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;

  h1 { margin: 3px 0 4px; font-size: 24px; }
  p { margin: 0; color: var(--color-text-secondary); }
}

.header-kicker { color: var(--main-700); font-size: 12px; font-weight: 600; }
.panel-heading :deep(.ant-btn), .stage-actions :deep(.ant-btn), .editor-actions :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; }

.studio-main { max-width: 1180px; margin: 18px auto 0; }
.studio-production {
  padding-bottom: 0;
  .studio-header, .studio-main { max-width: none; }
}
.stage-panel { background: var(--gray-0); border: 1px solid var(--gray-150); border-radius: 8px; padding: 24px; }
.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 22px; }
.panel-heading span { color: var(--main-700); font-size: 12px; font-weight: 600; }
.panel-heading h2 { margin: 2px 0 0; font-size: 20px; }
.panel-heading p { max-width: 460px; margin: 0; color: var(--color-text-secondary); }

.setup-grid, .brief-layout, .review-layout { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(280px, 0.8fr); gap: 20px; }
.run-layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; }
.active-run-layout { width: 100%; max-width: 1100px; min-height: calc(100vh - 210px); margin: 0 auto; display: flex; flex-direction: column; gap: 0; }
.setup-grid { grid-template-columns: 1fr 1fr; margin-bottom: 20px; }
.creation-type-field {
  margin-top: 24px;
  :deep(.ant-radio-group) { display: flex; flex-wrap: wrap; gap: 8px; }
}

.template-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.template-card { min-height: 116px; padding: 16px; display: flex; flex-direction: column; gap: 7px; text-align: left; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); color: var(--color-text); cursor: pointer; }
.template-card:hover { border-color: var(--main-300); background: var(--main-10); }
.template-card.selected { border-color: var(--main-color); background: var(--main-30); }
.template-card strong { font-size: 15px; }
.template-card span, .template-card small { color: var(--color-text-secondary); }

.form-card, .facts-preview, .human-review-card, .running-card, .content-editor-card, .review-sidebar { border: 1px solid var(--gray-150); border-radius: 8px; padding: 20px; background: var(--gray-0); }
.form-card, .content-editor-card { display: flex; flex-direction: column; gap: 18px; }
.dynamic-form { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.dynamic-form .field-block:has(textarea), .dynamic-form .field-block:has(.ant-select-multiple) { grid-column: 1 / -1; }
.field-block { display: flex; flex-direction: column; gap: 7px; color: var(--color-text); }
.field-block > span { font-size: 13px; font-weight: 600; }
.field-block em { margin-left: 3px; color: var(--color-error-700); font-style: normal; }
.field-block small { color: var(--color-text-tertiary); }
.creation-mode-options { display: flex; gap: 12px; }
.creation-mode-card { position: relative; display: flex; align-items: center; justify-content: center; width: 112px; min-height: 64px; border: 1px solid var(--gray-200); border-radius: 8px; background: var(--gray-0); cursor: pointer; }
.creation-mode-card input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.creation-mode-card:hover { border-color: var(--color-info-500); }
.creation-mode-card.selected { border-color: var(--color-info-500); background: var(--color-info-50); color: var(--color-info-700); font-weight: 600; }
.creation-mode-card:has(input:focus-visible) { outline: 2px solid var(--color-info-500); outline-offset: 2px; }
.mode-row { display: flex; align-items: center; gap: 8px; color: var(--color-text-secondary); }
.mode-row small { margin-left: auto; }
.mode-row .save-error { color: var(--color-error-600); }
.mode-badge, .status-badge { display: inline-flex; align-items: center; padding: 3px 9px; border-radius: 999px; background: var(--main-50); color: var(--main-700); font-size: 12px; }
.facts-preview { background: var(--gray-10); }
.facts-preview h3 { margin: 10px 0 6px; }
.facts-preview p, .facts-preview li { color: var(--color-text-secondary); }
.facts-preview ul { padding-left: 18px; }
.visual-material-card { margin-top: 20px; padding: 20px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.visual-material-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--gray-150); }
.visual-material-heading h3 { margin: 3px 0 5px; font-size: 17px; }
.visual-material-heading p { margin: 0; color: var(--color-text-secondary); font-size: 13px; }
.section-kicker { color: var(--main-700); font-size: 12px; font-weight: 600; }
.visual-material-heading :deep(.ant-btn), .template-manage-link { display: inline-flex; align-items: center; gap: 6px; }
.material-selector-block { padding-top: 18px; }
.template-selector-block { margin-top: 18px; border-top: 1px solid var(--gray-150); }
.material-selector-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.material-selector-title > div { display: flex; align-items: center; gap: 7px; }
.material-selector-title em { padding: 2px 7px; border-radius: 999px; color: var(--main-700); background: var(--main-30); font-size: 11px; font-style: normal; }
.material-selector-title small { color: var(--color-text-tertiary); text-align: right; }
.template-sync-status { display: inline-flex; align-items: center; gap: 4px; color: var(--color-success-700); font-size: 11px; white-space: nowrap; }
.gallery-folder-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 12px; }
.gallery-folder-card { position: relative; min-height: 92px; padding: 15px; display: flex; align-items: center; gap: 12px; border: 1px solid var(--gray-150); border-radius: 9px; color: var(--color-text); background: var(--gray-0); text-align: left; cursor: pointer; }
.gallery-folder-card:hover { border-color: var(--main-300); background: var(--main-10); transform: translateY(-1px); }
.gallery-folder-card.selected { border-color: var(--main-color); background: var(--main-30); box-shadow: 0 0 0 2px var(--main-10); }
.gallery-folder-icon { flex: 0 0 auto; display: grid; place-items: center; width: 52px; height: 46px; border-radius: 8px; color: var(--main-700); background: var(--main-30); }
.gallery-folder-copy { min-width: 0; display: grid; gap: 5px; }
.gallery-folder-copy strong, .gallery-folder-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gallery-folder-copy strong { font-size: 14px; }
.gallery-folder-copy small { color: var(--color-text-tertiary); font-size: 12px; }
.gallery-selected-badge { position: absolute; top: 7px; right: 8px; padding: 2px 7px; border-radius: 999px; color: var(--main-700); background: var(--main-50); font-size: 10px; }
.selected-gallery-image { margin-top: 14px; padding: 11px 13px; display: flex; align-items: center; justify-content: flex-start; gap: 10px; border: 1px solid var(--main-100); border-radius: 8px; background: var(--main-10); }
.selected-gallery-image small { color: var(--color-text-tertiary); font-size: 11px; }
.selected-gallery-image strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.selected-gallery-preview-grid { flex: 0 0 auto; display: grid; grid-template-columns: repeat(2, 128px); gap: 12px; }
.selected-gallery-preview-card { min-width: 0; display: grid; gap: 5px; }
.selected-gallery-preview-card > small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.selected-gallery-preview-media { display: grid; place-items: center; width: 100%; aspect-ratio: 3 / 4; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 6px; color: var(--color-text-tertiary); background: var(--gray-25); }
.selected-gallery-preview-media img { display: block; width: 100%; height: 100%; object-fit: cover; }
.selected-gallery-preview-trigger { position: relative; width: 100%; height: 100%; padding: 0; overflow: hidden; border: 0; color: inherit; background: transparent; cursor: zoom-in; }
.selected-gallery-preview-trigger:focus-visible { outline: 2px solid var(--main-color); outline-offset: -2px; }
.selected-gallery-preview-zoom { position: absolute; inset: 0; display: grid; place-items: center; color: var(--gray-0); background: var(--shadow-5); opacity: 0; transition: opacity 0.15s ease; }
.selected-gallery-preview-trigger:hover .selected-gallery-preview-zoom,
.selected-gallery-preview-trigger:focus-visible .selected-gallery-preview-zoom { opacity: 1; }
.content-image-preview-stage { position: relative; width: min(100%, calc((100vh - 80px) * 0.75)); aspect-ratio: 3 / 4; margin: 0 auto; overflow: hidden; border-radius: 6px; background: var(--gray-25); }
.content-image-preview-stage img { display: block; width: 100%; height: 100%; object-fit: cover; }
.content-image-preview-close { position: absolute; top: 12px; right: 12px; width: 36px; height: 36px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 50%; color: var(--gray-0); background: var(--shadow-5); cursor: pointer; }
.content-image-preview-close:hover { background: var(--shadow-4); }
.content-image-preview-close:focus-visible { outline: 2px solid var(--gray-0); outline-offset: 2px; }
:global(.content-image-preview-modal .ant-modal-content) { padding: 0; overflow: hidden; background: transparent; box-shadow: none; }
.poster-choice-grid { display: grid; grid-auto-flow: column; grid-auto-columns: 142px; gap: 12px; padding: 2px 2px 8px; overflow-x: auto; }
.gallery-modal-content { display: grid; gap: 16px; padding-top: 4px; }
.gallery-modal-back { width: fit-content; padding: 0; display: inline-flex; align-items: center; gap: 5px; border: 0; color: var(--main-700); background: transparent; cursor: pointer; }
.gallery-modal-folders { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-25); }
.gallery-modal-section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.gallery-modal-section-title small { color: var(--color-text-tertiary); }
.gallery-folder-card.compact { min-height: 76px; padding: 11px; background: var(--gray-0); }
.gallery-folder-card.compact .gallery-folder-icon { width: 46px; height: 40px; }
.gallery-modal-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.gallery-modal-heading p { margin: 0; color: var(--color-text-secondary); }
.gallery-modal-heading span { flex: 0 0 auto; color: var(--color-text-tertiary); font-size: 12px; }
.gallery-modal-grid { max-height: min(58vh, 620px); padding: 2px; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; overflow-y: auto; }
.gallery-modal-actions { padding-top: 14px; display: grid; grid-template-columns: auto 1fr auto auto; gap: 8px; border-top: 1px solid var(--gray-150); }
.image-choice, .poster-choice { position: relative; min-width: 0; padding: 7px; border: 1px solid var(--gray-150); border-radius: 8px; color: var(--color-text); background: var(--gray-0); text-align: left; cursor: pointer; }
.image-choice:hover, .poster-choice:hover { border-color: var(--main-300); transform: translateY(-1px); }
.image-choice.selected, .poster-choice.selected { border-color: var(--main-color); box-shadow: 0 0 0 2px var(--main-30); }
.poster-choice.unavailable { opacity: 0.64; cursor: not-allowed; }
.poster-choice.unavailable:hover { border-color: var(--gray-150); transform: none; }
.choice-preview, .poster-preview, .poster-auto-preview { position: relative; display: grid; place-items: center; width: 100%; overflow: hidden; border-radius: 6px; color: var(--color-text-tertiary); background: var(--gray-25); }
.choice-preview { aspect-ratio: 1 / 1; }
.poster-preview, .poster-auto-preview { aspect-ratio: 3 / 4; }
.choice-preview img, .poster-preview img { width: 100%; height: 100%; object-fit: cover; }
.image-choice > strong, .poster-choice > strong, .poster-choice > small { display: block; margin-top: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.image-choice > strong, .poster-choice > strong { font-size: 13px; }
.poster-choice > small { margin-top: 2px; color: var(--color-text-tertiary); font-size: 11px; }
.choice-check { position: absolute; z-index: 1; top: 9px; right: 9px; padding: 2px; border-radius: 50%; color: var(--main-color); background: var(--gray-0); }
.poster-auto-preview { color: var(--main-700); background: linear-gradient(145deg, var(--main-10), var(--main-50)); }
.material-loading-row { min-height: 140px; display: flex; align-items: center; justify-content: center; gap: 8px; color: var(--color-text-secondary); }
.template-manage-link { margin-top: 8px; padding-left: 0; }
.stage-actions { margin-top: 22px; display: flex; justify-content: flex-end; gap: 10px; }
.stage-actions.split { justify-content: space-between; }


.generation-start { max-width: 560px; margin: 50px auto; display: flex; flex-direction: column; align-items: center; gap: 12px; text-align: center; }
.generation-start h3, .generation-start p { margin: 0; }
.generation-start p { color: var(--color-text-secondary); }
.generation-start :deep(.ant-input) { max-width: 500px; }
.completion-stage { padding: 0; border: 0; background: transparent; }
.completion-layout { grid-template-columns: minmax(0, 1fr) 394px; gap: 24px; align-items: start; }
.completion-left { min-width: 0; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.completion-conversation { height: calc(100vh - 180px); min-height: 560px; display: flex; flex-direction: column; overflow: hidden; }
.completion-narrative { margin-top: 0; }
.workflow-complete-line { display: flex; align-items: center; gap: 8px; margin-top: 20px; color: var(--color-success-700); }
.workflow-complete-line strong { color: var(--color-text); font-size: 14px; }
.ai-edit-history { flex: 0 0 auto; max-height: 150px; display: flex; flex-direction: column; gap: 8px; margin: 12px 27px 0; overflow-y: auto; overscroll-behavior: contain; }
.ai-edit-message { max-width: 78%; padding: 8px 10px; border-radius: 8px; color: var(--color-text); background: var(--gray-0); font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; }
.ai-edit-message.user { align-self: flex-end; color: var(--main-700); background: var(--main-30); }
.ai-edit-message.assistant { align-self: flex-start; border: 1px solid var(--gray-150); }
.ai-edit-message.assistant.running { display: inline-flex; align-items: center; gap: 7px; color: var(--color-info-700); background: var(--color-info-50); border-color: var(--color-info-100); }
.ai-edit-message.assistant.failed { color: var(--color-error-700); background: var(--color-error-50); border-color: var(--color-error-100); }
.ai-edit-running { display: inline-flex; align-items: center; gap: 6px; color: var(--color-info-700); font-size: 12px; white-space: nowrap; }
.completion-results { min-width: 0; display: flex; flex-direction: column; gap: 12px; padding: 0 12px 12px; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.completion-results-heading { min-height: 56px; display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 -12px 4px; padding: 0 14px; border-bottom: 1px solid var(--gray-150); }
.completion-results-heading > div { display: flex; align-items: center; gap: 8px; }
.completion-results-icon { width: 24px; height: 24px; display: inline-flex; align-items: center; justify-content: center; border-radius: 4px; color: var(--color-info-500); background: var(--color-info-50); }
.completion-results-heading strong { font-size: 14px; }
.completion-results-heading > span { position: relative; padding-left: 11px; color: var(--color-text-tertiary); font-size: 11px; }
.completion-results-heading > span::before { content: ''; position: absolute; top: 50%; left: 0; width: 6px; height: 6px; border-radius: 50%; background: var(--color-success-700); transform: translateY(-50%); }
.completion-result-item { min-width: 0; padding: 14px; border: 1px solid var(--color-info-100); border-radius: 6px; background: var(--color-info-10); }
.completion-result-main { min-width: 0; }
.completion-result-copy { min-width: 0; }
.completion-result-meta { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.completion-result-meta span { padding: 3px 8px; border-radius: 3px; color: var(--color-info-700); background: var(--color-info-50); font-size: 11px; font-weight: 600; }
.completion-result-meta-details { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; }
.completion-result-meta small { color: var(--color-text-tertiary); font-size: 10px; white-space: nowrap; }
.completion-result-content { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) 104px; gap: 10px; align-items: start; }
.completion-result-media { width: 104px; aspect-ratio: 1; overflow: hidden; border-radius: 0; background: var(--gray-50); }
.completion-result-cover, .completion-result-cover-placeholder { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; }
.completion-result-cover { object-fit: cover; }
.completion-result-cover-placeholder { flex-direction: column; gap: 5px; color: var(--color-text-tertiary); border: 1px dashed var(--gray-200); font-size: 10px; }
.completion-result-item h3 { margin: 10px 0 8px; color: var(--color-text); font-size: 13px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.completion-result-body { max-height: 66px; overflow: hidden; color: var(--color-text-secondary); font-size: 11px; line-height: 1.65; }
.completion-result-body.expanded { max-height: none; }
.completion-result-body :deep(.yk-markdown-preview) { color: var(--color-text-secondary); font-size: 11px; line-height: 1.65; }
.completion-result-body :deep(p) { margin: 0 0 4px; }
.completion-result-expand { display: inline-flex; margin-top: 5px; padding: 0; border: 0; color: var(--main-700); background: transparent; font-size: 11px; cursor: pointer; }
.completion-result-expand:hover { color: var(--main-800); }
.completion-result-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding-top: 12px; }
.completion-result-actions :deep(.ant-btn) { height: 36px; display: inline-flex; align-items: center; justify-content: center; border-radius: 7px; font-size: 13px; }
.completion-result-actions :deep(.ant-btn-primary) { border-color: var(--color-info-500); background: var(--color-info-500); box-shadow: none; }
.completion-result-actions :deep(.ant-btn-primary:hover) { border-color: var(--color-info-700); background: var(--color-info-700); }
.completion-version-button { grid-column: 1 / -1; }
.result-detail-layout { height: calc(100dvh - 190px); min-height: 0; max-height: 820px; display: grid; grid-template-rows: minmax(0, 1fr); grid-template-columns: minmax(320px, 0.9fr) minmax(0, 1.1fr); overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; }
.result-detail-cover { min-width: 0; min-height: 0; display: flex; flex-direction: column; padding: 20px; background: var(--gray-25); border-right: 1px solid var(--gray-150); }
.result-detail-section-heading { min-height: 32px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.result-detail-section-heading > div { min-width: 0; display: flex; align-items: center; gap: 7px; }
.result-detail-section-heading svg { flex: 0 0 auto; color: var(--main-700); }
.result-detail-section-heading strong { font-size: 14px; }
.result-detail-section-heading :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 5px; color: var(--main-700); }
.result-detail-cover-tabs { padding: 3px; border-radius: 8px; background: var(--gray-100); }
.result-detail-cover-tabs button { min-width: 74px; height: 30px; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); display: inline-flex; align-items: center; justify-content: center; gap: 6px; cursor: pointer; transition: 0.18s ease; }
.result-detail-cover-tabs button.active { background: var(--color-bg-container); color: var(--main-700); box-shadow: 0 1px 4px var(--shadow-1); font-weight: 600; }
.result-detail-cover-tabs button:not(.active):hover:not(:disabled) { color: var(--main-700); background: var(--main-50); }
.result-detail-cover-tabs button:disabled { cursor: not-allowed; opacity: 0.42; }
.result-detail-cover-frame { position: relative; min-height: 0; flex: 1; display: flex; align-items: center; justify-content: center; margin-top: 12px; overflow: hidden; }
.result-detail-cover-frame img { display: block; width: min(100%, 400px); max-height: 100%; aspect-ratio: 3 / 4; border-radius: 6px; object-fit: contain; background: var(--gray-100); }
.result-detail-cover-frame.is-composition { align-items: flex-start; overflow-y: auto; overscroll-behavior: contain; }
.result-detail-cover-frame.is-composition .result-detail-carousel { min-height: 100%; height: auto; align-items: flex-start; padding: 0 0 44px; box-sizing: border-box; }
.result-detail-cover-frame.is-composition img.is-composition { width: 100%; max-width: none; max-height: none; aspect-ratio: auto; height: auto; flex: 0 0 auto; }
.result-detail-carousel { position: relative; width: 100%; height: 100%; min-height: 0; display: flex; align-items: center; justify-content: center; }
.result-detail-carousel-arrow { position: absolute; top: 50%; z-index: 1; width: 38px; height: 38px; padding: 0; display: grid; place-items: center; border: 1px solid var(--gray-200); border-radius: 50%; color: var(--color-text); background: var(--gray-0); box-shadow: 0 2px 8px var(--shadow-2); cursor: pointer; transform: translateY(-50%); }
.result-detail-carousel-arrow.previous { left: 8px; }
.result-detail-carousel-arrow.next { right: 8px; }
.result-detail-carousel-arrow:hover:not(:disabled) { border-color: var(--main-color); color: var(--main-700); background: var(--main-50); }
.result-detail-carousel-arrow:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
.result-detail-carousel-arrow:disabled { cursor: not-allowed; opacity: 0.35; }
.result-detail-carousel-count { position: absolute; bottom: 10px; left: 50%; padding: 3px 9px; border: 1px solid var(--gray-200); border-radius: 999px; color: var(--color-text-secondary); background: var(--gray-0); font-size: 12px; line-height: 1.4; transform: translateX(-50%); }
.result-detail-cover-state { min-height: 240px; width: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--color-text-secondary); text-align: center; }
.result-detail-cover-state.empty span { max-width: 240px; color: var(--color-text-tertiary); font-size: 12px; line-height: 1.6; }
.result-detail-content { min-width: 0; min-height: 0; height: 100%; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; overflow: hidden; padding: 0 22px; background: var(--gray-0); }
.result-detail-section { padding: 14px 0; border-bottom: 1px solid var(--gray-150); }
.result-detail-section:last-child { border-bottom: 0; }
.result-detail-title { margin: 8px 0 0; padding: 9px 12px; border: 1px solid var(--gray-150); border-radius: 6px; font-size: 15px; line-height: 1.5; overflow-wrap: anywhere; }
.result-detail-body-section { min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.result-detail-body { min-height: 0; flex: 1; margin-top: 8px; padding: 2px 8px 2px 0; overflow-y: auto; overscroll-behavior: contain; color: var(--color-text); font-size: 13px; line-height: 1.68; overflow-wrap: anywhere; }
.result-detail-body :deep(p:first-child) { margin-top: 0; }
.result-detail-body :deep(p:last-child) { margin-bottom: 0; }
.result-detail-topics-section { margin: 6px 0 8px; padding: 7px 10px; border: 1px solid var(--gray-150); border-radius: 8px; }
.result-detail-topics-section .result-detail-section-heading { min-height: 26px; }
.result-detail-topics { display: flex; flex-wrap: wrap; gap: 2px 7px; margin-top: 3px; }
.result-detail-topics span { color: var(--main-700); font-size: 13px; line-height: 1.35; }
.result-detail-empty { margin: 12px 0 0; color: var(--color-text-tertiary); font-size: 13px; }
.result-detail-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.result-detail-footer :deep(.ant-btn) { min-width: 132px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.result-detail-viral-reference { min-width: 0; width: 100%; height: 100%; display: flex; flex-direction: column; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.result-detail-viral-meta { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--gray-150); }
.result-detail-viral-meta > div { min-width: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 2px 7px; }
.result-detail-viral-meta span, .result-detail-viral-meta small { color: var(--color-text-tertiary); font-size: 11px; }
.result-detail-viral-meta small { grid-column: 2; }
.result-detail-viral-meta strong { min-width: 0; overflow: hidden; color: var(--color-text); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.result-detail-viral-meta :deep(.ant-btn) { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 4px; color: var(--main-700); }
.result-detail-viral-body { min-height: 0; flex: 1; padding: 16px; overflow-y: auto; color: var(--color-text); font-size: 13px; line-height: 1.85; white-space: pre-wrap; overflow-wrap: anywhere; }
.workflow-stream { min-width: 0; min-height: 0; flex: 1 1 auto; padding: 16px 24px 28px; overflow-y: auto; overscroll-behavior: contain; }
.completion-conversation > .ai-edit-history { flex: 0 0 auto; }
.codex-workflow-status { min-width: 0; padding: 4px 0 10px; }
.codex-workflow-heading { min-height: 52px; display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 10px; }
.codex-workflow-icon { display: inline-flex; color: var(--color-text-tertiary); }
.codex-workflow-status.running .codex-workflow-icon, .codex-workflow-status.active .codex-workflow-icon { color: var(--color-info-700); }
.codex-workflow-status.failed .codex-workflow-icon { color: var(--color-error-700); }
.codex-workflow-copy { min-width: 0; }
.codex-workflow-copy strong { color: var(--color-text); font-size: 14px; line-height: 1.4; }
.workflow-narrative { min-width: 0; max-width: 1100px; margin: 8px auto 0; }
.workflow-narrative :deep(.yk-markdown-preview ul) { display: block; margin: 6px 0 12px; padding-left: 20px; }
.workflow-narrative :deep(.yk-markdown-preview ul > li) { min-height: 0; margin: 0; padding: 0; line-height: 1.65; }
.workflow-narrative :deep(.yk-markdown-preview ul > li + li) { margin-top: 2px; }
.workflow-narrative :deep(.yk-markdown-preview ul > li > p) { display: inline; margin: 0; padding: 0; line-height: inherit; }
.workflow-thinking-row { margin-top: 10px; }
.workflow-thinking-row .workflow-thinking-indicator { margin-left: 0; }
.workflow-thinking-indicator { display: inline-flex; align-items: center; gap: 5px; margin-left: 8px; color: var(--color-info-700); font-size: 12px; line-height: 1; vertical-align: 0.05em; white-space: nowrap; }
.workflow-thinking-indicator > svg { flex: 0 0 auto; }
.workflow-thinking-dots { display: inline-flex; align-items: center; gap: 2px; height: 12px; }
.workflow-thinking-dots i { width: 3px; height: 3px; display: block; border-radius: 50%; background: currentColor; animation: workflow-thinking-pulse 1.2s ease-in-out infinite; }
.workflow-thinking-dots i:nth-child(2) { animation-delay: 0.16s; }
.workflow-thinking-dots i:nth-child(3) { animation-delay: 0.32s; }
.workflow-awaiting-event { min-height: 44px; display: flex; align-items: center; gap: 8px; color: var(--color-text-secondary); font-size: 13px; }
@keyframes workflow-thinking-pulse { 0%, 60%, 100% { opacity: 0.3; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-2px); } }
@media (prefers-reduced-motion: reduce) {
  .workflow-thinking-dots i { animation: none; opacity: 0.65; }
}
.workflow-list-enter-active, .workflow-list-leave-active { transition: opacity 0.2s ease, transform 0.2s ease; }
.workflow-list-enter-from { opacity: 0; transform: translateY(6px); }
.workflow-list-leave-to { opacity: 0; transform: translateY(-6px); }
.workflow-chat-panel { position: sticky; bottom: 0; z-index: 2; flex: 0 0 auto; padding: 12px 18px; border-radius: 0 0 8px 8px; background: var(--gray-0); }
.workflow-chat-panel > p { margin: 6px 0 0; color: var(--color-text-tertiary); font-size: 11px; line-height: 1.5; text-align: center; }
.human-review-card, .running-card { align-self: start; }
.failure-card { color: var(--color-error-700); background: var(--color-error-50); }
.title-validation-failures { width: 100%; margin: 8px 0 4px; padding: 14px; text-align: left; color: var(--color-text); background: var(--gray-0); border: 1px solid var(--color-error-200); border-radius: 8px; }
.title-validation-failures h4 { margin: 0 0 10px; font-size: 14px; }
.title-validation-failures > ol { display: grid; gap: 12px; margin: 0; padding-left: 22px; }
.title-validation-failures > ol > li > strong { display: block; font-size: 14px; line-height: 1.6; }
.title-validation-failures ul { display: grid; gap: 5px; margin: 6px 0 0; padding-left: 18px; color: var(--color-error-700); }
.title-validation-failures ul span, .title-validation-failures ul small { display: block; font-size: 12px; line-height: 1.55; }
.title-validation-failures ul small { margin-top: 2px; color: var(--color-text-secondary); }
.external-wait-card { gap: 10px; background: var(--color-info-50); color: var(--color-info-700); }
.external-wait-card h3, .external-wait-card p { margin: 0; }
.external-wait-card p, .external-wait-card small { color: var(--color-text-secondary); }
.external-wait-card :deep(.ant-progress) { width: 100%; }
.human-heading { display: flex; gap: 10px; margin-bottom: 16px; }
.human-heading h3, .human-heading p { margin: 0; }
.human-heading p { margin-top: 3px; color: var(--color-text-secondary); }
.title-options, .fact-options { width: 100%; display: flex; flex-direction: column; gap: 9px; margin-bottom: 16px; }
.title-options :deep(.ant-radio-wrapper), .fact-options :deep(.ant-checkbox-wrapper) { width: 100%; margin-inline-start: 0; padding: 12px; border: 1px solid var(--gray-150); border-radius: 6px; align-items: flex-start; }
.angle-options :deep(.ant-radio-wrapper-checked) { border-color: var(--main-color); background: var(--main-30); }
.angle-options strong, .angle-options span { display: block; }
.angle-options span { margin-top: 4px; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.evidence-references { margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--gray-150); color: var(--color-text-secondary); }
.evidence-references-title { margin-bottom: 6px; font-size: 12px; font-weight: 600; line-height: 1.5; }
.evidence-references ol { display: flex; flex-direction: column; gap: 6px; margin: 0; padding: 0; list-style: none; counter-reset: evidence-reference; }
.evidence-references li { display: grid; grid-template-columns: 24px minmax(0, 1fr); margin: 0; font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; counter-increment: evidence-reference; }
.evidence-references li::before { content: counter(evidence-reference) '、'; color: var(--main-color); font-weight: 600; }
.approval-checks { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
.approval-checks > div { padding: 10px 12px; border-radius: 6px; background: var(--color-warning-50); color: var(--color-warning-900); }
.approval-checks strong, .approval-checks span { display: block; font-size: 12px; }
.approval-checks span { margin-top: 3px; opacity: 0.82; }
.correction-target { margin: 12px 0 0; color: var(--gray-600); font-size: 13px; }
.approval-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.title-options strong, .title-options span { display: block; }
.title-options span { margin-top: 3px; color: var(--color-text-tertiary); font-size: 12px; }
.cover-options :deep(.ant-radio-wrapper) { align-items: flex-start; max-width: 400px; }
.cover-option { color: var(--color-text); }
.cover-options :deep(.ant-radio + span) { min-width: 0; flex: 1; }
.cover-option { display: grid; gap: 4px; min-width: 0; }
.cover-candidate-preview { width: 100%; margin-bottom: 7px; overflow: hidden; border-radius: 8px; background: var(--gray-25); }
.cover-candidate-preview img { display: block; width: 100%; aspect-ratio: 3 / 4; object-fit: contain; }
.cover-candidate-placeholder { min-height: 220px; display: flex; align-items: center; justify-content: center; color: var(--color-text-tertiary); }
.cover-option > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.running-card { display: flex; flex-direction: column; align-items: center; text-align: center; }

.content-editor-card textarea { resize: vertical; }
.artifact-cover-preview { display: flex; flex-direction: column; gap: 12px; padding-bottom: 18px; border-bottom: 1px solid var(--gray-150); }
.artifact-cover-heading { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.artifact-cover-heading div { display: grid; gap: 3px; }
.artifact-cover-heading span, .artifact-cover-heading small { color: var(--color-text-tertiary); font-size: 12px; }
.artifact-cover-heading small { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.artifact-cover-preview img { width: min(100%, 320px); aspect-ratio: 3 / 4; align-self: center; border-radius: 8px; object-fit: cover; box-shadow: 0 8px 24px var(--shadow-3); }
.hycanvas-panel { display: flex; flex-direction: column; gap: 14px; padding: 16px; border: 1px solid var(--color-primary-100); border-radius: 8px; background: var(--color-primary-50); }
.hycanvas-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.hycanvas-heading h3, .hycanvas-heading p { margin: 0; }
.hycanvas-heading p { margin-top: 5px; color: var(--color-text-secondary); font-size: 13px; }
.hycanvas-template-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; max-height: 210px; overflow: auto; }
.hycanvas-template-grid button { display: grid; gap: 5px; min-width: 0; padding: 12px; border: 1px solid var(--gray-200); border-radius: 7px; color: var(--color-text-primary); text-align: left; background: var(--gray-0); cursor: pointer; }
.hycanvas-template-grid button.selected { border-color: var(--color-primary-700); box-shadow: 0 0 0 2px var(--color-primary-100); }
.hycanvas-template-grid strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hycanvas-template-grid small { color: var(--color-text-tertiary); }
.hycanvas-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.hycanvas-fields label { display: grid; gap: 5px; }
.hycanvas-fields label span { color: var(--color-text-secondary); font-size: 12px; font-weight: 600; }
.hycanvas-image-field { display: grid; gap: 6px; padding: 12px; border: 1px dashed var(--gray-300); border-radius: 7px; background: var(--gray-0); }
.hycanvas-image-field span { font-size: 13px; font-weight: 600; }
.hycanvas-image-field small { color: var(--color-text-tertiary); }
.hycanvas-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
.hycanvas-actions span { margin-right: auto; color: var(--color-success-700); font-size: 12px; }
.cover-preview-loading { min-height: 240px; display: flex; align-items: center; justify-content: center; gap: 8px; color: var(--color-text-secondary); background: var(--gray-25); border-radius: 8px; }
.editor-actions { display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.review-sidebar { display: flex; flex-direction: column; gap: 16px; align-self: start; }
.review-sidebar section { border-bottom: 1px solid var(--gray-150); padding-bottom: 14px; }
.review-sidebar h3 { margin: 0 0 10px; }
.empty-checks { display: flex; align-items: center; gap: 8px; color: var(--color-success-700); }
.review-check { margin-bottom: 8px; padding: 10px; border-radius: 6px; background: var(--gray-25); }
.review-check strong, .review-check span { display: block; }
.review-check span { margin-top: 3px; color: var(--color-text-secondary); font-size: 12px; }
.review-check.error { background: var(--color-error-50); }
.review-check.warning { background: var(--color-warning-50); }
.evidence-list { display: flex; flex-direction: column; gap: 7px; max-height: 240px; overflow: auto; }
.evidence-list div { display: flex; justify-content: space-between; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--gray-100); }
.evidence-list span { color: var(--color-text-tertiary); font-size: 12px; }
.status-badge.passed { background: var(--color-success-50); color: var(--color-success-700); }
.status-badge.warning { background: var(--color-warning-50); color: var(--color-warning-900); }
.status-badge.blocked { background: var(--color-error-50); color: var(--color-error-700); }
.page-loading { min-height: 400px; display: flex; align-items: center; justify-content: center; gap: 10px; color: var(--color-text-secondary); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 1100px) {
  .completion-layout { grid-template-columns: 1fr; }
}

@media (max-width: 900px) {
  .studio-header, .panel-heading { flex-direction: column; }
  .setup-grid, .brief-layout, .review-layout { grid-template-columns: 1fr; }
  .result-detail-layout { height: auto; min-height: 0; max-height: calc(100dvh - 210px); grid-template-columns: 1fr; grid-template-rows: auto auto; overflow-y: auto; }
  .result-detail-cover { height: 420px; min-height: 0; border-right: 0; border-bottom: 1px solid var(--gray-150); }
  .result-detail-content { height: auto; display: block; overflow: visible; }
  .result-detail-body-section { overflow: visible; }
  .result-detail-body { padding-right: 0; overflow: visible; }
  .template-grid { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 600px) {
  .content-studio-page { padding-top: 14px; }
  .stage-panel { padding: 16px; }
  .workflow-stream, .workflow-chat-panel { padding-left: 12px; padding-right: 12px; }
  .workflow-narrative { margin-left: 0; }
  .completion-stage { padding: 0; }
  .ai-edit-message { max-width: 92%; }
  .result-detail-cover { height: 340px; padding: 16px; }
  .result-detail-content { padding: 0 16px; }
  .result-detail-footer :deep(.ant-btn) { min-width: 0; flex: 1; }
  .template-grid, .dynamic-form { grid-template-columns: 1fr; }
  .hycanvas-template-grid, .hycanvas-fields { grid-template-columns: 1fr; }
  .dynamic-form .field-block { grid-column: auto; }
  .stage-actions, .stage-actions.split, .editor-actions { width: 100%; flex-direction: column; }
  .visual-material-heading, .material-selector-title { flex-direction: column; }
  .material-selector-title small { text-align: left; }
  .selected-gallery-image { flex-wrap: wrap; }
  .selected-gallery-preview-grid { flex: 0 0 100%; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .gallery-folder-grid { grid-template-columns: 1fr 1fr; }
  .gallery-modal-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .gallery-modal-actions { grid-template-columns: 1fr 1fr; }
  .gallery-modal-actions > span { display: none; }
  .image-choice-grid, .poster-choice-grid { grid-auto-columns: 124px; }
  .workflow-groups { padding: 12px; }
  .workflow-group summary { grid-template-columns: auto minmax(0, 1fr) auto; gap: 9px; padding: 11px; }
  .workflow-group-progress { display: none; }
  .workflow-group-copy small { white-space: normal; }
  .stage-actions :deep(.ant-btn), .editor-actions :deep(.ant-btn) { width: 100%; justify-content: center; }
}
</style>
