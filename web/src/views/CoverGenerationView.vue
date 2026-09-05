<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Download, ImagePlus, RefreshCw, Settings, Sparkles, WandSparkles, X } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import PosterBillboardPanel from '@/components/content/PosterBillboardPanel.vue'
import { useCoverGenerationStore } from '@/stores/coverGeneration'

const route = useRoute()
const router = useRouter()
const store = useCoverGenerationStore()
const tab = ref('compose')
const sourceAssets = ref([])
const templateAsset = ref(null)
const templateSourceAsset = ref(null)
const templatePlan = ref(null)
const templatePlanLoading = ref(false)
const maskAsset = ref(null)
const draggingRole = ref('')
const image2ConfigOpen = ref(false)
const image2ConfigSaving = ref(false)
const image2ConfigTesting = ref(false)
const previewUrls = new Map()
const form = reactive({
  contentTaskId: '',
  templateId: 'grid_3x3',
  themeId: 'editorial_ink',
  size: '1080x1440',
  title: '',
  prompt: '',
  negativePrompt: '',
  gap: 18,
  margin: 24,
  fit: 'cover',
  count: 1
})
const image2ConfigForm = reactive({ baseUrl: '', apiKey: '', model: 'gpt-image-2' })

const templates = computed(() => store.bootstrap?.templates || [])
const themes = computed(() => store.bootstrap?.themes || [])
const sizes = computed(() => store.bootstrap?.sizes || [])
const outputSizes = computed(() => (
  ['template', 'poster'].includes(tab.value)
    ? sizes.value.filter((item) => item.id === '1080x1440')
    : sizes.value
))
const tasks = computed(() => store.bootstrap?.content_tasks || [])
const activeTemplate = computed(() => templates.value.find((item) => item.id === form.templateId))
const templateAspectWarning = computed(() => {
  if (!templateAsset.value?.image_width || !templateAsset.value?.image_height) return ''
  const expected = form.size === '1080x1080' ? 1 : 0.75
  const actual = templateAsset.value.image_width / templateAsset.value.image_height
  if (Math.abs(actual - expected) / expected < 0.08) return ''
  return '模板比例与输出尺寸差异较大，导出时会按画布居中裁切；建议选择匹配比例的模板或输出尺寸。'
})
const canCancel = computed(() => store.isRunning && !['saving', 'cancel_requested'].includes(store.currentJob?.status))
const image2Ready = computed(() => Boolean(store.bootstrap?.image2?.configured))
const image2Verification = computed(() => store.bootstrap?.image2?.verification_status || 'unverified')
const image2StatusText = computed(() => {
  if (!image2Ready.value) return '未配置'
  if (image2Verification.value === 'verified') return '已验证'
  if (image2Verification.value === 'warning') return '已连通（模型别名）'
  return '待验证'
})
const templateCopySlots = computed(() => templatePlan.value?.copy_plan?.slots || [])
const canManageImage2 = computed(() => Boolean(
  store.bootstrap?.image2 && store.bootstrap.image2.can_manage !== false
))
const canSubmit = computed(() => {
  if (store.isRunning || store.loading.upload || store.loading.submit) return false
  if (tab.value === 'compose') {
    const count = sourceAssets.value.length
    return count >= (activeTemplate.value?.min_assets || 2) && count <= (activeTemplate.value?.max_assets || 9)
  }
  if (tab.value === 'poster') return false
  if (!image2Ready.value) return false
  if (sourceAssets.value.length > 9) return false
  if (tab.value === 'template') {
    return Boolean(templateAsset.value && templateSourceAsset.value)
  }
  if (maskAsset.value) return sourceAssets.value.length === 1 && Boolean(form.prompt.trim() || form.contentTaskId)
  return Boolean(form.prompt.trim() || form.contentTaskId)
})

const statusText = computed(() => {
  const map = {
    queued: '等待执行',
    running: '处理中',
    submitting: '正在提交 image2',
    polling: 'image2 正在生成',
    downloading: '正在获取结果',
    saving: '正在保存',
    cancel_requested: '正在取消',
    succeeded: '生成完成',
    failed: '生成失败',
    cancelled: '已取消'
  }
  return map[store.currentJob?.status] || store.currentJob?.status || ''
})

const isTemplateReplicationJob = (job) => (
  job?.mode === 'multi_reference' && Boolean(job.request?.template_asset_id)
)

const jobKindLabel = (job) => {
  if (job?.mode === 'compose') return '智能排版'
  if (job?.mode === 'poster_billboard') return '大字报'
  if (isTemplateReplicationJob(job)) return '模板复刻'
  return 'AI 生成'
}

function restoreTabFromJob(job) {
  if (job?.mode === 'compose') tab.value = 'compose'
  else if (job?.mode === 'poster_billboard') tab.value = 'poster'
  else if (isTemplateReplicationJob(job)) tab.value = 'template'
  else tab.value = 'ai'
}

const trackPreview = (asset, file) => {
  const url = URL.createObjectURL(file)
  previewUrls.set(asset.id, url)
  return { ...asset, previewUrl: url }
}

async function uploadSourceFiles(files) {
  if (!files.length) return
  const acceptedFiles = tab.value === 'template' ? files.slice(0, 1) : files
  if (tab.value === 'template' && files.length > 1) {
    message.info('模板复刻只使用一张原图，已取第一张文件')
  }
  try {
    const uploaded = await store.upload(acceptedFiles, 'source', form.contentTaskId || null)
    const items = uploaded.map((asset, index) => trackPreview(asset, acceptedFiles[index]))
    if (tab.value === 'template') {
      if (templateSourceAsset.value) await removeAsset(templateSourceAsset.value, 'template-source')
      templateSourceAsset.value = items[0]
      templatePlan.value = null
    } else {
      sourceAssets.value.push(...items)
    }
  } catch (error) {
    message.error(error.message || '原图上传失败')
  }
}

