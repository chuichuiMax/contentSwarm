<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Eye,
  FolderUp,
  ImagePlus,
  Layers3,
  RefreshCw,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Trash2,
  UploadCloud,
  WandSparkles,
  X
} from 'lucide-vue-next'
import { useCoverGenerationStore } from '@/stores/coverGeneration'
import { materialLibraryApi } from '@/apis/material_library_api'
import MaterialImagePickerModal from '@/components/material/MaterialImagePickerModal.vue'
import PosterCanvasPreview from './PosterCanvasPreview.vue'

const props = defineProps({
  contentTaskId: { type: String, default: '' },
  image2Ready: { type: Boolean, default: false }
})

const store = useCoverGenerationStore()
const route = useRoute()
const router = useRouter()
const productAsset = ref(null)
const selectedTemplateId = ref('')
const selectedTemplateRecord = ref(null)
const previewDataUrl = ref('')
const dragging = ref('')
const importFiles = ref([])
const productPreviewUrl = ref('')
const showImport = ref(false)
const showManagement = ref(false)
const showAdvanced = ref(false)
const imagePickerOpen = ref(false)
let restoreJobVersion = 0
const page = ref(1)
const filters = reactive({ query: '', category: '', status: '' })
const categoryOptions = ref([])
const importForm = reactive({ category: '' })
const templateForm = reactive({ name: '', category: '', status: 'ready' })
const annotation = reactive({ x: 0.08, y: 0.25, width: 0.84, height: 0.65 })
const form = reactive({
  title: '',
  fit: 'cover',
  scale: 1,
  focalX: 0.5,
  focalY: 0.5,
  xOffset: 0,
  yOffset: 0,
  enhance: false,
  enhancementPrompt: '',
  negativePrompt: '',
  count: 1,
  copyOverrides: {}
})

const selectedTemplate = computed(() => selectedTemplateRecord.value)
const editableSlots = computed(() => (
  (selectedTemplate.value?.text_slots || []).filter((item) => item.editable)
))
const hasFilters = computed(() => Boolean(filters.query.trim() || filters.category.trim() || filters.status))
const templateReady = computed(() => selectedTemplate.value?.status === 'ready')
const canPreview = computed(() => Boolean(
  productAsset.value
  && templateReady.value
  && !store.loading.posterPreview
))
const canGenerate = computed(() => Boolean(
  !store.isRunning
  && !store.loading.upload
  && !store.loading.submit
  && productAsset.value
  && templateReady.value
  && (!form.enhance || (props.image2Ready && selectedTemplate.value?.template_type === 'alpha_overlay'))
))
const totalPages = computed(() => Math.max(1, Math.ceil(store.posterTemplateTotal / 24)))
const currentStep = computed(() => {
  if (!selectedTemplate.value || !templateReady.value) return 1
  if (!productAsset.value) return 2
  if (!previewDataUrl.value) return 3
  return 4
})
const generateHint = computed(() => {
  if (!selectedTemplate.value) return '请先选择一个可使用的封面模板'
  if (!templateReady.value) return '请先完成底图区域标注并启用模板'
  if (!productAsset.value) return '请从素材库选择一张图片作为底图'
  if (form.enhance && selectedTemplate.value?.template_type !== 'alpha_overlay') return '当前模板不支持 image2，请关闭智能美化或改用透明 PNG 模板'
  if (form.enhance && !props.image2Ready) return '请先在右上角完成 image2 全局配置'
  if (store.isRunning || store.loading.submit) return '任务正在处理中，请稍候'
  return previewDataUrl.value ? '预览已就绪，可以生成高清成品' : '建议先预览排版，确认后再生成'
})

function stepState(step) {
  if (step < currentStep.value) return 'done'
  if (step === currentStep.value) return 'active'
  return 'pending'
}

function transformPayload() {
  return {
    fit: form.fit,
    scale: Number(form.scale),
    focal_x: Number(form.focalX),
    focal_y: Number(form.focalY),
    x_offset: Number(form.xOffset),
    y_offset: Number(form.yOffset)
  }
}

function basePayload() {
  return {
    poster_template_id: selectedTemplateId.value,
    product_asset_id: productAsset.value.id,
    content_task_id: props.contentTaskId || null,
    title: form.title,
    copy_overrides: { ...form.copyOverrides },
    transform: transformPayload()
  }
}

async function loadTemplates() {
  try {
    await store.loadPosterTemplates({
      query: filters.query.trim() || null,
      category: filters.category.trim() || null,
      status: filters.status || null,
      page: page.value
    })
    const refreshed = store.posterTemplates.find((item) => item.id === selectedTemplateId.value)
    if (refreshed) selectedTemplateRecord.value = refreshed
  } catch (error) {
    message.error(error.message || '大字报素材库加载失败')
  }
}

async function clearFilters() {
  Object.assign(filters, { query: '', category: '', status: '' })
  page.value = 1
  await loadTemplates()
}

function chooseTemplate(item) {
  selectedTemplateId.value = item.id
  selectedTemplateRecord.value = item
  previewDataUrl.value = ''
  showManagement.value = false
  Object.assign(annotation, item.product_box || { x: 0.08, y: 0.25, width: 0.84, height: 0.65 })
  form.copyOverrides = {}
  Object.assign(templateForm, {
    name: item.name || '',
    category: item.category === 'uncategorized' ? '' : item.category,
    status: item.status || 'ready'
  })
}

function collectFiles(event) {
  importFiles.value = Array.from(event.target.files || [])
  event.target.value = ''
}

