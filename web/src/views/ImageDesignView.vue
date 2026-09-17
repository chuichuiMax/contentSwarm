<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  Check,
  Download,
  ImagePlus,
  LoaderCircle,
  RefreshCw,
  Sparkles,
  Trash2,
  Upload,
  WandSparkles
} from 'lucide-vue-next'

import MaterialImagePickerModal from '@/components/material/MaterialImagePickerModal.vue'
import { materialLibraryApi } from '@/apis/material_library_api'
import { imageDesignApi } from '@/apis/image_design_api'
import { useImageDesignStore } from '@/stores/imageDesign'

const store = useImageDesignStore()
const workflow = ref('style_transfer')
const activeTab = ref('materials')
const pickerOpen = ref(false)
const pickerRole = ref('reference')
const uploadInput = ref(null)
const galleries = ref([])
const activeGallery = ref('')
const galleryItems = ref([])
const galleryUrls = ref({})
const resultUrls = ref({})
const showcaseItems = ref([])
const showcaseUrls = ref({})
const resultDetail = ref(null)
const detailReferenceUrl = ref('')
const detailRawRoomUrl = ref('')
const loadingGallery = ref(false)
const refinedPromptDraft = ref('')
const refinedPromptDirty = ref(false)
const polling = new Map()

const commonForm = reactive({
  prompt: '',
  aspectRatio: '3:4',
  genCount: 1,
  clarity: '1K',
  refining: false
})
const styleTransferForm = reactive({
  reference: null,
  styleLabel: '现代轻奢',
  usePromptAsStyle: false
})
const roomAdaptForm = reactive({
  styleReference: null,
  rawStructure: null
})
const crossSpaceForm = reactive({
  styleReference: null,
  targetSpace: 'living_room',
  targetSpaceLabel: '客厅',
  spaceLayout: 'sofa_wall',
  spaceLayoutDesc: '一字型沙发靠墙',
  spaceAddons: []
})

const styles = ['现代轻奢', '意式极简', '新中式', '现代法式', '极简奶油风', '现代简约', '侘寂风', '南洋复古风', '美式现代', '日式极简禅风']
const jobStatusLabels = {
  queued: '等待执行',
  running: '正在生成',
  polling: '正在生成',
  succeeded: '生成完成',
  failed: '生成失败',
  cancelled: '已取消'
}
const spaces = [
  ['living_room', '客厅'], ['dining_room', '餐厅'], ['kitchen', '厨房'], ['master_bedroom', '主卧'],
  ['children_room', '次卧/儿童房'], ['study', '书房'], ['master_bathroom', '主卫'], ['bathroom', '公卫'],
  ['balcony', '阳台'], ['entrance', '玄关'], ['cloakroom', '衣帽间'], ['tea_room', '茶室'],
  ['audio_visual_room', '影音室'], ['wine_cellar', '酒窖'], ['gym', '健身房'], ['elder_room', '长辈房'], ['guest_room', '客房']
]
const layouts = [
  ['sofa_wall', '一字型沙发靠墙'], ['l_sofa', 'L型沙发+单人椅'], ['face_to_face', '沙发对坐式'], ['free_layout', '无主沙发自由布局']
]
const addons = [
  ['lounge_chair', '落地窗旁休闲躺椅'], ['bar_table', '沙发后长条书桌/吧台'], ['storage_wall', '电视墙满墙收纳柜'],
  ['shelf', '开放式层板展示架'], ['rug_zone', '地毯划分沙发区'], ['fireplace', '壁炉居中']
]
const spaceConfigs = {
  living_room: { layouts, addons },
  dining_room: { layouts: [['round_table', '圆桌居中'], ['island_table', '餐岛一体'], ['long_table', '长桌靠墙']], addons: [['sideboard', '餐边柜'], ['pendant_lights', '组合吊灯'], ['display_cabinet', '餐具展示柜']] },
  kitchen: { layouts: [['island', '中岛操作台'], ['l_shape', 'L型橱柜'], ['u_shape', 'U型橱柜']], addons: [['high_cabinet', '高柜电器区'], ['breakfast_bar', '早餐吧台'], ['open_shelf', '开放层板']] },
  master_bedroom: { layouts: [['bed_center', '床居中对称'], ['bed_window', '床靠窗'], ['bed_wall', '床靠墙留过道']], addons: [['bedside_bench', '床尾凳'], ['vanity', '梳妆台'], ['tv_wall', '电视背景墙']] },
  children_room: { layouts: [['bed_desk', '床铺+书桌'], ['bunk', '上下床'], ['bed_play', '床铺+活动区']], addons: [['study_shelf', '学习收纳墙'], ['play_corner', '玩耍角'], ['blackboard', '黑板墙']] },
  study: { layouts: [['desk_window', '书桌靠窗'], ['desk_wall', '书桌靠墙'], ['dual_desk', '双人书桌']], addons: [['bookcase', '整墙书柜'], ['reading_chair', '阅读单椅'], ['printer_cabinet', '打印收纳柜']] },
  master_bathroom: { layouts: [['wet_dry', '干湿分离'], ['double_vanity', '双台盆对称'], ['bathtub_window', '浴缸靠窗']], addons: [['bathtub', '独立浴缸'], ['shower', '淋浴间'], ['smart_toilet', '智能马桶']] },
  bathroom: { layouts: [['compact', '紧凑一字型'], ['wet_dry', '干湿分离'], ['vanity_wall', '台盆靠墙']], addons: [['mirror_cabinet', '镜柜'], ['shower', '淋浴间'], ['laundry', '洗烘组合']] },
  balcony: { layouts: [['lounge', '休闲区'], ['laundry', '洗衣区'], ['greenhouse', '阳台花园']], addons: [['laundry_cabinet', '洗衣收纳柜'], ['plants', '绿植角'], ['coffee_table', '小茶几']] },
  entrance: { layouts: [['one_wall', '一字玄关柜'], ['l_shape', 'L型转角柜'], ['open_entry', '开放式玄关']], addons: [['bench', '换鞋凳'], ['full_height', '通顶收纳'], ['mirror', '穿衣镜']] },
  cloakroom: { layouts: [['u_storage', 'U型衣柜'], ['island_storage', '衣帽岛台'], ['parallel', '平行衣柜']], addons: [['glass_door', '玻璃柜门'], ['island', '中岛抽屉'], ['jewelry', '首饰收纳']] },
  tea_room: { layouts: [['tea_table_center', '茶桌居中'], ['tea_wall', '茶桌靠墙'], ['floor_seating', '地台茶席']], addons: [['tea_cabinet', '茶具柜'], ['landscape', '山水挂画'], ['screen', '屏风隔断']] },
  audio_visual_room: { layouts: [['screen_center', '幕布居中'], ['sofa_screen', '沙发对屏'], ['immersive', '沉浸式环绕']], addons: [['projector', '投影设备'], ['acoustic_wall', '吸音墙面'], ['recliner', '影音躺椅']] },
  wine_cellar: { layouts: [['cellar_wall', '酒柜靠墙'], ['island_cellar', '酒柜+中岛'], ['tasting_table', '品酒桌居中']], addons: [['wine_rack', '红酒陈列架'], ['tasting_bar', '品酒吧台'], ['glass_cabinet', '玻璃酒柜']] },
  gym: { layouts: [['mirror_wall', '镜墙训练区'], ['equipment_wall', '器械靠墙'], ['free_training', '自由训练区']], addons: [['treadmill', '跑步机'], ['yoga', '瑜伽垫区'], ['dumbbell', '哑铃架']] },
  elder_room: { layouts: [['bed_side', '床靠墙留宽过道'], ['bed_center', '床居中'], ['bed_lounge', '床+休闲椅']], addons: [['grab_bar', '安全扶手'], ['reading_light', '床头阅读灯'], ['storage', '低位收纳柜']] },
  guest_room: { layouts: [['bed_center', '床居中对称'], ['bed_desk', '床铺+书桌'], ['sofa_bed', '沙发床组合']], addons: [['luggage', '行李收纳位'], ['wardrobe', '衣柜'], ['reading_chair', '阅读单椅']] }
}
const activeReference = computed(() => {
  if (workflow.value === 'style_transfer') return styleTransferForm.reference
  if (workflow.value === 'room_adapt') return roomAdaptForm.styleReference
  return crossSpaceForm.styleReference
})
const activeReferenceRole = computed(() => {
  if (workflow.value === 'style_transfer') return 'structure_source'
  if (workflow.value === 'room_adapt') return 'style_reference'
  return 'cross_space_style'
})
const styleOptions = computed(() => store.bootstrap?.profiles?.styles?.map((item) => item.label) || styles)
const spaceOptions = computed(() => store.bootstrap?.profiles?.spaces?.map((item) => [item.id, item.label]) || spaces)
const activeSpaceProfile = computed(() => store.bootstrap?.profiles?.spaces?.find((item) => item.id === crossSpaceForm.targetSpace))
const currentLayouts = computed(() => activeSpaceProfile.value?.layouts?.map((item) => [item.id, item.label]) || spaceConfigs[crossSpaceForm.targetSpace]?.layouts || layouts)
const currentAddons = computed(() => activeSpaceProfile.value?.addons?.map((item) => [item.id, item.label]) || spaceConfigs[crossSpaceForm.targetSpace]?.addons || addons)
const analysisKey = (materialId, role) => `${materialId}:${role}`
const materialAnalysis = (material, role) => material ? store.analyses[analysisKey(material.id, role)] : null
const compiledPromptPreview = computed(() => {
  const refinement = store.activeRefinement
  if (!refinement) return ''
  return refinement.compiled_prompts?.[commonForm.aspectRatio] || refinement.compiled_prompt || ''
})