async function uploadSource(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  await uploadSourceFiles(files)
}

async function uploadSingleFile(file, role) {
  if (!file) return
  try {
    const [asset] = await store.upload([file], role, form.contentTaskId || null)
    const item = trackPreview(asset, file)
    if (role === 'template') {
      if (templateAsset.value) await removeAsset(templateAsset.value, 'template')
      templateAsset.value = item
      templatePlan.value = null
    } else {
      if (maskAsset.value) await removeAsset(maskAsset.value, 'mask')
      maskAsset.value = item
    }
  } catch (error) {
    message.error(error.message || '图片上传失败')
  }
}

async function uploadSingle(event, role) {
  const file = event.target.files?.[0]
  event.target.value = ''
  await uploadSingleFile(file, role)
}

function onDragOver(role, event) {
  if (store.isRunning) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
  draggingRole.value = role
}

function onDragLeave(role, event) {
  if (event.currentTarget.contains(event.relatedTarget)) return
  if (draggingRole.value === role) draggingRole.value = ''
}

async function onDrop(role, event) {
  event.preventDefault()
  if (store.isRunning) return
  draggingRole.value = ''
  const files = Array.from(event.dataTransfer.files || [])
  if (!files.length) return
  if (role === 'source') await uploadSourceFiles(files)
  else await uploadSingleFile(files[0], role)
}

async function removeAsset(asset, role = 'source') {
  if (!asset || store.isRunning) return
  try {
    await store.deleteAsset(asset.id)
  } catch (error) {
    message.error(error.message || '素材删除失败')
    return
  }
  const url = previewUrls.get(asset.id)
  if (url) URL.revokeObjectURL(url)
  previewUrls.delete(asset.id)
  if (role === 'source') sourceAssets.value = sourceAssets.value.filter((item) => item.id !== asset.id)
  else if (role === 'template') templateAsset.value = null
  else if (role === 'template-source') templateSourceAsset.value = null
  else maskAsset.value = null
  if (role === 'template' || role === 'template-source') templatePlan.value = null
}

function buildGeneratePayload() {
  let mode = 'text_to_image'
  if (tab.value === 'ai' && maskAsset.value) mode = 'mask'
  else if (tab.value === 'template' || sourceAssets.value.length > 1) mode = 'multi_reference'
  else if (sourceAssets.value.length === 1) mode = 'image_to_image'
  return {
    mode,
    content_task_id: form.contentTaskId || null,
    source_asset_ids: tab.value === 'template'
      ? [templateSourceAsset.value.id]
      : sourceAssets.value.map((item) => item.id),
    template_asset_id: tab.value === 'template' ? templateAsset.value?.id : null,
    mask_asset_id: mode === 'mask' ? maskAsset.value?.id : null,
    title: form.title,
    prompt: form.prompt,
    negative_prompt: form.negativePrompt || null,
    size: form.size,
    n: form.count,
    parameters: tab.value === 'template'
      ? { quality: 'high', output_format: 'png' }
      : {},
    copy_overrides: tab.value === 'template'
      ? Object.fromEntries(templateCopySlots.value.map((item) => [item.slot_id, item.text]))
      : {},
    layout_overrides: tab.value === 'template'
      ? Object.fromEntries(templateCopySlots.value.map((item) => [item.slot_id, {
          x_offset: (item.xOffsetPercent || 0) / 100,
          y_offset: (item.yOffsetPercent || 0) / 100,
          font_scale: item.fontScale || 1
        }]))
      : {}
  }
}

async function analyzeTemplatePlan() {
  if (!templateAsset.value || !templateSourceAsset.value || templatePlanLoading.value) return
  templatePlanLoading.value = true
  try {
    const layoutOverrides = Object.fromEntries(templateCopySlots.value.map((item) => [item.slot_id, {
      x_offset: (item.xOffsetPercent || 0) / 100,
      y_offset: (item.yOffsetPercent || 0) / 100,
      font_scale: item.fontScale || 1
    }]))
    const plan = await contentApi.previewCoverTemplateReplication({
      template_asset_id: templateAsset.value.id,
      source_asset_id: templateSourceAsset.value.id,
      content_task_id: form.contentTaskId || null,
      title: form.title,
      size: form.size,
      copy_overrides: Object.fromEntries(templateCopySlots.value.map((item) => [item.slot_id, item.text])),
      layout_overrides: layoutOverrides
    })
    for (const slot of plan.copy_plan?.slots || []) {
      const override = layoutOverrides[slot.slot_id]
      slot.xOffsetPercent = (override?.x_offset || 0) * 100
      slot.yOffsetPercent = (override?.y_offset || 0) * 100
      slot.fontScale = override?.font_scale || 1
    }
    templatePlan.value = plan
    message.success('模板槽位与文案已分析，可在生成前微调')
  } catch (error) {
    message.error(error.message || '模板分析失败')
  } finally {
    templatePlanLoading.value = false
  }
}

async function submit() {
  if (!canSubmit.value) return
  try {
    const payload = tab.value === 'compose'
      ? {
          asset_ids: sourceAssets.value.map((item) => item.id),
          template_id: form.templateId,
          theme_id: form.themeId,
          size: form.size,
          content_task_id: form.contentTaskId || null,
          layout: { title: form.title, gap: form.gap, margin: form.margin, fit: form.fit }
        }
      : buildGeneratePayload()
    const job = await store.submit(tab.value === 'compose' ? 'compose' : 'generate', payload)
    await router.replace({ query: { ...route.query, job: job.id } })
    message.success('封面任务已提交')
  } catch (error) {
    message.error(error.message || '任务提交失败')
  }
}