async function importTemplates(files = importFiles.value) {
  if (!files.length) return
  if (!importForm.category) return message.warning('请选择模板分类')
  try {
    const response = await store.importPosterTemplates(
      files,
      importForm.category
    )
    importFiles.value = []
    await loadTemplates()
    const summary = response.summary || {}
    message.success(`导入完成：新增 ${summary.created || 0}，重复 ${summary.duplicate || 0}，失败 ${summary.failed || 0}`)
    if (summary.created) showImport.value = false
  } catch (error) {
    message.error(error.message || '模板批量导入失败')
  }
}

async function selectLibraryImage(item) {
  if (!item?.asset_id || store.isRunning) return
  try {
    const response = await materialLibraryApi.getItemFile(item.id)
    const nextPreviewUrl = URL.createObjectURL(await response.blob())
    if (productPreviewUrl.value) URL.revokeObjectURL(productPreviewUrl.value)
    productPreviewUrl.value = nextPreviewUrl
    productAsset.value = {
      id: item.asset_id,
      materialItemId: item.id,
      galleryId: item.category,
      categoryName: item.category_name,
      previewUrl: nextPreviewUrl,
      localName: item.name,
      width: item.width,
      height: item.height,
      source: 'library'
    }
    previewDataUrl.value = ''
  } catch (error) {
    message.error(error.message || '素材库底图加载失败')
  }
}

function removeProduct() {
  if (!productAsset.value) return
  if (productPreviewUrl.value) URL.revokeObjectURL(productPreviewUrl.value)
  productPreviewUrl.value = ''
  productAsset.value = null
  previewDataUrl.value = ''
}

function onDragOver(role, event) {
  if (store.isRunning) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
  dragging.value = role
}

function onDragLeave(role, event) {
  if (event.currentTarget.contains(event.relatedTarget)) return
  if (dragging.value === role) dragging.value = ''
}

function onDrop(role, event) {
  event.preventDefault()
  dragging.value = ''
  const files = Array.from(event.dataTransfer.files || [])
  if (!files.length) return
  if (role === 'library') importFiles.value = files
}

function resetTransform() {
  Object.assign(form, {
    fit: 'cover',
    scale: 1,
    focalX: 0.5,
    focalY: 0.5,
    xOffset: 0,
    yOffset: 0
  })
  previewDataUrl.value = ''
}

async function saveAnnotation() {
  if (!selectedTemplate.value) return
  const right = annotation.x + annotation.width
  const bottom = annotation.y + annotation.height
  if (right > 1 || bottom > 1) {
    message.warning('底图区域不能超出画布')
    return
  }
  try {
    const updated = await store.updatePosterTemplate(selectedTemplate.value.id, {
      product_box: {
        x: Number(annotation.x),
        y: Number(annotation.y),
        width: Number(annotation.width),
        height: Number(annotation.height)
      },
      status: 'ready'
    })
    chooseTemplate(updated)
    message.success('底图展示区域已保存，模板现在可以使用')
  } catch (error) {
    message.error(error.message || '底图区域保存失败')
  }
}

async function deleteTemplate(item) {
  if (!window.confirm(`确定删除模板“${item.name}”吗？`)) return
  try {
    await store.deletePosterTemplate(item.id)
    if (selectedTemplateId.value === item.id) {
      selectedTemplateId.value = ''
      selectedTemplateRecord.value = null
      previewDataUrl.value = ''
    }
    message.success('模板已删除')
  } catch (error) {
    message.error(error.message || '模板删除失败')
  }
}

async function preview() {
  if (!canPreview.value) return
  try {
    const response = await store.previewPosterBillboard(basePayload())
    for (const slot of response.copy_plan?.slots || []) {
      if (slot.editable) form.copyOverrides[slot.slot_id] = slot.text
    }
    previewDataUrl.value = response.preview_data_url
    message.success('预览已更新')
  } catch (error) {
    message.error(error.message || '大字报预览失败')
  }
}

async function reanalyzeTemplate() {
  if (!selectedTemplate.value) return
  try {
    const updated = await store.analyzePosterTemplate(selectedTemplate.value.id)
    chooseTemplate(updated)
    message.success('模板已重新分析')
  } catch (error) {
    message.error(error.message || '模板重新分析失败')
  }
}

async function saveTemplateMetadata() {
  if (!selectedTemplate.value) return
  if (!templateForm.name.trim() || !templateForm.category) return message.warning('请填写名称并选择分类')
  try {
    const payload = {
      name: templateForm.name.trim(),
      category: templateForm.category
    }
    if (templateForm.status !== 'needs_annotation') payload.status = templateForm.status
    const updated = await store.updatePosterTemplate(selectedTemplate.value.id, payload)
    chooseTemplate(updated)
    message.success('模板信息已保存')
  } catch (error) {
    message.error(error.message || '模板信息保存失败')
  }
}

async function generate() {
  if (!canGenerate.value) return
  try {
    const job = await store.submit('poster', {
      ...basePayload(),
      enhance_with_image2: form.enhance,
      enhancement_prompt: form.enhancementPrompt,
      negative_prompt: form.negativePrompt || null,
      n: form.enhance ? Number(form.count) : 1
    })
    await router.replace({ query: { ...route.query, job: job.id } })
    message.success('大字报任务已提交')
  } catch (error) {
    message.error(error.message || '大字报任务提交失败')
  }
}