watch(
  () => store.activeRefinement?.id,
  () => {
    refinedPromptDraft.value = store.activeRefinement?.compiled_prompt || ''
    refinedPromptDirty.value = false
  }
)

const refineIssues = computed(() => {
  const issues = []
  if (!activeReference.value) issues.push('请先选择参考图')
  if (activeReference.value && materialAnalysis(activeReference.value, activeReferenceRole.value)?.status !== 'completed') issues.push('请等待参考图视觉分析完成')
  if (workflow.value === 'room_adapt' && !roomAdaptForm.rawStructure) issues.push('请先选择毛坯实拍图')
  if (roomAdaptForm.rawStructure && workflow.value === 'room_adapt' && materialAnalysis(roomAdaptForm.rawStructure, 'structure_source')?.status !== 'completed') issues.push('请等待毛坯图视觉分析完成')
  if (!commonForm.prompt.trim()) issues.push('请填写补充描述')
  return issues
})

const generateIssues = computed(() => {
  const issues = [...refineIssues.value]
  if (workflow.value === 'style_transfer' && !styleTransferForm.styleLabel && !styleTransferForm.usePromptAsStyle) issues.push('请选择换装风格')
  if (workflow.value === 'cross_space' && (!crossSpaceForm.targetSpace || !crossSpaceForm.spaceLayout)) issues.push('请选择目标空间和布局')
  if (!store.activeRefinement) issues.push('请先完成 AI 深度润色')
  if (store.activeRefinement && refinedPromptDirty.value) issues.push('优化结果已修改，请先重新校验')
  if (!store.image2Ready) issues.push('请先配置并验证 image2 中转站')
  return issues
})

const canSubmit = computed(() => generateIssues.value.length === 0)

function invalidateRefinement() {
  store.invalidateRefinement()
  refinedPromptDraft.value = ''
  refinedPromptDirty.value = false
}

function switchWorkflow(next) {
  workflow.value = next
  commonForm.prompt = ''
  invalidateRefinement()
}

function chooseSpace([value, label]) {
  invalidateRefinement()
  crossSpaceForm.targetSpace = value
  crossSpaceForm.targetSpaceLabel = label
  const firstLayout = currentLayouts.value[0]
  crossSpaceForm.spaceLayout = firstLayout?.[0] || ''
  crossSpaceForm.spaceLayoutDesc = firstLayout?.[1] || ''
  crossSpaceForm.spaceAddons = []
}

function chooseLayout([value, label]) {
  invalidateRefinement()
  crossSpaceForm.spaceLayout = value
  crossSpaceForm.spaceLayoutDesc = label
}

function chooseStyle(style) {
  styleTransferForm.styleLabel = style
  styleTransferForm.usePromptAsStyle = false
  invalidateRefinement()
}

function usePromptForStyle() {
  styleTransferForm.usePromptAsStyle = true
  styleTransferForm.styleLabel = ''
  invalidateRefinement()
}

function toggleAddon(value) {
  crossSpaceForm.spaceAddons = crossSpaceForm.spaceAddons.includes(value)
    ? crossSpaceForm.spaceAddons.filter((item) => item !== value)
    : [...crossSpaceForm.spaceAddons, value]
  invalidateRefinement()
}

function clearPrompt() {
  commonForm.prompt = ''
  invalidateRefinement()
}

function openPicker(role) {
  pickerRole.value = role
  pickerOpen.value = true
}