async function selectHistory(job) {
  try {
    const restored = await store.restore(job.id)
    restoreTabFromJob(restored)
    await router.replace({ query: { ...route.query, job: job.id } })
  } catch (error) {
    message.error(error.message || '任务读取失败')
  }
}

async function retryJob(job) {
  try {
    const created = await store.retry(job)
    await router.replace({ query: { ...route.query, job: created.id } })
  } catch (error) {
    message.error(error.message || '重试失败')
  }
}

function openImage2Config() {
  image2ConfigForm.baseUrl = store.bootstrap?.image2?.base_url || ''
  image2ConfigForm.apiKey = ''
  image2ConfigForm.model = store.bootstrap?.image2?.model || 'gpt-image-2'
  image2ConfigOpen.value = true
}

async function saveImage2Config() {
  if (!canManageImage2.value) return
  if (!image2ConfigForm.baseUrl.trim()) {
    message.warning('请填写 image2 中转站 Base URL')
    return
  }
  if (!store.bootstrap?.image2?.api_key_configured && !image2ConfigForm.apiKey.trim()) {
    message.warning('首次配置请填写 API Key')
    return
  }
  image2ConfigSaving.value = true
  try {
    await store.saveImage2Config({
      base_url: image2ConfigForm.baseUrl.trim(),
      api_key: image2ConfigForm.apiKey.trim() || null,
      model: image2ConfigForm.model.trim()
    })
    image2ConfigForm.apiKey = ''
    image2ConfigOpen.value = false
    message.success('image2 全局配置已保存')
  } catch (error) {
    message.error(error.message || 'image2 配置保存失败')
  } finally {
    image2ConfigSaving.value = false
  }
}

async function testImage2Config() {
  if (!canManageImage2.value || !image2ConfigForm.baseUrl.trim() || !image2ConfigForm.model.trim()) return
  if (!store.bootstrap?.image2?.api_key_configured && !image2ConfigForm.apiKey.trim()) {
    message.warning('首次配置请填写 API Key')
    return
  }
  image2ConfigTesting.value = true
  try {
    const response = await store.testImage2Config({
      base_url: image2ConfigForm.baseUrl.trim(),
      api_key: image2ConfigForm.apiKey.trim() || null,
      model: image2ConfigForm.model.trim()
    })
    image2ConfigForm.apiKey = ''
    message.success(response.profile?.model_discovered === false
      ? '中转站可访问，但模型列表未发现该模型，请确认别名'
      : '中转站与模型验证通过')
  } catch (error) {
    message.error(error.message || 'image2 连接验证失败')
  } finally {
    image2ConfigTesting.value = false
  }
}

async function setCurrent(assetId) {
  try {
    await store.setCurrent(assetId)
    message.success('已设为内容资产当前封面')
  } catch (error) {
    message.error(error.message || '设置当前封面失败')
  }
}

function downloadResult(item, index) {
  const link = document.createElement('a')
  link.href = item.url
  link.download = `小红书封面-${index + 1}.png`
  link.click()
}

async function changeContentTask() {
  templatePlan.value = null
  try {
    const query = { ...route.query }
    if (form.contentTaskId) query.task = form.contentTaskId
    else delete query.task
    delete query.job
    await router.replace({ query })
    store.clearCurrent()
    await store.loadHistory(form.contentTaskId || null)
  } catch (error) {
    message.error(error.message || '封面任务历史加载失败')
  }
}

async function ensureContentTask(taskId) {
  if (!taskId || tasks.value.some((item) => item.id === taskId)) return Boolean(taskId)
  try {
    const response = await contentApi.getTask(taskId)
    if (!response.task) return false
    store.bootstrap.content_tasks.unshift({
      id: response.task.id,
      name: response.task.name,
      status: response.task.status,
      updated_at: response.task.updated_at
    })
    return true
  } catch (error) {
    message.warning(error.message || '关联内容任务不存在或无权访问')
    return false
  }
}

watch(() => form.templateId, () => {
  const maximum = activeTemplate.value?.max_assets || 9
  if (sourceAssets.value.length > maximum) message.warning(`该版式最多使用 ${maximum} 张图片，请移除多余素材`)
})

watch(tab, (value) => {
  if (['template', 'poster'].includes(value)) form.size = '1080x1440'
})

watch(() => [form.contentTaskId, form.title, form.size], () => {
  if (tab.value === 'template') templatePlan.value = null
})

onMounted(async () => {
  try {
    await store.loadBootstrap()
    if (typeof route.query.task === 'string') {
      if (await ensureContentTask(route.query.task)) form.contentTaskId = route.query.task
    }
    const jobId = typeof route.query.job === 'string' ? route.query.job : ''
    if (jobId) {
      const restored = await store.restore(jobId)
      restoreTabFromJob(restored)
      if (
        !form.contentTaskId
        && restored.content_task_id
        && await ensureContentTask(restored.content_task_id)
      ) {
        form.contentTaskId = restored.content_task_id
      }
    }
    await store.loadHistory(form.contentTaskId || null)
  } catch (error) {
    message.error(error.message || '封面工作台加载失败')
  }
})

onBeforeUnmount(() => {
  previewUrls.forEach((url) => URL.revokeObjectURL(url))
  store.dispose()
})
</script>