async function restoreFromJob(job) {
  if (job?.mode !== 'poster_billboard') return
  const version = ++restoreJobVersion
  if (!store.posterTemplates.length) await loadTemplates()
  const request = job.request || {}
  selectedTemplateId.value = request.poster_template_id || ''
  const item = store.posterTemplates.find((template) => template.id === selectedTemplateId.value)
  if (item) chooseTemplate(item)
  const transform = request.transform || {}
  form.title = request.title || ''
  form.fit = transform.fit || 'cover'
  form.scale = transform.scale ?? 1
  form.focalX = transform.focal_x ?? 0.5
  form.focalY = transform.focal_y ?? 0.5
  form.xOffset = transform.x_offset ?? 0
  form.yOffset = transform.y_offset ?? 0
  form.enhance = Boolean(request.enhance_with_image2)
  form.enhancementPrompt = request.enhancement_prompt || ''
  form.negativePrompt = request.negative_prompt || ''
  form.count = request.n || 1
  form.copyOverrides = { ...(request.copy_overrides || {}) }
  if (request.product_asset_id) {
    try {
      const previewUrl = await store.loadAssetPreviewUrl(request.product_asset_id)
      if (version !== restoreJobVersion || store.currentJob?.id !== job.id) {
        URL.revokeObjectURL(previewUrl)
        return
      }
      if (productPreviewUrl.value) URL.revokeObjectURL(productPreviewUrl.value)
      productPreviewUrl.value = previewUrl
      productAsset.value = {
        id: request.product_asset_id,
        previewUrl,
        localName: '历史任务底图',
        source: 'history'
      }
    } catch (error) {
      if (version === restoreJobVersion) {
        productAsset.value = null
        message.warning(error.message || '历史任务底图已被删除，无法恢复预览')
      }
    }
  }
}

watch(() => store.currentJob, (job) => { void restoreFromJob(job) }, { deep: false })
watch(() => props.contentTaskId, () => { previewDataUrl.value = '' })

onMounted(async () => {
  const response = await materialLibraryApi.listCategories('cover_template')
  categoryOptions.value = (response.categories || []).filter((item) => item.code !== 'uncategorized')
  await loadTemplates()
  await restoreFromJob(store.currentJob)
})

onBeforeUnmount(() => {
  restoreJobVersion += 1
  if (productPreviewUrl.value) URL.revokeObjectURL(productPreviewUrl.value)
})
</script>