async function setMaterial(role, item) {
  const previous = role === 'reference' ? activeReference.value : roomAdaptForm.rawStructure
  if (previous?.url) URL.revokeObjectURL(previous.url)
  try {
    const response = await materialLibraryApi.getItemFile(item.id)
    const url = URL.createObjectURL(await response.blob())
    const value = { id: item.id, name: item.name, url, width: item.width, height: item.height }
    if (role === 'reference') {
      if (workflow.value === 'style_transfer') styleTransferForm.reference = value
      else if (workflow.value === 'room_adapt') roomAdaptForm.styleReference = value
      else crossSpaceForm.styleReference = value
    } else roomAdaptForm.rawStructure = value
    invalidateRefinement()
    const analysisRole = role === 'reference' ? activeReferenceRole.value : 'structure_source'
    store.analyze(item.id, analysisRole).catch((error) => {
      message.error(error.message || '图片视觉分析失败')
    })
    pickerOpen.value = false
    await refreshGallery()
    return true
  } catch (error) {
    message.error(error.message || '素材读取失败')
    return false
  }
}

async function retryAnalysis(material, role) {
  if (!material) return
  try {
    await store.analyze(material.id, role)
    message.success('图片视觉分析已完成')
  } catch (error) {
    message.error(error.message || '图片视觉分析失败')
  }
}

async function loadGalleries() {
  const response = await materialLibraryApi.listGalleries()
  galleries.value = response.galleries || []
  activeGallery.value = galleries.value.find((item) => (item.count || item.direct_count || 0) > 0)?.id || galleries.value[0]?.id || ''
  await refreshGallery()
}

async function refreshGallery() {
  Object.values(galleryUrls.value).forEach((url) => URL.revokeObjectURL(url))
  galleryUrls.value = {}
  if (!activeGallery.value) return
  loadingGallery.value = true
  try {
    const response = await materialLibraryApi.listItems({ material_type: 'image', category: activeGallery.value, status: 'enabled', page: 1, page_size: 24, sort: 'newest' })
    galleryItems.value = response.items || []
    const urls = {}
    await Promise.all(galleryItems.value.map(async (item) => {
      try {
        const file = await materialLibraryApi.getItemThumbnail(item.id)
        urls[item.id] = URL.createObjectURL(await file.blob())
      } catch {
        urls[item.id] = ''
      }
    }))
    galleryUrls.value = urls
  } finally {
    loadingGallery.value = false
  }
}

async function loadShowcase() {
  const response = await imageDesignApi.listShowcase()
  showcaseItems.value = response.items || []
  Object.values(showcaseUrls.value).forEach((url) => URL.revokeObjectURL(url))
  const entries = await Promise.all(showcaseItems.value.map(async (item) => {
    try {
      const file = await materialLibraryApi.getItemThumbnail(item.image_material_id)
      return [item.id, URL.createObjectURL(await file.blob())]
    } catch {
      return [item.id, '']
    }
  }))
  showcaseUrls.value = Object.fromEntries(entries)
}

async function useShowcase(item) {
  commonForm.prompt = item.style_text
  invalidateRefinement()
  const selected = await setMaterial('reference', { id: item.image_material_id, name: item.title })
  if (!selected) return
  activeTab.value = 'materials'
  message.success('已使用精选案例图片和提示词')
}

async function uploadToLibrary(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length || !activeGallery.value) return
  try {
    await materialLibraryApi.importImages(files, activeGallery.value)
    message.success('图片已导入素材库')
    await loadGalleries()
  } catch (error) {
    message.error(error.message || '图片导入失败')
  }
}

function buildRefinementPayload() {
  const base = { workflow: workflow.value, user_prompt: commonForm.prompt }
  if (workflow.value === 'style_transfer') {
    return {
      ...base,
      source_material_id: styleTransferForm.reference.id,
      style_label: styleTransferForm.usePromptAsStyle ? null : styleTransferForm.styleLabel,
      use_prompt_as_style: styleTransferForm.usePromptAsStyle
    }
  }
  if (workflow.value === 'room_adapt') {
    return {
      ...base,
      style_reference_material_id: roomAdaptForm.styleReference.id,
      raw_structure_material_id: roomAdaptForm.rawStructure.id
    }
  }
  return {
    ...base,
    style_reference_material_id: crossSpaceForm.styleReference.id,
    target_space: crossSpaceForm.targetSpace,
    layout: crossSpaceForm.spaceLayout,
    addons: crossSpaceForm.spaceAddons
  }
}

async function refinePrompt() {
  if (refineIssues.value.length) {
    message.warning(refineIssues.value[0])
    return
  }
  commonForm.refining = true
  try {
    await store.refine(buildRefinementPayload())
    message.success('AI 深度优化已完成，生成约束已由服务端验证')
  } catch (error) {
    message.error(error.message || 'AI 润色失败')
  } finally {
    commonForm.refining = false
  }
}

async function revalidateRefinedPrompt() {
  if (!store.activeRefinement || !refinedPromptDraft.value.trim()) return
  commonForm.refining = true
  try {
    await store.refine({
      ...buildRefinementPayload(),
      parent_refinement_id: store.activeRefinement.id,
      edited_prompt: refinedPromptDraft.value
    })
    message.success('修改后的优化结果已重新校验并生成新版本')
  } catch (error) {
    message.error(error.message || '优化结果重新校验失败')
  } finally {
    commonForm.refining = false
  }
}

async function generate() {
  if (generateIssues.value.length) {
    message.warning(generateIssues.value[0])
    return
  }
  try {
    const job = await store.submit({
      refinement_id: store.activeRefinement.id,
      aspect_ratio: commonForm.aspectRatio,
      gen_count: commonForm.genCount,
      clarity: commonForm.clarity
    })
    activeTab.value = 'jobs'
    if (job?.id) pollJob(job.id)
    message.success('生成任务已提交，可在右侧查看实时进度')
  } catch (error) {
    message.error(error.name === 'AbortError' ? '生成任务提交超时，请到任务列表确认是否已创建' : (error.message || '生成任务提交失败'))
  }
}

async function pollJob(jobId) {
  if (polling.has(jobId)) return
  const tick = async () => {
    try {
      const job = await store.poll(jobId)
      if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
        polling.delete(jobId)
        if (job.status === 'succeeded') {
          await store.loadResults()
          await loadResultPreviews()
          activeTab.value = 'results'
          message.success('案例图生成完成')
        } else {
          message.error(job.error_message || '案例图生成失败，请查看任务详情')
        }
        return
      }
      polling.set(jobId, window.setTimeout(tick, 2000))
    } catch {
      polling.delete(jobId)
    }
  }
  await tick()
}

async function removeResult(result) {
  Modal.confirm({
    title: '删除生成结果？',
    content: '删除后无法恢复，但不会影响素材库中的输入图片。',
    okText: '删除',
    cancelText: '取消',
    async onOk() {
      await store.removeResult(result.id)
      if (resultUrls.value[result.id]) {
        URL.revokeObjectURL(resultUrls.value[result.id])
        const next = { ...resultUrls.value }
        delete next[result.id]
        resultUrls.value = next
      }
      message.success('结果已删除')
    }
  })
}