<template>
  <main class="cover-page">
    <header class="page-head">
      <div>
        <p class="eyebrow">CONTENT COVER STUDIO</p>
        <h1>封面生成</h1>
        <p>将多张素材排成稳定版式，或使用 image2 根据内容资产生成小红书封面。</p>
      </div>
      <button class="image2-state" :class="{ ready: image2Ready && image2Verification === 'verified' }" @click="openImage2Config">
        <Settings :size="15" />image2 {{ image2StatusText }}
      </button>
    </header>

    <div class="workspace-grid">
      <section class="editor-panel">
        <div class="mode-tabs" role="tablist" aria-label="封面生成方式">
          <button :class="{ active: tab === 'compose' }" @click="tab = 'compose'">智能排版</button>
          <button :class="{ active: tab === 'ai' }" @click="tab = 'ai'">AI 封面</button>
          <button :class="{ active: tab === 'template' }" @click="tab = 'template'">模板复刻</button>
          <button :class="{ active: tab === 'poster' }" @click="tab = 'poster'">大字报</button>
        </div>

        <div class="form-grid">
          <label>
            <span>关联内容资产</span>
            <select v-model="form.contentTaskId" :disabled="store.isRunning" @change="changeContentTask">
              <option value="">不关联内容任务</option>
              <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.name }}</option>
            </select>
          </label>
          <label>
            <span>输出尺寸</span>
            <select v-model="form.size">
              <option v-for="size in outputSizes" :key="size.id" :value="size.id">{{ size.label }} · {{ size.id }}</option>
            </select>
          </label>
        </div>

        <div v-if="tab === 'compose'" class="section-block">
          <div class="section-title"><span>选择版式</span><small>版式配置由后端声明，后续可直接扩展</small></div>
          <div class="template-grid">
            <button
              v-for="item in templates"
              :key="item.id"
              class="template-card"
              :class="{ active: form.templateId === item.id }"
              @click="form.templateId = item.id"
            >
              <span class="layout-glyph" :data-layout="item.id"><i /><i /><i /><i /></span>
              <strong>{{ item.name }}</strong>
              <small>{{ item.min_assets }}–{{ item.max_assets }} 张</small>
            </button>
          </div>
          <div class="form-grid compact">
            <label><span>主题</span><select v-model="form.themeId"><option v-for="theme in themes" :key="theme.id" :value="theme.id">{{ theme.name }}</option></select></label>
            <label><span>裁切</span><select v-model="form.fit"><option value="cover">填满画面</option><option value="contain">完整显示</option></select></label>
            <label><span>间距 {{ form.gap }}px</span><input v-model.number="form.gap" type="range" min="0" max="80" /></label>
            <label><span>边距 {{ form.margin }}px</span><input v-model.number="form.margin" type="range" min="0" max="120" /></label>
          </div>
          <label class="wide-field"><span>可选标题</span><input v-model="form.title" maxlength="60" placeholder="排版封面上的简短标题" /></label>
          <div class="compose-preview">
            <div
              class="preview-stage"
              :data-layout="form.templateId"
              :style="{ '--preview-gap': `${Math.max(2, form.gap / 4)}px` }"
            >
              <img v-for="asset in sourceAssets" :key="asset.id" :src="asset.previewUrl" :alt="asset.localName" />
              <span v-if="!sourceAssets.length">上传图片后在这里预览版式</span>
              <strong v-if="form.title">{{ form.title }}</strong>
            </div>
            <small>版式预览仅用于确认图片顺序，最终导出由后端按 1080 像素画板渲染。</small>
          </div>
        </div>

        <div v-else-if="tab !== 'poster'" class="section-block ai-fields">
          <div v-if="tab === 'template'" class="upload-group">
            <div class="section-title"><span>模板与原图</span><small>原图作为完整底图，image2 只迁移模板的文字版式和上层装饰</small></div>
            <div class="template-upload-pair">
              <article class="reference-upload-card">
                <header><strong>① 模板图</strong><span>提供文字、图案与风格</span></header>
                <label
                  v-if="!templateAsset"
                  class="upload-box compact-upload"
                  :class="{ 'is-dragging': draggingRole === 'template' }"
                  @dragover="onDragOver('template', $event)"
                  @dragleave="onDragLeave('template', $event)"
                  @drop="onDrop('template', $event)"
                >
                  <ImagePlus :size="22" /><strong>拖拽模板图到这里</strong><span>或点击上传</span><input type="file" accept="image/*" :disabled="store.isRunning" @change="uploadSingle($event, 'template')" />
                </label>
                <div v-else class="reference-preview" :style="{ aspectRatio: form.size === '1080x1080' ? '1 / 1' : '3 / 4' }">
                  <img :src="templateAsset.previewUrl" alt="模板图" />
                  <span>样式参考</span>
                  <button :disabled="store.isRunning" aria-label="移除模板图" @click="removeAsset(templateAsset, 'template')"><X :size="16" /></button>
                </div>
              </article>
              <article class="reference-upload-card">
                <header><strong>② 原图</strong><span>作为最终完整底图与主体</span></header>
                <label
                  v-if="!templateSourceAsset"
                  class="upload-box compact-upload"
                  :class="{ 'is-dragging': draggingRole === 'source' }"
                  @dragover="onDragOver('source', $event)"
                  @dragleave="onDragLeave('source', $event)"
                  @drop="onDrop('source', $event)"
                >
                  <ImagePlus :size="22" /><strong>拖拽原图到这里</strong><span>或点击上传</span><input type="file" accept="image/*" :disabled="store.isRunning" @change="uploadSource" />
                </label>
                <div v-else class="reference-preview" :style="{ aspectRatio: form.size === '1080x1080' ? '1 / 1' : '3 / 4' }">
                  <img :src="templateSourceAsset.previewUrl" alt="原图" />
                  <span>唯一底图</span>
                  <button :disabled="store.isRunning" aria-label="移除原图" @click="removeAsset(templateSourceAsset, 'template-source')"><X :size="16" /></button>
                </div>
              </article>
            </div>
            <p v-if="templateAspectWarning" class="template-aspect-warning">{{ templateAspectWarning }}</p>
            <div class="template-transfer-note"><strong>复刻规则</strong><span>原图铺满画布 → 迁移模板文字/贴纸/图案 → 依据内容资产替换文案</span></div>
            <div class="template-copy-planner">
              <div class="planner-head">
                <span>文案槽位预览</span>
                <button class="secondary-button" type="button" :disabled="templatePlanLoading || !templateAsset || !templateSourceAsset" @click="analyzeTemplatePlan">
                  <Sparkles :size="15" />{{ templatePlanLoading ? '正在分析' : (templatePlan ? '重新分析' : '分析模板与文案') }}
                </button>
              </div>
              <p v-if="!templatePlan">上传模板图与原图后分析，系统会按模板槽位概括内容资产；生成前可微调每段文字。</p>
              <div v-for="slot in templateCopySlots" :key="slot.slot_id" class="copy-slot-row">
                <label><span>{{ slot.role }} · 最多 {{ slot.max_chars }} 字 / {{ slot.max_lines }} 行</span><input v-model="slot.text" :maxlength="slot.max_chars" /></label>
                <div class="slot-adjustments">
                  <label><span>水平 {{ slot.xOffsetPercent || 0 }}%</span><input v-model.number="slot.xOffsetPercent" type="range" min="-2" max="2" step="0.5" /></label>
                  <label><span>垂直 {{ slot.yOffsetPercent || 0 }}%</span><input v-model.number="slot.yOffsetPercent" type="range" min="-2" max="2" step="0.5" /></label>
                  <label><span>字号 {{ Math.round((slot.fontScale || 1) * 100) }}%</span><input v-model.number="slot.fontScale" type="range" min="0.85" max="1.15" step="0.05" /></label>
                </div>
              </div>
            </div>
          </div>
          <label class="wide-field"><span>封面标题 <small>推荐填写；留空时使用关联内容资产标题</small></span><input v-model="form.title" maxlength="60" placeholder="例如：内容生产新方式" /></label>
          <label class="wide-field"><span>生成要求 <small v-if="tab === 'template'">模板复刻只填写需要适配的内容差异</small></span><textarea v-model="form.prompt" rows="5" maxlength="8000" :placeholder="tab === 'template' ? '例如：只替换模板底图为原图，保留模板所有装饰、字体和版式；将文字适配为关联内容资产的标题与卖点。' : '例如：轻复古生活方式，主体清晰，暖色自然光，左上留出呼吸感；关联内容任务后会自动拼入正文摘要与话题。'" /></label>
          <label class="wide-field"><span>不希望出现</span><input v-model="form.negativePrompt" maxlength="4000" placeholder="例如：低清晰度、复杂水印、变形人物" /></label>
          <div class="form-grid compact">
            <label><span>生成数量</span><select v-model.number="form.count"><option :value="1">1 张</option><option :value="2">2 张</option><option :value="4">4 张</option></select></label>
            <label
              v-if="tab === 'ai'"
              class="mask-field mask-dropzone"
              :class="{ 'is-dragging': draggingRole === 'mask' }"
              @dragover="onDragOver('mask', $event)"
              @dragleave="onDragLeave('mask', $event)"
              @drop="onDrop('mask', $event)"
            >
              <span>可选蒙版</span><strong>拖拽蒙版图或点击上传</strong><small v-if="maskAsset">已上传 {{ maskAsset.localName }}；尺寸需与单张原图一致</small><input type="file" accept="image/*" :disabled="store.isRunning" @change="uploadSingle($event, 'mask')" />
            </label>
          </div>
        </div>

        <PosterBillboardPanel
          v-if="tab === 'poster'"
          :content-task-id="form.contentTaskId"
          :image2-ready="image2Ready"
        />

        <div v-if="!['template', 'poster'].includes(tab)" class="section-block">
          <div class="section-title">
            <span>图片素材</span>
            <small v-if="tab === 'compose'">当前 {{ sourceAssets.length }} 张 · {{ activeTemplate?.name }}需要 {{ activeTemplate?.min_assets }}–{{ activeTemplate?.max_assets }} 张</small>
            <small v-else>支持单图图生图与多图参考 · 当前 {{ sourceAssets.length }}/9 张</small>
          </div>
          <div class="asset-grid">
            <div v-for="asset in sourceAssets" :key="asset.id" class="asset-card">
              <img :src="asset.previewUrl" :alt="asset.localName" />
              <button :disabled="store.isRunning" aria-label="移除图片" @click="removeAsset(asset)"><X :size="15" /></button>
            </div>
            <label
              class="upload-box"
              :class="{ 'is-dragging': draggingRole === 'source' }"
              @dragover="onDragOver('source', $event)"
              @dragleave="onDragLeave('source', $event)"
              @drop="onDrop('source', $event)"
            >
              <ImagePlus :size="24" /><strong>拖拽图片到这里</strong><span>或点击上传 · PNG / JPG / WebP，单张不超过 20 MB</span>
              <input type="file" accept="image/*" multiple :disabled="store.isRunning" @change="uploadSource" />
            </label>
          </div>
        </div>

        <div v-if="tab !== 'poster'" class="submit-row">
          <p v-if="tab !== 'compose' && !image2Ready">请点击右上角配置可用的 image2 中转站。</p>
          <button class="primary-button" :disabled="!canSubmit" @click="submit">
            <WandSparkles :size="18" />{{ store.loading.submit ? '正在提交…' : '开始生成' }}
          </button>
        </div>
      </section>

      <aside class="result-panel">
        <div class="result-head"><div><span>生成结果</span><small v-if="store.currentJob">{{ statusText }}</small></div><button v-if="canCancel" class="text-button" @click="store.cancel">取消任务</button></div>
        <div v-if="store.currentJob && store.isRunning" class="progress-card">
          <Sparkles :size="28" /><strong>{{ statusText }}</strong><span>{{ store.currentJob.progress || 0 }}%</span>
          <div class="progress-track"><i :style="{ width: `${store.currentJob.progress || 0}%` }" /></div>
        </div>
        <div v-else-if="store.resultUrls.length" class="result-list">
          <article v-for="(item, index) in store.resultUrls" :key="item.id">
            <img :src="item.url" :alt="`封面结果 ${index + 1}`" />
            <div class="result-actions">
              <button @click="downloadResult(item, index)"><Download :size="16" />下载 PNG</button>
              <button v-if="store.currentJob?.artifact_id || store.currentJob?.content_task_id" @click="setCurrent(item.id)">设为当前封面</button>
            </div>
          </article>
        </div>
        <div v-else-if="store.currentJob?.status === 'failed'" class="empty-result error-state">
          <strong>{{ store.currentJob.error_message || '生成失败' }}</strong>
          <button class="secondary-button" @click="retryJob(store.currentJob)"><RefreshCw :size="16" />重新生成</button>
        </div>
        <div v-else class="empty-result"><ImagePlus :size="36" /><strong>结果会显示在这里</strong><span>先上传素材，再选择排版或 AI 生成方式。</span></div>

        <div class="history">
          <div class="result-head"><span>最近任务</span><button class="text-button" @click="store.loadHistory(form.contentTaskId || null)">刷新</button></div>
          <button v-for="job in store.jobs" :key="job.id" class="history-row" @click="selectHistory(job)">
            <span><strong>{{ jobKindLabel(job) }}</strong><small>{{ new Date(job.created_at).toLocaleString() }}</small></span>
            <em :data-status="job.status">{{ job.status }}</em>
          </button>
          <p v-if="!store.jobs.length" class="history-empty">还没有封面任务</p>
        </div>
      </aside>
    </div>

    <a-modal
      v-model:open="image2ConfigOpen"
      title="image2 全局配置"
      :confirm-loading="image2ConfigSaving"
      :ok-button-props="{ disabled: !canManageImage2 }"
      ok-text="保存全局配置"
      cancel-text="取消"
      @ok="saveImage2Config"
    >
      <div class="image2-config-modal">
        <p>配置保存后对当前账号的所有封面 image2 任务持续生效，环境变量仅作为未配置时的回退。</p>
        <label>
          <span>中转站 Base URL</span>
          <input v-model.trim="image2ConfigForm.baseUrl" type="url" maxlength="500" autocomplete="off" placeholder="例如：https://relay.example.com/v1" :disabled="!canManageImage2" />
        </label>
        <label>
          <span>API Key</span>
          <input v-model="image2ConfigForm.apiKey" type="password" maxlength="500" autocomplete="new-password" :placeholder="store.bootstrap?.image2?.api_key_configured ? '已配置，留空则保留原密钥' : '请输入中转站 API Key'" :disabled="!canManageImage2" />
        </label>
        <label>
          <span>模型</span>
          <input v-model.trim="image2ConfigForm.model" type="text" maxlength="255" autocomplete="off" placeholder="gpt-image-2" :disabled="!canManageImage2" />
        </label>
        <div class="quality-locks"><span>质量：最高</span><span>底图：锁定合成</span><span>格式：PNG</span></div>
        <div class="image2-test-row">
          <button class="secondary-button" type="button" :disabled="image2ConfigTesting || image2ConfigSaving" @click="testImage2Config">
            <RefreshCw :size="15" />{{ image2ConfigTesting ? '正在验证' : '测试连接与模型' }}
          </button>
          <small v-if="store.bootstrap?.image2?.capabilities?.message">{{ store.bootstrap.image2.capabilities.message }}</small>
        </div>
        <small v-if="canManageImage2">API Key 保存后不会在页面或接口中回显。</small>
      </div>
    </a-modal>
  </main>