<template>
  <div class="poster-panel">
    <nav class="workflow-steps" aria-label="大字报制作进度">
      <div v-for="(label, index) in ['选择模板', '选择底图', '调整内容', '预览生成']" :key="label" class="workflow-step" :data-state="stepState(index + 1)">
        <span class="step-index"><Check v-if="stepState(index + 1) === 'done'" :size="14" />{{ stepState(index + 1) === 'done' ? '' : index + 1 }}</span>
        <span>{{ label }}</span>
      </div>
    </nav>

    <section class="poster-section library-section">
      <div class="section-head">
        <div class="section-title"><span class="section-number">1</span><div><strong>选择封面模板</strong><small>透明 PNG 模板中的文字、装饰和品牌元素会覆盖到底图上</small></div></div>
        <div class="section-actions"><span class="result-count">{{ store.posterTemplateTotal }} 个结果</span><button type="button" class="secondary" @click="showImport = !showImport"><FolderUp :size="16" />{{ showImport ? '收起导入' : '导入模板' }}</button></div>
      </div>

      <div class="library-toolbar">
        <label class="search-field"><span class="sr-only">搜索模板</span><Search :size="16" /><input v-model="filters.query" placeholder="搜索模板名称" @keyup.enter="page = 1; loadTemplates()" /></label>
        <label><span class="sr-only">模板分类</span><select v-model="filters.category" @change="page = 1; loadTemplates()"><option value="">全部分类</option><option v-for="item in categoryOptions" :key="item.code" :value="item.code">{{ item.name }}</option></select></label>
        <label><span class="sr-only">模板状态</span><select v-model="filters.status" @change="page = 1; loadTemplates()"><option value="">全部状态</option><option value="ready">可使用</option><option value="needs_review">待校对</option><option value="needs_annotation">待标注</option><option value="disabled">已停用</option></select></label>
        <button type="button" class="secondary" :disabled="store.loading.posterTemplates" @click="page = 1; loadTemplates()"><SlidersHorizontal :size="15" />筛选</button>
        <button v-if="hasFilters" type="button" class="text-button" @click="clearFilters"><X :size="14" />清除</button>
      </div>

      <div v-if="showImport" class="import-panel">
        <label class="dropzone compact" :class="{ dragging: dragging === 'library' }" @dragover="onDragOver('library', $event)" @dragleave="onDragLeave('library', $event)" @drop="onDrop('library', $event)">
          <UploadCloud :size="24" /><strong>{{ importFiles.length ? `已选择 ${importFiles.length} 张模板` : '拖拽或点击选择透明 PNG 模板' }}</strong><span>建议使用透明背景 PNG；单次最多 100 张，单张不超过 20 MB</span>
          <input type="file" accept="image/png,image/jpeg,image/webp" multiple @change="collectFiles" />
        </label>
        <div class="import-settings">
          <label><span>统一分类</span><select v-model="importForm.category"><option value="" disabled>请选择分类</option><option v-for="item in categoryOptions" :key="item.code" :value="item.code">{{ item.name }} — {{ item.description }}</option></select></label>
          <button type="button" class="secondary" :disabled="!importFiles.length || store.loading.posterImport" @click="importTemplates()">{{ store.loading.posterImport ? '正在导入…' : `确认导入${importFiles.length ? ` ${importFiles.length} 张` : ''}` }}</button>
        </div>
      </div>

      <div v-if="store.loading.posterTemplates" class="library-loading" role="status"><RefreshCw :size="18" />正在加载模板…</div>
      <div v-else-if="store.posterTemplates.length" class="poster-library">
        <article v-for="item in store.posterTemplates" :key="item.id" class="poster-template-card" :class="{ active: selectedTemplateId === item.id }">
          <button type="button" class="template-select" :aria-pressed="selectedTemplateId === item.id" @click="chooseTemplate(item)">
            <span class="template-image"><img :src="item.thumbnail_url" :alt="`${item.name} 模板预览`" /><span v-if="selectedTemplateId === item.id" class="selected-mark"><Check :size="14" />已选择</span></span>
            <span class="template-meta"><strong :title="item.name">{{ item.name }}</strong><small>{{ item.category_name }}</small><em :data-status="item.status">{{ item.status === 'ready' ? '可使用' : item.status === 'needs_review' ? '待校对' : item.status === 'needs_annotation' ? '待标注' : '已停用' }}</em></span>
          </button>
          <button type="button" class="delete-template" :aria-label="`删除模板 ${item.name}`" @click="deleteTemplate(item)"><Trash2 :size="15" /></button>
        </article>
      </div>
      <div v-else class="library-empty">
        <Layers3 :size="30" />
        <strong>{{ hasFilters ? '没有找到符合条件的模板' : '素材库还没有封面模板' }}</strong>
        <span>{{ hasFilters ? '可以清除筛选，或换一个关键词继续查找。' : '导入第一批透明 PNG 模板后，就可以开始制作大字报。' }}</span>
        <button type="button" class="secondary" @click="hasFilters ? clearFilters() : (showImport = true)">{{ hasFilters ? '清除全部筛选' : '立即导入模板' }}</button>
      </div>
      <div v-if="store.posterTemplateTotal > 24" class="pagination"><button type="button" :disabled="page <= 1" @click="page--; loadTemplates()">上一页</button><span>第 {{ page }} / {{ totalPages }} 页</span><button type="button" :disabled="page >= totalPages" @click="page++; loadTemplates()">下一页</button></div>
    </section>

    <div v-if="selectedTemplate" class="selected-template-bar">
      <img :src="selectedTemplate.thumbnail_url" :alt="`${selectedTemplate.name} 缩略图`" />
      <div><small>当前封面模板</small><strong>{{ selectedTemplate.name }}</strong><span>{{ selectedTemplate.category_name }} · {{ templateReady ? '可以直接使用' : '需要先标注底图区域' }}</span></div>
      <button type="button" class="text-button" @click="showManagement = !showManagement">管理模板<ChevronUp v-if="showManagement" :size="15" /><ChevronDown v-else :size="15" /></button>
    </div>

    <section v-if="selectedTemplate && showManagement" class="poster-section template-management">
      <div class="section-head"><div><strong>模板信息</strong><small>只影响素材库中的名称、分类和状态，不会改变画面内容</small></div></div>
      <div class="management-grid">
        <label><span>名称</span><input v-model="templateForm.name" maxlength="255" /></label>
        <label><span>分类</span><select v-model="templateForm.category"><option value="" disabled>请选择分类</option><option v-for="item in categoryOptions" :key="item.code" :value="item.code">{{ item.name }}</option></select></label>
        <label><span>状态</span><select v-model="templateForm.status"><option v-if="selectedTemplate.product_box && selectedTemplate.status !== 'needs_review'" value="ready">可使用</option><option v-if="selectedTemplate.status === 'needs_review'" value="needs_review" disabled>待校对（请前往素材库确认）</option><option value="disabled">已停用</option><option v-if="!selectedTemplate.product_box" value="needs_annotation">待标注</option></select></label>
      </div>
      <button type="button" class="secondary management-save" @click="saveTemplateMetadata">保存模板信息</button>
    </section>

    <section v-if="selectedTemplate && selectedTemplate.status === 'needs_annotation'" class="poster-section annotation-editor">
      <div class="section-head"><div class="section-title"><span class="section-number warning">!</span><div><strong>标注底图展示区域</strong><small>在画布上确认素材库图片出现的位置，保存后即可使用该模板</small></div></div></div>
      <div class="annotation-layout">
        <div class="annotation-stage"><img :src="selectedTemplate.thumbnail_url" alt="待标注模板" /><span class="annotation-box" :style="{ left: `${annotation.x * 100}%`, top: `${annotation.y * 100}%`, width: `${annotation.width * 100}%`, height: `${annotation.height * 100}%` }"><b>底图区域</b></span></div>
        <div class="annotation-controls">
          <label><span>左侧 <b>{{ Math.round(annotation.x * 100) }}%</b></span><input v-model.number="annotation.x" type="range" min="0" max="0.9" step="0.01" /></label>
          <label><span>顶部 <b>{{ Math.round(annotation.y * 100) }}%</b></span><input v-model.number="annotation.y" type="range" min="0" max="0.9" step="0.01" /></label>
          <label><span>宽度 <b>{{ Math.round(annotation.width * 100) }}%</b></span><input v-model.number="annotation.width" type="range" min="0.1" :max="1 - annotation.x" step="0.01" /></label>
          <label><span>高度 <b>{{ Math.round(annotation.height * 100) }}%</b></span><input v-model.number="annotation.height" type="range" min="0.1" :max="1 - annotation.y" step="0.01" /></label>
          <div class="annotation-actions"><button type="button" class="secondary" @click="reanalyzeTemplate"><RefreshCw :size="14" />重新分析</button><button type="button" class="primary inline" @click="saveAnnotation">保存区域并启用</button></div>
        </div>
      </div>
    </section>

    <section v-if="!selectedTemplate" class="next-step-placeholder">
      <span class="section-number muted">2</span><div><strong>选择模板后继续选择素材库底图</strong><small v-if="productAsset">你已选择的底图会继续保留，不需要重新选择。</small><small v-else>下方的画布、排版和生成设置会根据所选模板自动展开。</small></div>
    </section>

    <template v-else-if="templateReady">
      <section class="poster-section product-editor">
        <div class="section-head">
          <div class="section-title"><span class="section-number">2</span><div><strong>选择素材库底图并调整构图</strong><small>底图与透明模板会在画布中实时叠加，拖动参数即可查看构图变化</small></div></div>
          <button v-if="productAsset" type="button" class="text-button" @click="resetTransform"><RotateCcw :size="14" />恢复默认</button>
        </div>
        <div class="product-layout">
          <div class="canvas-column">
            <PosterCanvasPreview
              :template-url="selectedTemplate.thumbnail_url"
              :background-url="productAsset?.previewUrl || ''"
              :precise-preview-url="previewDataUrl"
              :template-type="selectedTemplate.template_type || 'alpha_overlay'"
              :product-box="selectedTemplate.product_box"
              :transform="transformPayload()"
              :canvas-width="selectedTemplate.canvas_width || 1080"
              :canvas-height="selectedTemplate.canvas_height || 1440"
            />
            <button v-if="!productAsset" type="button" class="image-library-empty" :disabled="store.isRunning" @click="imagePickerOpen = true">
              <ImagePlus :size="22" />
              <span><strong>从素材库选择底图</strong><small>选择图库中的一张图片进行合成</small></span>
            </button>
            <div v-else class="selected-library-image">
              <div><small>当前素材库底图</small><strong :title="productAsset.localName">{{ productAsset.localName }}</strong><span>{{ productAsset.categoryName || (productAsset.source === 'history' ? '来自历史任务' : '素材库') }}<template v-if="productAsset.width && productAsset.height"> · {{ productAsset.width }} × {{ productAsset.height }}</template></span></div>
              <button type="button" class="text-button" :disabled="store.isRunning" @click="imagePickerOpen = true">更换</button>
              <button type="button" class="text-button danger" :disabled="store.isRunning" @click="removeProduct">清除</button>
            </div>
          </div>
          <div class="transform-controls" :class="{ disabled: !productAsset }">
            <div class="transform-heading"><strong>底图构图</strong><small>实时画布显示位置与裁切参考；更新精确预览后可确认最终像素效果。</small></div>
            <label class="fit-control"><span>显示方式</span><select v-model="form.fit" :disabled="!productAsset" @change="previewDataUrl = ''"><option value="cover">填满底图区域</option><option value="contain">完整显示底图</option></select></label>
            <label><span>缩放 <b>{{ Number(form.scale).toFixed(2) }}×</b></span><input v-model.number="form.scale" :disabled="!productAsset" type="range" min="0.5" max="2" step="0.01" @input="previewDataUrl = ''" /></label>
            <label><span>水平焦点 <b>{{ Math.round(form.focalX * 100) }}%</b></span><input v-model.number="form.focalX" :disabled="!productAsset" type="range" min="0" max="1" step="0.01" @input="previewDataUrl = ''" /></label>
            <label><span>垂直焦点 <b>{{ Math.round(form.focalY * 100) }}%</b></span><input v-model.number="form.focalY" :disabled="!productAsset" type="range" min="0" max="1" step="0.01" @input="previewDataUrl = ''" /></label>
            <label><span>水平移动 <b>{{ Math.round(form.xOffset * 100) }}%</b></span><input v-model.number="form.xOffset" :disabled="!productAsset" type="range" min="-0.5" max="0.5" step="0.01" @input="previewDataUrl = ''" /></label>
            <label><span>垂直移动 <b>{{ Math.round(form.yOffset * 100) }}%</b></span><input v-model.number="form.yOffset" :disabled="!productAsset" type="range" min="-0.5" max="0.5" step="0.01" @input="previewDataUrl = ''" /></label>
          </div>
        </div>
      </section>

      <section class="poster-section content-editor" :class="{ locked: !productAsset }">
        <div class="section-head"><div class="section-title"><span class="section-number">3</span><div><strong>调整文字与美化效果</strong><small>关联内容资产时，系统会概括内容并适配可编辑文字槽</small></div></div><button type="button" class="text-button" @click="reanalyzeTemplate"><RefreshCw :size="14" />重新识别文字</button></div>
        <fieldset :disabled="!productAsset">
          <label class="wide"><span>封面主标题 <small>留空时使用关联内容资产自动概括</small></span><input v-model="form.title" maxlength="60" placeholder="例如：4 大产品服务" @input="previewDataUrl = ''" /></label>
          <div v-if="editableSlots.length" class="slot-grid">
            <label v-for="slot in editableSlots" :key="slot.id"><span>{{ slot.role }} <small>最多 {{ slot.max_chars }} 字</small></span><input v-model="form.copyOverrides[slot.id]" :maxlength="slot.max_chars" :placeholder="slot.source_text" @input="previewDataUrl = ''" /></label>
          </div>
          <p v-else class="slot-empty">当前模板没有识别到可编辑文字槽，固定文字、Logo 和装饰会原样保留。</p>
          <button type="button" class="advanced-toggle" @click="showAdvanced = !showAdvanced"><span><WandSparkles :size="16" />智能美化（可选）</span><small>使用 image2 优化背景、光影与边缘融合</small><ChevronUp v-if="showAdvanced" :size="16" /><ChevronDown v-else :size="16" /></button>
          <div v-if="showAdvanced" class="advanced-panel">
            <label class="switch-row"><input v-model="form.enhance" type="checkbox" /><span>开启 image2 智能美化</span></label>
            <p v-if="form.enhance && !image2Ready" class="warning-text">请先在页面右上角完成 image2 全局配置。</p>
            <p v-if="form.enhance && selectedTemplate.template_type !== 'alpha_overlay'" class="warning-text">当前是不透明模板，不能使用 image2；请改用透明 PNG 模板或关闭智能美化。</p>
            <template v-if="form.enhance">
              <label><span>美化要求</span><textarea v-model="form.enhancementPrompt" rows="2" placeholder="例如：增加柔和自然光与产品周围的轻微投影" /></label>
              <label><span>不希望出现</span><input v-model="form.negativePrompt" placeholder="例如：产品变形、过度锐化、杂乱背景" /></label>
              <label><span>生成数量</span><select v-model.number="form.count"><option :value="1">1 张</option><option :value="2">2 张</option><option :value="3">3 张</option><option :value="4">4 张</option></select></label>
            </template>
          </div>
        </fieldset>
        <div v-if="!productAsset" class="section-lock-note">选择素材库底图后即可调整文字和美化效果</div>
      </section>

      <section class="poster-section output-section">
        <div class="section-head"><div class="section-title"><span class="section-number">4</span><div><strong>精确预览并生成</strong><small>后端会按正式合成规则生成精确预览，并替换上方实时画布内容</small></div></div></div>
        <div v-if="previewDataUrl" class="preview-confirmation"><Check :size="20" /><div><strong>后端精确预览已应用</strong><small>上方画布现在显示最终文字、装饰、底图裁切和图层顺序。</small></div></div>
        <div v-else class="preview-placeholder"><Eye :size="24" /><span>{{ productAsset ? '点击“生成精确预览”确认最终合成效果' : '选择素材库底图后可生成精确预览' }}</span></div>
        <div class="output-actions">
          <div class="readiness"><span :class="{ ready: selectedTemplate }"><Check :size="13" />封面模板</span><span :class="{ ready: productAsset }"><Check :size="13" />素材库底图</span><span :class="{ ready: previewDataUrl }"><Check :size="13" />精确预览</span><small>{{ generateHint }}</small></div>
          <button type="button" class="secondary preview-button" :disabled="!canPreview" @click="preview"><Eye :size="16" />{{ store.loading.posterPreview ? '正在生成预览…' : previewDataUrl ? '更新精确预览' : '生成精确预览' }}</button>
          <button type="button" class="primary generate-button" :disabled="!canGenerate" @click="generate"><WandSparkles :size="18" />{{ store.loading.submit ? '正在提交…' : '生成高清大字报' }}</button>
        </div>
      </section>
    </template>

    <MaterialImagePickerModal
      v-model:open="imagePickerOpen"
      :selected-item-id="productAsset?.materialItemId || ''"
      :selected-gallery-id="productAsset?.galleryId || ''"
      :disabled="store.isRunning"
      @select="selectLibraryImage"
    />
  </div>