async function openResult(result) {
  resultDetail.value = result
  detailReferenceUrl.value = ''
  detailRawRoomUrl.value = ''
  const refs = [
    ['reference_material_id', 'reference'],
    ['raw_room_material_id', 'raw']
  ]
  await Promise.all(refs.map(async ([field, role]) => {
    const materialId = result[field]
    if (!materialId) return
    try {
      const response = await materialLibraryApi.getItemFile(materialId)
      const url = URL.createObjectURL(await response.blob())
      if (role === 'reference') detailReferenceUrl.value = url
      else detailRawRoomUrl.value = url
    } catch {
      // 输入素材可能已下架，详情仍保留结果和元数据。
    }
  }))
}

function closeResultDetail() {
  if (detailReferenceUrl.value) URL.revokeObjectURL(detailReferenceUrl.value)
  if (detailRawRoomUrl.value) URL.revokeObjectURL(detailRawRoomUrl.value)
  detailReferenceUrl.value = ''
  detailRawRoomUrl.value = ''
  resultDetail.value = null
}

async function downloadResult(result) {
  const response = await imageDesignApi.getResultFile(result.id)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = result.file_name || 'image-design.png'
  link.click()
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  try {
    await store.loadBootstrap()
    await Promise.all([store.loadResults(), store.loadJobs(), loadGalleries(), loadShowcase()])
    await loadResultPreviews()
  } catch (error) {
    message.error(error.message || '图片设计初始化失败')
  }
})

onBeforeUnmount(() => {
  polling.forEach((timer) => window.clearTimeout(timer))
  Object.values(galleryUrls.value).forEach((url) => URL.revokeObjectURL(url))
  Object.values(showcaseUrls.value).forEach((url) => URL.revokeObjectURL(url))
  Object.values(resultUrls.value).forEach((url) => URL.revokeObjectURL(url))
  closeResultDetail()
  const selected = [styleTransferForm.reference, roomAdaptForm.styleReference, roomAdaptForm.rawStructure, crossSpaceForm.styleReference]
  new Set(selected.map((item) => item?.url).filter(Boolean)).forEach((url) => URL.revokeObjectURL(url))
})

async function loadResultPreviews() {
  Object.values(resultUrls.value).forEach((url) => URL.revokeObjectURL(url))
  resultUrls.value = {}
  const entries = await Promise.all(store.results.map(async (result) => {
    try {
      const response = await imageDesignApi.getResultFile(result.id)
      return [result.id, URL.createObjectURL(await response.blob())]
    } catch {
      return [result.id, '']
    }
  }))
  resultUrls.value = Object.fromEntries(entries)
}
</script>