</template>

<style scoped lang="less">
.cover-page { min-height: 100%; padding: 32px; color: var(--gray-1000); background: var(--main-20); }
.page-head { max-width: 1500px; margin: 0 auto 24px; display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; }
.eyebrow { margin: 0 0 6px; color: var(--main-700); font-size: 11px; font-weight: 700; letter-spacing: .16em; }
h1 { margin: 0; font-size: 30px; letter-spacing: -.04em; } .page-head p:last-child { margin: 8px 0 0; color: var(--gray-600); }
.image2-state { border: 1px solid var(--color-warning-100); border-radius: 999px; padding: 7px 12px; display: inline-flex; gap: 6px; align-items: center; color: var(--color-warning-700); background: var(--color-warning-10); font-size: 12px; cursor: pointer; }
.image2-state.ready { color: var(--color-success-700); border-color: var(--color-success-100); background: var(--color-success-10); }
.workspace-grid { max-width: 1500px; margin: auto; display: grid; grid-template-columns: minmax(0, 1fr) 390px; gap: 20px; align-items: start; }
.editor-panel, .result-panel { border: 1px solid var(--gray-200); border-radius: 18px; background: var(--main-0); box-shadow: 0 10px 34px rgba(1, 21, 31, .06); }
.editor-panel { padding: 22px; } .result-panel { padding: 20px; position: sticky; top: 20px; }
.mode-tabs { display: grid; grid-template-columns: repeat(4, 1fr); padding: 4px; border-radius: 12px; background: var(--gray-100); }
.mode-tabs button { border: 0; border-radius: 9px; padding: 10px; color: var(--gray-600); background: transparent; cursor: pointer; }
.mode-tabs button.active { color: var(--main-900); background: white; box-shadow: 0 2px 8px rgba(0, 0, 0, .08); font-weight: 600; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 20px; } .form-grid.compact { margin-top: 16px; }
label { display: grid; gap: 7px; font-size: 13px; color: var(--gray-700); } input, select, textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--gray-300); border-radius: 9px; padding: 10px 11px; color: var(--gray-1000); background: white; outline: none; } textarea { resize: vertical; line-height: 1.6; }
input:focus, select:focus, textarea:focus { border-color: var(--main-500); box-shadow: 0 0 0 3px var(--main-50); }
.section-block { margin-top: 24px; padding-top: 22px; border-top: 1px solid var(--gray-150); } .section-title, .result-head { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
.section-title > span, .result-head > span, .result-head > div > span { font-weight: 700; } small { color: var(--gray-500); font-style: normal; }
.template-grid { display: grid; grid-template-columns: repeat(6, minmax(88px, 1fr)); gap: 10px; margin-top: 13px; }
.template-card { padding: 11px; border: 1px solid var(--gray-200); border-radius: 11px; background: white; display: grid; gap: 5px; justify-items: start; cursor: pointer; }
.template-card.active { border-color: var(--main-600); background: var(--main-30); box-shadow: 0 0 0 2px var(--main-100); }
.layout-glyph { width: 100%; aspect-ratio: 4/3; display: grid; grid-template-columns: 1fr 1fr; gap: 3px; } .layout-glyph i { background: var(--main-200); border-radius: 2px; }
.layout-glyph[data-layout='split_horizontal'] { grid-template-columns: 1fr; }.layout-glyph[data-layout='grid_3x3'] { grid-template-columns: repeat(3, 1fr); }
.wide-field { margin-top: 15px; }.asset-grid { margin-top: 13px; display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px; }
.asset-card, .single-preview { position: relative; overflow: hidden; border-radius: 11px; background: var(--gray-100); }.asset-card { aspect-ratio: 1; }.asset-card img, .single-preview img { width: 100%; height: 100%; display: block; object-fit: cover; }.single-preview.template-preview img { object-fit: contain; }
.asset-card button, .single-preview button { position: absolute; top: 7px; right: 7px; width: 27px; height: 27px; border: 0; border-radius: 50%; display: grid; place-items: center; color: white; background: rgba(0,0,0,.65); cursor: pointer; }
.template-upload-pair { margin-top: 13px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.reference-upload-card { min-width: 0; padding: 14px; border: 1px solid var(--gray-150); border-radius: 13px; background: var(--main-20); }
.reference-upload-card header { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
.reference-upload-card header strong { color: var(--gray-900); font-size: 13px; }.reference-upload-card header span { color: var(--gray-500); font-size: 11px; }
.reference-preview { position: relative; width: min(100%, 280px); max-height: 360px; margin: 12px auto 0; overflow: hidden; border: 1px solid var(--gray-200); border-radius: 11px; background: var(--gray-100); }
.reference-preview > img { width: 100%; height: 100%; display: block; object-fit: cover; }
.reference-preview > span { position: absolute; left: 8px; bottom: 8px; padding: 4px 7px; border-radius: 6px; color: white; background: rgba(0,0,0,.66); font-size: 10px; }
.reference-preview > button { position: absolute; top: 8px; right: 8px; width: 28px; height: 28px; border: 0; border-radius: 50%; display: grid; place-items: center; color: white; background: rgba(0,0,0,.68); cursor: pointer; }
.template-aspect-warning { margin: 10px 0 0; padding: 9px 11px; border-radius: 8px; color: var(--color-warning-700); background: var(--color-warning-10); font-size: 12px; }
.template-transfer-note { margin-top: 11px; padding: 11px 13px; display: flex; gap: 12px; align-items: center; border: 1px solid var(--main-100); border-radius: 10px; color: var(--main-800); background: var(--main-30); font-size: 12px; }.template-transfer-note span { color: var(--gray-600); }
.template-copy-planner { margin-top: 12px; padding: 13px; border: 1px solid var(--gray-150); border-radius: 11px; background: var(--main-0); }.planner-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; }.planner-head > span { font-weight: 700; }.template-copy-planner > p { margin: 10px 0 0; color: var(--gray-500); font-size: 12px; }.copy-slot-row { margin-top: 11px; padding-top: 11px; border-top: 1px solid var(--gray-100); }.copy-slot-row > label > span { font-size: 11px; color: var(--gray-500); }.slot-adjustments { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 8px; }.slot-adjustments label { display: grid; gap: 3px; }.slot-adjustments span { color: var(--gray-500); font-size: 10px; }.slot-adjustments input { padding: 0; }
.quality-locks { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }.quality-locks span { padding: 4px 8px; border-radius: 999px; color: var(--color-success-700); background: var(--color-success-10); font-size: 11px; }
.image2-config-modal { display: grid; gap: 15px; }.image2-config-modal > p { margin: 0; color: var(--gray-600); line-height: 1.6; }.image2-config-modal > small { color: var(--gray-500); }
.image2-test-row { display: flex; gap: 10px; align-items: center; }.image2-test-row small { color: var(--gray-500); }
.upload-box { min-height: 130px; border: 1px dashed var(--main-300); border-radius: 11px; display: grid; place-content: center; justify-items: center; gap: 7px; text-align: center; color: var(--main-700); background: var(--main-30); cursor: pointer; transition: border-color .15s, background .15s, box-shadow .15s; }.upload-box span { color: var(--gray-500); font-size: 11px; }.upload-box input { display: none; }.upload-box.is-dragging { border-color: var(--main-600); background: var(--main-50); box-shadow: 0 0 0 3px var(--main-100); }
.compact-upload { min-height: 86px; margin-top: 12px; }.single-preview { width: 150px; height: 100px; margin-top: 12px; }.mask-field input { padding: 7px; }.mask-field small { display: block; }.mask-dropzone { min-height: 86px; padding: 12px; box-sizing: border-box; border: 1px dashed var(--main-300); border-radius: 11px; color: var(--main-700); background: var(--main-30); cursor: pointer; text-align: center; }.mask-dropzone input { display: none; }
.compose-preview { display: grid; justify-items: center; gap: 8px; margin-top: 18px; padding: 16px; border: 1px solid var(--gray-150); border-radius: 12px; background: var(--main-20); }
.compose-preview > small { color: var(--gray-500); text-align: center; }
.preview-stage { --preview-gap: 5px; position: relative; display: grid; width: min(100%, 270px); aspect-ratio: 3 / 4; gap: var(--preview-gap); overflow: hidden; border-radius: 10px; padding: 7px; background: var(--main-0); box-shadow: 0 5px 18px rgba(1, 21, 31, .1); }
.preview-stage img { width: 100%; height: 100%; min-width: 0; min-height: 0; object-fit: cover; overflow: hidden; border-radius: 4px; }
.preview-stage > span { place-self: center; align-self: center; color: var(--gray-500); font-size: 12px; text-align: center; }
.preview-stage > strong { position: absolute; z-index: 3; left: 14px; top: 14px; max-width: calc(100% - 28px); padding: 5px 8px; overflow: hidden; border-radius: 5px; color: var(--gray-1000); background: rgba(255, 255, 255, .9); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.preview-stage[data-layout='grid_3x3'] > strong, .preview-stage[data-layout='before_after'] > strong { top: auto; bottom: 14px; }
.preview-stage[data-layout='grid_3x3'] { grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(3, 1fr); }
.preview-stage[data-layout='split_vertical'], .preview-stage[data-layout='before_after'] { grid-template-columns: repeat(2, 1fr); }
.preview-stage[data-layout='split_horizontal'] { grid-template-rows: repeat(2, 1fr); }
.preview-stage[data-layout='card_stack'] { display: block; background: var(--gray-1000); }
.preview-stage[data-layout='card_stack'] img:first-of-type { position: absolute; inset: 7px; width: calc(100% - 14px); height: calc(100% - 14px); opacity: .72; }
.preview-stage[data-layout='card_stack'] img:not(:first-of-type) { position: relative; z-index: 2; width: 70%; height: 25%; margin: 7px 0 0 27%; border: 2px solid var(--main-0); }
.preview-stage[data-layout='hero_thumbs'] { grid-template-columns: repeat(4, 1fr); grid-template-rows: 3fr 1fr; }
.preview-stage[data-layout='hero_thumbs'] img:first-of-type { grid-column: 1 / -1; }
.submit-row { margin-top: 24px; display: flex; justify-content: flex-end; gap: 16px; align-items: center; }.submit-row p { margin: 0 auto 0 0; color: var(--color-warning-700); font-size: 12px; }
.primary-button, .secondary-button, .result-list article button { border: 0; border-radius: 10px; display: inline-flex; gap: 7px; align-items: center; justify-content: center; cursor: pointer; }
.primary-button { padding: 11px 20px; color: white; background: var(--main-700); font-weight: 650; }.primary-button:disabled { opacity: .4; cursor: not-allowed; }.secondary-button { padding: 9px 13px; color: var(--main-800); background: var(--main-50); }
.result-head small { display: block; margin-top: 2px; }.text-button { border: 0; padding: 4px; color: var(--main-700); background: transparent; cursor: pointer; }
.progress-card, .empty-result { min-height: 250px; margin-top: 16px; border-radius: 13px; display: grid; place-content: center; justify-items: center; gap: 10px; color: var(--gray-500); background: var(--gray-50); text-align: center; }.progress-card strong, .empty-result strong { color: var(--gray-800); }
.progress-track { width: 220px; height: 7px; overflow: hidden; border-radius: 999px; background: var(--gray-200); }.progress-track i { display: block; height: 100%; border-radius: inherit; background: var(--main-600); transition: width .3s; }
.result-list { display: grid; gap: 13px; margin-top: 16px; }.result-list article { overflow: hidden; border-radius: 13px; background: var(--gray-100); }.result-list img { width: 100%; max-height: 520px; object-fit: contain; display: block; }.result-actions { padding: 10px; display: flex; justify-content: flex-end; gap: 7px; border-top: 1px solid var(--gray-150); background: var(--main-0); }.result-list article button { padding: 8px 11px; background: var(--gray-50); color: var(--main-900); }
.error-state strong { color: var(--color-error-700); }.history { margin-top: 25px; padding-top: 20px; border-top: 1px solid var(--gray-150); }.history-row { width: 100%; padding: 11px 0; border: 0; border-bottom: 1px solid var(--gray-100); display: flex; justify-content: space-between; align-items: center; text-align: left; background: transparent; cursor: pointer; }.history-row span { display: grid; gap: 3px; }.history-row em { color: var(--gray-500); font-size: 11px; font-style: normal; }.history-row em[data-status='succeeded'] { color: var(--color-success-700); }.history-row em[data-status='failed'] { color: var(--color-error-700); }.history-empty { color: var(--gray-500); font-size: 12px; }
@media (max-width: 1100px) { .workspace-grid { grid-template-columns: 1fr; }.result-panel { position: static; }.template-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 680px) { .cover-page { padding: 18px; }.page-head { display: grid; }.form-grid, .asset-grid, .template-upload-pair, .slot-adjustments { grid-template-columns: 1fr; }.template-grid { grid-template-columns: repeat(2, 1fr); }.template-transfer-note { align-items: flex-start; flex-direction: column; } }
</style>