</template>

<style scoped lang="less">
.poster-panel { display: grid; gap: 16px; margin-top: 20px; color: var(--color-text); }
.workflow-steps { display: grid; grid-template-columns: repeat(4, 1fr); padding: 13px 18px; border: 1px solid var(--gray-150); border-radius: 10px; background: var(--gray-25); }
.workflow-step { position: relative; display: flex; align-items: center; justify-content: center; gap: 8px; color: var(--gray-500); font-size: 13px; }
.workflow-step:not(:last-child)::after { content: ''; position: absolute; right: -15%; width: 30%; height: 1px; background: var(--gray-200); }
.workflow-step[data-state='active'] { color: var(--main-700); font-weight: 650; }.workflow-step[data-state='done'] { color: var(--color-success-700); }
.step-index, .section-number { width: 25px; height: 25px; flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; color: var(--gray-600); background: var(--gray-100); font-size: 12px; font-weight: 700; }
.workflow-step[data-state='active'] .step-index { color: var(--main-0); background: var(--main-700); }.workflow-step[data-state='done'] .step-index { color: var(--color-success-700); background: var(--color-success-50); }
.poster-section { padding: 18px; border: 1px solid var(--gray-150); border-radius: 10px; background: var(--gray-0); }
.section-head, .section-actions, .section-title, .selected-template-bar, .output-actions, .readiness, .advanced-toggle { display: flex; align-items: center; }
.section-head { justify-content: space-between; gap: 14px; align-items: flex-start; }.section-title { gap: 10px; }.section-title > div, .section-head > div:not(.section-title) { display: grid; gap: 3px; }
.section-head strong { font-size: 15px; }.section-head small { color: var(--color-text-secondary); font-size: 12px; }.section-actions { gap: 10px; }.result-count { color: var(--color-text-secondary); font-size: 12px; }
.section-number { color: var(--main-700); background: var(--main-50); }.section-number.warning { color: var(--color-warning-900); background: var(--color-warning-50); }.section-number.muted { color: var(--gray-500); background: var(--gray-100); }
.library-toolbar { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr) 130px auto auto; gap: 8px; margin-top: 16px; }
.library-toolbar label { position: relative; }.search-field svg { position: absolute; z-index: 1; left: 11px; top: 11px; color: var(--gray-500); }.search-field input { padding-left: 34px; }
input, select, textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--gray-200); border-radius: 7px; padding: 9px 10px; color: var(--color-text); background: var(--gray-0); outline: none; }
input:focus, select:focus, textarea:focus, button:focus-visible { border-color: var(--main-500); box-shadow: 0 0 0 2px var(--main-50); outline: none; }
input:disabled, select:disabled, textarea:disabled { cursor: not-allowed; color: var(--gray-400); background: var(--gray-50); }
.import-panel { display: grid; grid-template-columns: 1.2fr 1fr; gap: 12px; margin-top: 12px; padding: 12px; border-radius: 8px; background: var(--gray-25); }
.import-settings { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; align-content: center; }.import-settings label { display: grid; gap: 5px; color: var(--color-text-secondary); font-size: 12px; }.import-settings button { grid-column: 1 / -1; }
.dropzone { min-height: 132px; border: 1px dashed var(--main-300); border-radius: 8px; display: grid; place-content: center; justify-items: center; gap: 6px; padding: 12px; color: var(--main-700); background: var(--main-30); text-align: center; cursor: pointer; }.dropzone:hover { border-color: var(--main-500); background: var(--main-50); }.dropzone span { color: var(--color-text-secondary); font-size: 12px; }.dropzone input { display: none; }.dropzone.compact { min-height: 94px; }.dropzone.dragging { border-color: var(--main-700); box-shadow: 0 0 0 3px var(--main-100); }
.library-loading, .library-empty { min-height: 112px; margin-top: 12px; border-radius: 8px; display: grid; place-content: center; justify-items: center; gap: 7px; color: var(--color-text-secondary); background: var(--gray-25); text-align: center; }.library-loading svg { animation: spin 1s linear infinite; }.library-empty strong { color: var(--color-text); }.library-empty span { max-width: 420px; font-size: 12px; }
.poster-library { display: grid; grid-template-columns: repeat(4, minmax(130px, 1fr)); gap: 10px; margin-top: 14px; }.poster-template-card { position: relative; min-width: 0; border: 1px solid var(--gray-150); border-radius: 8px; overflow: hidden; background: var(--gray-0); }.poster-template-card:hover { border-color: var(--gray-300); }.poster-template-card.active { border-color: var(--main-500); box-shadow: 0 0 0 2px var(--main-50); }
.template-select { width: 100%; padding: 7px; border: 0; display: grid; gap: 7px; text-align: left; color: inherit; background: transparent; cursor: pointer; }.template-image { position: relative; display: block; }.template-image img { display: block; width: 100%; aspect-ratio: 3 / 4; object-fit: contain; border-radius: 5px; background: var(--gray-50); }.selected-mark { position: absolute; left: 6px; bottom: 6px; padding: 3px 6px; border-radius: 999px; display: flex; align-items: center; gap: 3px; color: var(--main-0); background: var(--main-700); font-size: 10px; }
.template-meta { min-width: 0; display: grid; grid-template-columns: 1fr auto; gap: 2px 6px; align-items: center; }.template-meta strong { grid-column: 1 / -1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }.template-meta small { overflow: hidden; color: var(--color-text-secondary); text-overflow: ellipsis; white-space: nowrap; }.template-meta em { padding: 2px 5px; border-radius: 999px; color: var(--gray-600); background: var(--gray-100); font-size: 10px; font-style: normal; }.template-meta em[data-status='ready'] { color: var(--color-success-700); background: var(--color-success-50); }.template-meta em[data-status='needs_annotation'], .template-meta em[data-status='needs_review'] { color: var(--color-warning-900); background: var(--color-warning-50); }
.delete-template { position: absolute; top: 11px; right: 11px; width: 30px; height: 30px; border: 0; border-radius: 50%; display: grid; place-items: center; color: var(--gray-0); background: var(--dark-70); cursor: pointer; }.delete-template:hover { background: var(--color-error-700); }
.pagination { margin-top: 11px; display: flex; justify-content: flex-end; gap: 10px; align-items: center; color: var(--color-text-secondary); font-size: 12px; }.pagination button { border: 0; padding: 6px 8px; color: var(--main-700); background: transparent; cursor: pointer; }.pagination button:disabled { color: var(--gray-400); cursor: not-allowed; }
.selected-template-bar { gap: 12px; padding: 10px 12px; border: 1px solid var(--main-100); border-radius: 8px; background: var(--main-30); }.selected-template-bar img { width: 54px; height: 68px; border-radius: 5px; object-fit: contain; background: var(--gray-50); }.selected-template-bar > div { min-width: 0; display: grid; gap: 2px; }.selected-template-bar small, .selected-template-bar span { color: var(--color-text-secondary); font-size: 11px; }.selected-template-bar strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.selected-template-bar .text-button { margin-left: auto; }
.management-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; margin-top: 12px; }.management-grid label, .annotation-controls label, .transform-controls label, .slot-grid label, .wide, .advanced-panel > label { display: grid; gap: 5px; color: var(--color-text-secondary); font-size: 12px; }.management-save { margin-top: 10px; }
.annotation-layout { display: grid; grid-template-columns: minmax(180px, 280px) 1fr; gap: 20px; margin-top: 14px; }.annotation-stage { position: relative; overflow: hidden; border-radius: 7px; background: var(--gray-50); }.annotation-stage img { display: block; width: 100%; aspect-ratio: 3 / 4; object-fit: contain; }.annotation-box { position: absolute; border: 2px dashed var(--main-600); background: var(--main-50); opacity: .82; }.annotation-box b { position: absolute; top: 4px; left: 4px; padding: 2px 5px; color: var(--main-0); background: var(--main-700); font-size: 10px; }.annotation-controls { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-content: center; }.annotation-controls b, .transform-controls b { float: right; color: var(--main-700); }.annotation-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 8px; }
.next-step-placeholder { min-height: 74px; padding: 16px 18px; border: 1px dashed var(--gray-200); border-radius: 10px; display: flex; align-items: center; gap: 12px; color: var(--color-text-secondary); background: var(--gray-25); }.next-step-placeholder > div { display: grid; gap: 3px; }.next-step-placeholder strong { color: var(--color-text); }.next-step-placeholder small { font-size: 12px; }
.product-layout { display: grid; grid-template-columns: minmax(280px, 380px) 1fr; gap: 22px; margin-top: 14px; }.canvas-column { min-width: 0; display: grid; gap: 10px; align-content: start; }
.image-library-empty { width: 100%; padding: 12px; border: 1px dashed var(--main-300); border-radius: 8px; display: flex; align-items: center; justify-content: center; gap: 9px; color: var(--main-700); background: var(--main-30); cursor: pointer; }.image-library-empty > span { display: grid; gap: 2px; text-align: left; }.image-library-empty small { color: var(--color-text-secondary); font-size: 11px; }.image-library-empty:hover { border-color: var(--main-500); background: var(--main-50); }.image-library-empty:disabled { opacity: .45; cursor: not-allowed; }
.selected-library-image { padding: 10px 11px; border: 1px solid var(--main-100); border-radius: 8px; display: flex; align-items: center; gap: 5px; background: var(--main-30); }.selected-library-image > div { min-width: 0; flex: 1; display: grid; gap: 2px; }.selected-library-image small, .selected-library-image span { color: var(--color-text-secondary); font-size: 11px; }.selected-library-image strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }.text-button.danger { color: var(--color-error-700); }
.transform-controls { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; align-content: start; }.transform-controls.disabled { opacity: .55; }.transform-heading, .fit-control { grid-column: 1 / -1; }.transform-heading { padding-bottom: 10px; border-bottom: 1px solid var(--gray-150); display: grid; gap: 3px; }.transform-heading small { color: var(--color-text-secondary); font-size: 11px; line-height: 1.6; }
.content-editor { position: relative; }.content-editor fieldset { min-width: 0; margin: 14px 0 0; padding: 0; border: 0; }.content-editor.locked fieldset { opacity: .45; }.slot-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }.wide > span { display: flex; justify-content: space-between; gap: 8px; }.wide small, .slot-grid small { color: var(--gray-500); }.slot-empty { margin: 12px 0; color: var(--color-text-secondary); font-size: 12px; }
.advanced-toggle { width: 100%; margin-top: 12px; padding: 11px 12px; border: 1px solid var(--gray-150); border-radius: 7px; justify-content: space-between; gap: 10px; color: var(--color-text); background: var(--gray-25); text-align: left; cursor: pointer; }.advanced-toggle > span { display: flex; align-items: center; gap: 7px; font-weight: 600; }.advanced-toggle small { margin-left: auto; color: var(--color-text-secondary); }.advanced-panel { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--gray-150); border-top: 0; border-radius: 0 0 7px 7px; }.switch-row { grid-template-columns: auto 1fr !important; justify-content: start; align-items: center; }.switch-row input { width: auto; }.warning-text { margin: 0; color: var(--color-warning-900); font-size: 12px; }.section-lock-note { margin-top: 10px; color: var(--color-text-secondary); font-size: 12px; }
.preview-placeholder { min-height: 108px; margin-top: 14px; border: 1px dashed var(--gray-200); border-radius: 8px; display: grid; place-content: center; justify-items: center; gap: 7px; color: var(--color-text-secondary); background: var(--gray-25); font-size: 12px; }.preview-confirmation { margin-top: 14px; padding: 14px; border: 1px solid var(--color-success-100); border-radius: 8px; display: flex; align-items: center; gap: 10px; color: var(--color-success-700); background: var(--color-success-50); }.preview-confirmation > div { display: grid; gap: 3px; }.preview-confirmation small { color: var(--color-text-secondary); }
.output-actions { display: grid; grid-template-columns: auto auto; justify-content: end; gap: 9px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--gray-150); }.readiness { grid-column: 1 / -1; width: 100%; flex-wrap: wrap; gap: 6px; }.readiness > span { padding: 3px 7px; border-radius: 999px; display: inline-flex; align-items: center; gap: 3px; color: var(--gray-500); background: var(--gray-100); font-size: 11px; }.readiness > span svg { opacity: .35; }.readiness > span.ready { color: var(--color-success-700); background: var(--color-success-50); }.readiness > span.ready svg { opacity: 1; }.readiness small { flex-basis: 100%; color: var(--color-text-secondary); }
.primary, .secondary, .text-button { min-height: 38px; box-sizing: border-box; border-radius: 7px; padding: 8px 12px; display: inline-flex; justify-content: center; align-items: center; gap: 6px; cursor: pointer; }.primary { border: 1px solid var(--main-700); color: var(--main-0); background: var(--main-700); font-weight: 650; }.primary.inline { justify-self: auto; }.secondary { border: 1px solid var(--gray-150); color: var(--color-text); background: var(--gray-25); }.secondary:hover { border-color: var(--main-300); color: var(--main-700); }.text-button { min-height: 34px; border: 0; padding: 5px 7px; color: var(--main-700); background: transparent; }.text-button:hover { background: var(--main-50); }.primary:disabled, .secondary:disabled, .text-button:disabled { opacity: .45; cursor: not-allowed; }.generate-button { min-width: 150px; }
.sr-only { position: absolute !important; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 980px) { .poster-library { grid-template-columns: repeat(3, 1fr); }.library-toolbar { grid-template-columns: 1fr 1fr 120px auto; }.library-toolbar .text-button { grid-column: 1 / -1; justify-self: start; }.management-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 760px) { .workflow-steps { grid-template-columns: 1fr 1fr; gap: 12px; }.workflow-step::after { display: none; }.library-toolbar, .import-panel, .product-layout, .annotation-layout { grid-template-columns: 1fr; }.poster-library { grid-template-columns: repeat(2, 1fr); }.output-actions { grid-template-columns: 1fr; }.readiness { grid-column: 1; margin-bottom: 4px; }.preview-button, .generate-button { width: 100%; }.section-head { align-items: stretch; flex-direction: column; }.section-actions { justify-content: space-between; } }
@media (max-width: 520px) { .workflow-step { justify-content: flex-start; }.poster-library, .management-grid, .annotation-controls, .slot-grid, .transform-controls, .import-settings { grid-template-columns: 1fr; }.fit-control, .annotation-actions, .import-settings button { grid-column: 1; }.annotation-actions { flex-direction: column; }.selected-template-bar { align-items: flex-start; flex-wrap: wrap; }.selected-template-bar .text-button { margin-left: 0; }.advanced-toggle small { display: none; } }
</style>