<template>
  <div class="image-design-view">
    <header class="page-head">
      <div>
        <p class="eyebrow">IMAGE DESIGN</p>
        <h1>生成案例图</h1>
        <p>从素材库选择空间图片，通过 image2 中转站生成装修案例图。</p>
      </div>
      <div class="provider-state" :class="{ ready: store.image2Ready }">
        <span class="state-dot" />{{ store.image2Ready ? `image2 · ${store.bootstrap?.image2?.model || '已配置'}` : 'image2 未配置' }}
      </div>
    </header>

    <div class="design-layout">
      <main class="form-panel">
        <section class="card workflow-card">
          <div class="section-heading"><div><span class="accent-line" />创作工作流</div><span class="required">*</span></div>
          <div class="workflow-tabs">
            <button :class="{ active: workflow === 'style_transfer' }" @click="switchWorkflow('style_transfer')">原房换装<Check v-if="workflow === 'style_transfer'" :size="15" /></button>
            <button :class="{ active: workflow === 'room_adapt' }" @click="switchWorkflow('room_adapt')">户型适配<Check v-if="workflow === 'room_adapt'" :size="15" /></button>
            <button :class="{ active: workflow === 'cross_space' }" @click="switchWorkflow('cross_space')">跨空间迁移<Check v-if="workflow === 'cross_space'" :size="15" /></button>
          </div>
          <p class="workflow-hint">{{ workflow === 'style_transfer' ? '上传原房实拍图，选择风格并保留原房结构框架。' : workflow === 'room_adapt' ? '参考效果图改造毛坯房，保留户型框架。' : '将参考图的风格迁移到指定目标空间。' }}</p>
        </section>

        <section class="card">
          <div class="section-heading"><div><span class="accent-line" />{{ workflow === 'room_adapt' ? '参考效果图' : '参考图' }}</div><span class="required">*</span></div>
          <button class="material-slot" :class="{ filled: activeReference }" @click="openPicker('reference')">
            <img v-if="activeReference?.url" :src="activeReference.url" :alt="activeReference.name" />
            <span v-else><ImagePlus :size="21" /><strong>从素材库选择{{ workflow === 'room_adapt' ? '参考效果图' : '参考图' }}</strong><small>直接使用已有素材，不重复上传</small></span>
            <small v-if="activeReference" class="slot-name">{{ activeReference.name }} · {{ materialAnalysis(activeReference, activeReferenceRole)?.status === 'completed' ? '视觉分析完成' : materialAnalysis(activeReference, activeReferenceRole)?.status === 'running' ? '视觉分析中' : materialAnalysis(activeReference, activeReferenceRole)?.status === 'failed' ? '视觉分析失败' : '待分析' }}</small>
          </button>
          <button v-if="materialAnalysis(activeReference, activeReferenceRole)?.status === 'failed'" type="button" class="analysis-retry" @click="retryAnalysis(activeReference, activeReferenceRole)">重试视觉分析</button>
          <label class="upload-inline"><Upload :size="14" /> 上传到素材库 <input ref="uploadInput" type="file" accept="image/jpeg,image/png,image/webp" multiple @change="uploadToLibrary" /></label>
        </section>

        <section v-if="workflow === 'room_adapt'" class="card">
          <div class="section-heading"><div><span class="accent-line" />毛坯实拍图</div><span class="required">*</span></div>
          <button class="material-slot" :class="{ filled: roomAdaptForm.rawStructure }" @click="openPicker('rawRoom')">
            <img v-if="roomAdaptForm.rawStructure?.url" :src="roomAdaptForm.rawStructure.url" :alt="roomAdaptForm.rawStructure.name" />
            <span v-else><ImagePlus :size="21" /><strong>从素材库选择毛坯图</strong><small>选择同一素材库中的图片</small></span>
            <small v-if="roomAdaptForm.rawStructure" class="slot-name">{{ roomAdaptForm.rawStructure.name }} · {{ materialAnalysis(roomAdaptForm.rawStructure, 'structure_source')?.status === 'completed' ? '视觉分析完成' : materialAnalysis(roomAdaptForm.rawStructure, 'structure_source')?.status === 'running' ? '视觉分析中' : materialAnalysis(roomAdaptForm.rawStructure, 'structure_source')?.status === 'failed' ? '视觉分析失败' : '待分析' }}</small>
          </button>
          <button v-if="materialAnalysis(roomAdaptForm.rawStructure, 'structure_source')?.status === 'failed'" type="button" class="analysis-retry" @click="retryAnalysis(roomAdaptForm.rawStructure, 'structure_source')">重试视觉分析</button>
        </section>

        <section v-if="workflow === 'style_transfer'" class="card">
          <div class="section-heading"><div><span class="accent-line" />换装风格</div></div>
          <div class="choice-grid styles-grid">
            <button v-for="item in styleOptions" :key="item" type="button" :class="{ selected: styleTransferForm.styleLabel === item && !styleTransferForm.usePromptAsStyle }" @click="chooseStyle(item)">{{ item }}</button>
            <button type="button" :class="{ selected: styleTransferForm.usePromptAsStyle }" @click="usePromptForStyle">使用补充描述作为风格提示词</button>
          </div>
        </section>

        <section v-if="workflow === 'cross_space'" class="card">
          <div class="section-heading"><div><span class="accent-line" />目标空间</div></div>
          <div class="choice-grid space-grid"><button v-for="item in spaceOptions" :key="item[0]" :class="{ selected: crossSpaceForm.targetSpace === item[0] }" @click="chooseSpace(item)">{{ item[1] }}</button></div>
          <div class="subheading">布局类型</div>
          <div class="choice-grid layout-grid"><button v-for="item in currentLayouts" :key="item[0]" :class="{ selected: crossSpaceForm.spaceLayout === item[0] }" @click="chooseLayout(item)">{{ item[1] }}</button></div>
          <div class="subheading">附加元素</div>
          <div class="choice-grid addon-grid"><button v-for="item in currentAddons" :key="item[0]" type="button" :class="{ selected: crossSpaceForm.spaceAddons.includes(item[0]) }" @click="toggleAddon(item[0])">{{ item[1] }}</button></div>
        </section>

        <section class="card prompt-card">
          <div class="section-heading"><div><span class="accent-line" />补充描述 <span class="required">*</span></div><button type="button" class="clear-button" @click="clearPrompt">清空</button></div>
          <textarea v-model="commonForm.prompt" maxlength="3000" placeholder="描述你希望生成的空间风格、材质、色彩和氛围…" @input="invalidateRefinement" />
          <div class="prompt-footer"><span>{{ commonForm.prompt.length }}/3000</span><button type="button" class="refine-button" :class="{ blocked: refineIssues.length }" :disabled="commonForm.refining" @click="refinePrompt"><LoaderCircle v-if="commonForm.refining" class="spin" :size="16" /><Sparkles v-else :size="16" />{{ commonForm.refining ? '正在分析并编译…' : 'AI 深度优化（必做）' }}</button></div>
          <div class="refine-note" :class="{ done: store.activeRefinement }"><Check v-if="store.activeRefinement" :size="14" /><span>{{ store.activeRefinement ? '图片、选项和最终提示词已通过服务端验证；修改语义条件后需重新优化。' : (refineIssues[0] || '请完成 AI 深度优化后再提交生成。') }}</span></div>
        </section>

        <section v-if="store.activeRefinement" class="card refinement-card">
          <div class="section-heading"><div><span class="accent-line" />执行预览</div><span class="verified-badge"><Check :size="13" />已验证</span></div>
          <div class="analysis-summary" v-for="role in store.activeRefinement.plan.image_roles" :key="role.material_id"><strong>{{ role.label }}</strong><span>{{ materialAnalysis({ id: role.material_id }, role.role)?.result?.room_type || '已完成视觉分析' }}</span></div>
          <div class="effective-options">
            <strong>本次生效选项</strong>
            <span>风格：{{ store.activeRefinement.plan.style_profile.label }}</span>
            <span v-if="store.activeRefinement.plan.target_space">空间：{{ store.activeRefinement.plan.target_space.label }}</span>
            <span v-if="store.activeRefinement.plan.layout">布局：{{ store.activeRefinement.plan.layout.label }}</span>
            <span v-if="store.activeRefinement.plan.addons?.length">附加元素：{{ store.activeRefinement.plan.addons.map((item) => item.label).join('、') }}</span>
          </div>
          <div class="constraint-list"><strong>工作流硬约束</strong><ul><li v-for="item in store.activeRefinement.plan.hard_constraints" :key="item">{{ item }}</li></ul></div>
          <div v-if="store.activeRefinement.conflicts.length" class="conflict-list"><strong>已修正冲突</strong><ul><li v-for="item in store.activeRefinement.conflicts" :key="item">{{ item }}</li></ul></div>
          <label class="compiled-prompt"><span>已验证优化结果</span><textarea v-model="refinedPromptDraft" maxlength="3000" @input="refinedPromptDirty = refinedPromptDraft !== store.activeRefinement.compiled_prompt" /></label>
          <div v-if="refinedPromptDirty" class="prompt-revalidate"><span>修改后不能直接生成，服务端将重新注入并校验全部硬约束。</span><button type="button" :disabled="commonForm.refining || !refinedPromptDraft.trim()" @click="revalidateRefinedPrompt">重新校验</button></div>
          <label class="compiled-prompt"><span>最终生成提示词（随画幅实时更新）</span><textarea :value="compiledPromptPreview" readonly /></label>
        </section>

        <section class="card options-card">
          <div class="option-group"><strong>图片比例</strong><div class="option-row"><button v-for="item in [['3:4','竖版 3:4','1152×1536'],['4:3','横版 4:3','1536×1152'],['1:1','方图 1:1','1024×1024']]" :key="item[0]" :class="{ selected: commonForm.aspectRatio === item[0] }" @click="commonForm.aspectRatio = item[0]">{{ item[1] }}<small>{{ item[2] }}</small></button></div></div>
          <div class="option-group"><strong>生成数量</strong><div class="option-row"><button v-for="count in [1, 2, 4]" :key="count" :class="{ selected: commonForm.genCount === count }" @click="commonForm.genCount = count">{{ count }} 张</button></div></div>
          <div class="option-group"><strong>清晰度</strong><div class="option-row"><button v-for="quality in ['1K', '2K']" :key="quality" :class="{ selected: commonForm.clarity === quality }" @click="commonForm.clarity = quality">{{ quality }} {{ quality === '1K' ? '标清' : '高清' }}</button></div></div>
        </section>

        <section class="submit-row">
          <button type="button" class="generate-button" :class="{ blocked: !canSubmit }" :disabled="store.loading" @click="generate"><WandSparkles :size="18" />{{ store.loading ? '正在提交…' : '生成案例图' }}</button>
        </section>
        <p v-if="generateIssues.length" class="blocked-note">{{ generateIssues[0] }}；点击“生成案例图”可查看提示。</p>
      </main>

      <aside class="workspace-panel card">
        <div class="workspace-tabs"><button :class="{ active: activeTab === 'materials' }" @click="activeTab = 'materials'">素材库 <small>{{ galleryItems.length }}</small></button><button :class="{ active: activeTab === 'results' }" @click="activeTab = 'results'; store.loadResults().then(loadResultPreviews)">生成结果 <small>{{ store.results.length }}</small></button><button :class="{ active: activeTab === 'showcase' }" @click="activeTab = 'showcase'">精选案例提示词 <small>{{ showcaseItems.length }}</small></button><button :class="{ active: activeTab === 'jobs' }" @click="activeTab = 'jobs'; store.loadJobs()">任务 <small>{{ store.jobs.filter((item) => !['succeeded','failed'].includes(item.status)).length }}</small></button><button :class="{ active: activeTab === 'assets' }" @click="activeTab = 'assets'">资产中心</button></div>
        <div v-if="activeTab === 'materials'" class="workspace-content">
          <div class="library-toolbar"><select v-model="activeGallery" @change="refreshGallery"><option v-for="gallery in galleries" :key="gallery.id" :value="gallery.id">{{ gallery.name }}</option></select><button title="导入到素材库" @click="uploadInput?.click()"><Upload :size="15" /></button><button title="刷新" @click="refreshGallery"><RefreshCw :size="15" /></button></div>
          <div v-if="loadingGallery" class="empty-state"><LoaderCircle class="spin" :size="24" /></div>
          <div v-else-if="galleryItems.length" class="material-grid"><button v-for="item in galleryItems" :key="item.id" class="material-card" @click="setMaterial('reference', item)"><img v-if="galleryUrls[item.id]" :src="galleryUrls[item.id]" :alt="item.name" /><span>{{ item.name }}</span><small>{{ item.width }}×{{ item.height }}</small></button></div>
          <div v-else class="empty-state"><ImagePlus :size="28" /><p>素材库暂无图片</p><button @click="uploadInput?.click()">导入图片</button></div>
        </div>
        <div v-else-if="activeTab === 'results'" class="workspace-content result-grid"><div v-if="!store.results.length" class="empty-state">暂无生成结果</div><article v-for="result in store.results" :key="result.id" class="result-card" @click="openResult(result)"><img v-if="resultUrls[result.id]" :src="resultUrls[result.id]" :alt="result.file_name" /><div><strong>{{ result.workflow === 'room_adapt' ? '户型适配' : result.workflow === 'cross_space' ? '跨空间迁移' : '原房换装' }}</strong><small>{{ result.width }}×{{ result.height }}</small></div><div class="result-actions"><button @click.stop="downloadResult(result)"><Download :size="14" /></button><button @click.stop="removeResult(result)"><Trash2 :size="14" /></button></div></article></div>
        <div v-else-if="activeTab === 'showcase'" class="workspace-content showcase-grid"><div v-if="!showcaseItems.length" class="empty-state">暂无精选案例</div><article v-for="item in showcaseItems" :key="item.id" class="showcase-card"><img v-if="showcaseUrls[item.id]" :src="showcaseUrls[item.id]" :alt="item.title" /><div class="showcase-copy"><strong>{{ item.title }}</strong><small>{{ item.category }}</small><p>{{ item.style_text }}</p><button @click="useShowcase(item)">使用案例</button></div></article></div>
        <div v-else-if="activeTab === 'jobs'" class="workspace-content job-list"><div v-if="!store.jobs.length" class="empty-state">暂无任务</div><article v-for="job in store.jobs" :key="job.id" class="job-card" :class="{ failed: job.status === 'failed' }"><div><strong>{{ job.workflow === 'room_adapt' ? '户型适配' : job.workflow === 'cross_space' ? '跨空间迁移' : '原房换装' }}</strong><small>{{ jobStatusLabels[job.status] || job.status }} · {{ job.progress }}%</small></div><div class="progress"><span :style="{ width: `${job.progress}%` }" /></div><p v-if="job.status === 'failed'" class="job-error">{{ job.error_message || '生成失败，请重新提交任务' }}</p></article></div>
        <div v-else class="workspace-content"><div class="asset-heading"><strong>资产中心图库</strong><small>与现有素材库共享，点击图片可设为参考图</small></div><div v-if="galleryItems.length" class="material-grid"><button v-for="item in galleryItems" :key="item.id" class="material-card" @click="setMaterial('reference', item)"><img v-if="galleryUrls[item.id]" :src="galleryUrls[item.id]" :alt="item.name" /><span>{{ item.name }}</span><small>{{ item.width }}×{{ item.height }}</small></button></div><div v-else class="empty-state"><ImagePlus :size="28" /><p>当前图库暂无资产</p><button @click="activeTab = 'materials'">去素材库导入</button></div></div>
      </aside>
    </div>

    <div v-if="resultDetail" class="result-detail-overlay" @click.self="closeResultDetail">
      <section class="result-detail-modal">
        <button class="result-detail-close" aria-label="关闭" @click="closeResultDetail">×</button>
        <div class="result-detail-images">
          <div class="detail-output"><img v-if="resultUrls[resultDetail.id]" :src="resultUrls[resultDetail.id]" :alt="resultDetail.file_name" /><span>生成结果</span></div>
          <div v-if="detailReferenceUrl" class="detail-input"><img :src="detailReferenceUrl" alt="参考图" /><span>参考图</span></div>
          <div v-if="detailRawRoomUrl" class="detail-input"><img :src="detailRawRoomUrl" alt="毛坯实拍图" /><span>毛坯实拍图</span></div>
        </div>
        <div class="result-detail-meta"><strong>{{ resultDetail.workflow === 'room_adapt' ? '户型适配' : resultDetail.workflow === 'cross_space' ? '跨空间迁移' : '原房换装' }}</strong><span>{{ resultDetail.width }}×{{ resultDetail.height }} · {{ resultDetail.created_at || '刚刚' }}</span><p>{{ resultDetail.prompt }}</p></div>
      </section>
    </div>

    <MaterialImagePickerModal v-model:open="pickerOpen" :selected-item-id="pickerRole === 'reference' ? activeReference?.id : roomAdaptForm.rawStructure?.id" title="从素材库选择图片" :description="pickerRole === 'reference' ? '选择参考图' : '选择毛坯实拍图'" :confirm-text="pickerRole === 'reference' ? '使用这张参考图' : '使用这张毛坯图'" @select="(item) => setMaterial(pickerRole, item)" />
  </div>
</template>

<style scoped lang="less">
.image-design-view { min-height: 100%; padding: 28px 32px 48px; background: var(--gray-0); color: var(--color-text); }
.page-head { max-width: 1480px; margin: 0 auto 22px; display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
.eyebrow { margin: 0 0 6px; color: var(--main-700); font-size: 11px; font-weight: 800; letter-spacing: .16em; }
h1 { margin: 0; font-size: 28px; letter-spacing: -.04em; } .page-head p:last-child { margin: 7px 0 0; color: var(--gray-600); }
.provider-state { display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--color-warning-100); border-radius: 999px; padding: 8px 12px; color: var(--color-warning-700); background: var(--color-warning-10); font-size: 12px; }
.provider-state.ready { color: var(--color-success-700); border-color: var(--color-success-100); background: var(--color-success-10); } .state-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.design-layout { max-width: 1480px; margin: auto; display: grid; grid-template-columns: minmax(0, 1fr) 450px; gap: 20px; align-items: start; } .form-panel { display: grid; gap: 14px; }
.card { border: 1px solid var(--gray-200); border-radius: 16px; background: var(--main-0); box-shadow: 0 8px 28px rgba(1, 21, 31, .05); } .form-panel .card { padding: 18px; }
.section-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 13px; font-weight: 800; } .section-heading > div { display: flex; gap: 8px; align-items: center; } .accent-line { width: 3px; height: 17px; border-radius: 9px; background: var(--main-600); } .required { color: var(--color-error-600); }
.workflow-tabs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; } .workflow-tabs button, .choice-grid button, .option-row button { border: 1px solid var(--gray-200); border-radius: 11px; background: var(--gray-0); color: var(--gray-700); padding: 11px 10px; cursor: pointer; transition: .15s; } .workflow-tabs button { display: flex; justify-content: center; gap: 5px; font-weight: 700; } button:hover { border-color: var(--main-400); } button.active, button.selected { border-color: var(--main-500); color: var(--main-800); background: var(--main-30); box-shadow: 0 0 0 2px var(--main-100); }
.workflow-hint { margin: 12px 0 0; padding: 11px 12px; border-radius: 10px; color: var(--main-800); background: var(--main-20); line-height: 1.65; font-size: 13px; }
.material-slot { width: 100%; min-height: 180px; border: 1px dashed var(--main-300); border-radius: 12px; background: var(--gray-50); color: var(--main-700); cursor: pointer; overflow: hidden; display: grid; place-items: center; position: relative; } .material-slot > span { display: grid; justify-items: center; gap: 7px; } .material-slot strong { font-size: 14px; } .material-slot small { color: var(--gray-500); } .material-slot img { display: block; width: 100%; height: clamp(260px, 38vw, 520px); object-fit: contain; background: var(--gray-50); } .slot-name { position: absolute; left: 10px; right: 10px; bottom: 8px; padding: 4px 8px; border-radius: 6px; color: #fff !important; background: rgba(0,0,0,.58); text-align: left; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.upload-inline { display: inline-flex; align-items: center; gap: 5px; margin-top: 10px; color: var(--main-700); font-size: 12px; cursor: pointer; } .upload-inline input { display: none; }
.choice-grid { display: grid; gap: 8px; } .styles-grid { grid-template-columns: repeat(5, 1fr); } .space-grid { grid-template-columns: repeat(6, 1fr); } .layout-grid { grid-template-columns: repeat(4, 1fr); } .addon-grid { grid-template-columns: repeat(3, 1fr); } .choice-grid button { min-height: 42px; font-size: 12px; } .subheading { margin: 16px 0 9px; color: var(--gray-700); font-size: 12px; font-weight: 700; }
.prompt-card textarea { width: 100%; min-height: 156px; box-sizing: border-box; padding: 12px; border: 1px solid var(--gray-200); border-radius: 10px; resize: vertical; font: inherit; line-height: 1.65; outline: none; } .prompt-card textarea:focus { border-color: var(--main-500); box-shadow: 0 0 0 2px var(--main-100); } .prompt-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 9px; color: var(--gray-500); font-size: 12px; } .clear-button { border: 0; background: transparent; color: var(--gray-500); cursor: pointer; } .refine-button { display: inline-flex; align-items: center; gap: 6px; border: 0; border-radius: 8px; padding: 8px 12px; color: #fff; background: var(--main-700); font-weight: 700; cursor: pointer; } .refine-button.blocked { opacity: .68; } .refine-button:disabled { opacity: .45; cursor: not-allowed; } .refine-model-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; color: var(--gray-600); font-size: 12px; } .refine-model-row > :last-child { min-width: 220px; } .refine-note { display: flex; align-items: center; gap: 6px; margin: 10px 0 0; color: var(--color-error-700); font-size: 12px; } .refine-note span { flex: 1; } .refine-note button { border: 0; padding: 0; color: var(--main-700); background: transparent; cursor: pointer; } .refine-note.done { color: var(--color-success-700); }
.verified-badge { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; border-radius: 999px; color: var(--color-success-700); background: var(--color-success-10); font-size: 11px; } .analysis-summary { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--gray-100); color: var(--gray-600); font-size: 12px; } .analysis-summary strong { color: var(--gray-800); } .analysis-retry { margin-top: 8px; border: 0; padding: 0; color: var(--main-700); background: transparent; font-size: 12px; font-weight: 700; cursor: pointer; } .effective-options { display: flex; flex-wrap: wrap; gap: 7px 12px; margin-top: 13px; padding: 10px 12px; border-radius: 8px; background: var(--gray-50); color: var(--gray-700); font-size: 12px; } .effective-options strong { width: 100%; color: var(--gray-800); } .constraint-list, .conflict-list { margin-top: 13px; color: var(--gray-700); font-size: 12px; } .constraint-list ul, .conflict-list ul { margin: 7px 0 0; padding-left: 20px; line-height: 1.7; } .conflict-list { color: var(--color-warning-700); } .compiled-prompt { display: grid; gap: 7px; margin-top: 14px; color: var(--gray-700); font-size: 12px; font-weight: 700; } .compiled-prompt textarea { min-height: 190px; padding: 11px; border: 1px solid var(--gray-200); border-radius: 9px; background: var(--gray-50); color: var(--gray-700); font: inherit; font-weight: 400; line-height: 1.65; resize: vertical; } .prompt-revalidate { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 8px; color: var(--color-warning-700); font-size: 12px; } .prompt-revalidate button { border: 0; border-radius: 7px; padding: 7px 10px; color: #fff; background: var(--main-700); font-weight: 700; cursor: pointer; } .prompt-revalidate button:disabled { opacity: .45; cursor: not-allowed; }
.option-group + .option-group { margin-top: 15px; } .option-group > strong { display: block; margin-bottom: 8px; font-size: 13px; } .option-row { display: flex; gap: 8px; flex-wrap: wrap; } .option-row button { min-width: 105px; } .option-row small { display: block; margin-top: 3px; color: var(--gray-500); font-size: 10px; }
.submit-row { display: flex; justify-content: flex-end; } .generate-button { display: inline-flex; align-items: center; gap: 7px; padding: 13px 18px; border: 0; border-radius: 10px; color: #fff; background: linear-gradient(135deg, var(--main-700), var(--main-500)); font-weight: 800; cursor: pointer; white-space: nowrap; } .generate-button.blocked { opacity: .68; } .generate-button:disabled { opacity: .45; cursor: not-allowed; } .blocked-note { margin: -6px 0 0; color: var(--color-warning-700); font-size: 12px; }
.workspace-panel { min-height: 680px; overflow: hidden; position: sticky; top: 18px; } .workspace-tabs { display: flex; gap: 2px; padding: 12px 13px 0; overflow-x: auto; border-bottom: 1px solid var(--gray-200); } .workspace-tabs button { padding: 10px 9px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--gray-600); white-space: nowrap; cursor: pointer; } .workspace-tabs button.active { color: var(--main-800); border-color: var(--main-600); } .workspace-tabs small { margin-left: 4px; color: var(--gray-500); } .workspace-content { padding: 14px; } .library-toolbar { display: flex; gap: 7px; margin-bottom: 12px; } .library-toolbar select { flex: 1; padding: 9px; border: 1px solid var(--gray-200); border-radius: 8px; background: #fff; } .library-toolbar button, .result-actions button { display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--gray-200); border-radius: 8px; background: #fff; color: var(--gray-600); cursor: pointer; }
.material-grid, .result-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; } .material-card { display: grid; gap: 4px; padding: 0 0 8px; border: 1px solid var(--gray-200); border-radius: 10px; overflow: hidden; background: #fff; text-align: left; cursor: pointer; } .material-card img { width: 100%; height: 145px; object-fit: contain; background: var(--gray-50); } .material-card span, .material-card small { padding: 0 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .material-card span { font-size: 12px; font-weight: 700; } .material-card small { color: var(--gray-500); font-size: 10px; }
.result-card { overflow: hidden; border: 1px solid var(--gray-200); border-radius: 10px; background: #fff; } .result-card img { width: 100%; height: 190px; object-fit: contain; background: var(--gray-50); } .result-card > div:not(.result-actions) { display: grid; gap: 3px; padding: 8px; } .result-card small { color: var(--gray-500); font-size: 10px; } .result-actions { display: flex; gap: 6px; padding: 0 8px 8px; } .job-list { display: grid; gap: 9px; } .job-card { padding: 10px; border: 1px solid var(--gray-200); border-radius: 9px; } .job-card.failed { border-color: var(--color-error-100); background: var(--color-error-10); } .job-card > div:first-child { display: flex; justify-content: space-between; gap: 8px; } .job-card small { color: var(--gray-500); } .job-card.failed small, .job-error { color: var(--color-error-700); } .job-error { margin: 8px 0 0; font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; } .progress { height: 5px; margin-top: 9px; border-radius: 99px; background: var(--gray-100); overflow: hidden; } .progress span { display: block; height: 100%; border-radius: inherit; background: var(--main-600); transition: width .2s; } .job-card.failed .progress span { background: var(--color-error-500); }
.showcase-grid { display: grid; gap: 12px; } .showcase-card { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 10px; padding: 9px; border: 1px solid var(--gray-200); border-radius: 10px; background: #fff; } .showcase-card img { width: 112px; height: 140px; border-radius: 7px; object-fit: contain; background: var(--gray-50); } .showcase-copy { min-width: 0; display: grid; align-content: start; gap: 5px; } .showcase-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .showcase-copy small { color: var(--gray-500); } .showcase-copy p { margin: 2px 0 4px; color: var(--gray-600); font-size: 11px; line-height: 1.5; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 4; overflow: hidden; } .showcase-copy button { justify-self: start; border: 1px solid var(--main-300); border-radius: 7px; padding: 6px 10px; color: var(--main-700); background: var(--main-20); cursor: pointer; }
.result-card { cursor: pointer; } .result-detail-overlay { position: fixed; z-index: 1000; inset: 0; display: grid; place-items: center; padding: 24px; background: rgba(4, 20, 27, .62); } .result-detail-modal { position: relative; width: min(980px, 96vw); max-height: 92vh; overflow: auto; padding: 22px; border-radius: 16px; background: var(--main-0); box-shadow: 0 18px 70px rgba(0, 0, 0, .24); } .result-detail-close { position: absolute; top: 8px; right: 12px; width: 30px; height: 30px; border: 0; border-radius: 50%; color: var(--gray-600); background: var(--gray-100); font-size: 22px; line-height: 1; cursor: pointer; } .result-detail-images { display: grid; grid-template-columns: minmax(0, 2fr) repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; } .result-detail-images > div { display: grid; gap: 6px; color: var(--gray-600); font-size: 12px; } .result-detail-images img { width: 100%; max-height: 540px; border-radius: 10px; object-fit: contain; background: var(--gray-50); } .detail-input img { max-height: 260px; } .result-detail-meta { display: grid; gap: 6px; margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--gray-200); } .result-detail-meta span { color: var(--gray-500); font-size: 12px; } .result-detail-meta p { margin: 4px 0 0; white-space: pre-wrap; color: var(--gray-700); line-height: 1.65; }
.asset-heading { display: grid; gap: 4px; margin-bottom: 12px; } .asset-heading small { color: var(--gray-500); font-size: 11px; }
.empty-state, .assets-placeholder { min-height: 280px; display: grid; place-items: center; align-content: center; gap: 8px; color: var(--gray-500); text-align: center; } .empty-state button { border: 1px solid var(--main-300); border-radius: 8px; padding: 7px 12px; color: var(--main-700); background: var(--main-20); cursor: pointer; } .spin { animation: spin 1s linear infinite; } @keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 1100px) { .design-layout { grid-template-columns: 1fr; } .workspace-panel { position: static; } }
@media (max-width: 700px) { .image-design-view { padding: 18px 12px 30px; } .page-head { display: grid; } .styles-grid { grid-template-columns: repeat(2, 1fr); } .space-grid { grid-template-columns: repeat(3, 1fr); } .layout-grid { grid-template-columns: repeat(2, 1fr); } .addon-grid { grid-template-columns: 1fr; } .submit-row { display: grid; } .generate-button { justify-content: center; } .result-detail-images { grid-template-columns: 1fr; } }
</style>
