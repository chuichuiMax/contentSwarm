<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Copy,
  Eye,
  EyeOff,
  Image as ImageIcon,
  LoaderCircle,
  Lock,
  LockOpen,
  Plus,
  Redo2,
  Save,
  Trash2,
  Type,
  Undo2
} from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import { drawEditorScene, ensureEditorFonts, pointInLayer } from '@/utils/coverEditorCanvas'

const route = useRoute()
const router = useRouter()
const canvasRef = ref(null)
const textInputRef = ref(null)
const project = ref(null)
const scene = ref(null)
const backgroundImage = ref(null)
const backgroundUrl = ref('')
const selectedLayerId = ref('')
const loading = ref(true)
const saving = ref(false)
const saveStatus = ref('正在加载')
const applying = ref(false)
const renderProgress = ref(0)
const zoom = ref(52)
const history = ref([])
const historyIndex = ref(-1)
let saveTimer = null
let dragState = null
let destroyed = false
let fontWarningShown = false

const selectedLayer = computed(() =>
  scene.value?.layers.find((layer) => layer.id === selectedLayerId.value)
)
const canUndo = computed(() => historyIndex.value > 0)
const canRedo = computed(
  () => historyIndex.value >= 0 && historyIndex.value < history.value.length - 1
)
const returnPath = computed(() =>
  route.query.taskId ? `/content/tasks/${route.query.taskId}?resultDetail=1` : '/content/covers'
)

const clone = (value) => JSON.parse(JSON.stringify(value))
const createRequestId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    return crypto.randomUUID()
  return `cover-editor-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function renderCanvas() {
  const canvas = canvasRef.value
  if (!canvas || !scene.value) return
  canvas.width = scene.value.canvas.width
  canvas.height = scene.value.canvas.height
  drawEditorScene(
    canvas.getContext('2d'),
    scene.value,
    backgroundImage.value,
    selectedLayerId.value
  )
}

async function loadSceneFonts() {
  try {
    await ensureEditorFonts(scene.value?.layers || [])
  } catch {
    if (!fontWarningShown) {
      fontWarningShown = true
      message.warning('标准画板字体加载失败，当前预览可能与最终导出存在细微差异')
    }
  }
  renderCanvas()
}

function resetHistory() {
  history.value = [clone(scene.value)]
  historyIndex.value = 0
}

function recordHistory() {
  const snapshot = clone(scene.value)
  const current = history.value[historyIndex.value]
  if (current && JSON.stringify(current) === JSON.stringify(snapshot)) return
  history.value = [...history.value.slice(0, historyIndex.value + 1), snapshot].slice(-60)
  historyIndex.value = history.value.length - 1
  scheduleSave()
  renderCanvas()
}

function restoreHistory(index) {
  if (index < 0 || index >= history.value.length) return
  historyIndex.value = index
  scene.value = clone(history.value[index])
  if (!scene.value.layers.some((layer) => layer.id === selectedLayerId.value)) {
    selectedLayerId.value = scene.value.layers.at(-1)?.id || ''
  }
  scheduleSave()
  renderCanvas()
}

const undo = () => canUndo.value && restoreHistory(historyIndex.value - 1)
const redo = () => canRedo.value && restoreHistory(historyIndex.value + 1)

function scheduleSave() {
  saveStatus.value = '有未保存修改'
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => void saveNow(), 800)
}

async function saveNow() {
  if (!project.value || !scene.value || saveStatus.value === '已自动保存') return true
  if (saving.value) {
    while (saving.value) await new Promise((resolve) => window.setTimeout(resolve, 50))
    return saveStatus.value === '有未保存修改' ? saveNow() : saveStatus.value !== '保存失败'
  }
  window.clearTimeout(saveTimer)
  const snapshot = clone(scene.value)
  const serialized = JSON.stringify(snapshot)
  saving.value = true
  saveStatus.value = '保存中'
  try {
    const response = await contentApi.updateCoverEditorProject(project.value.id, {
      expected_revision: project.value.revision,
      scene: snapshot
    })
    project.value = response.project
    if (serialized === JSON.stringify(scene.value)) {
      saveStatus.value = '已自动保存'
    } else {
      saveStatus.value = '有未保存修改'
      scheduleSave()
    }
    return true
  } catch (error) {
    saveStatus.value = '保存失败'
    message.error(error.message || '封面草稿保存失败')
    return false
  } finally {
    saving.value = false
  }
}

function updateLayer(field, value) {
  if (!selectedLayer.value) return
  if (
    [
      'font_size',
      'opacity',
      'letter_spacing',
      'line_height',
      'rotation',
      'stroke_width',
      'shadow_blur',
      'shadow_offset_x',
      'shadow_offset_y',
      'background_padding',
      'background_opacity',
      'background_radius',
      'x',
      'y',
      'width',
      'height'
    ].includes(field) &&
    (value === null || !Number.isFinite(Number(value)))
  )
    return
  if (field === 'text' && selectedLayer.value.name === selectedLayer.value.text) {
    selectedLayer.value.name = String(value || '').slice(0, 80) || '文字'
  }
  if (field === 'fill') selectedLayer.value.fill_runs = []
  selectedLayer.value[field] = value
  recordHistory()
  if (field === 'font_family' || field === 'font_weight') void loadSceneFonts()
}

function updateFillRun(index, fill) {
  if (!selectedLayer.value?.fill_runs?.[index]) return
  selectedLayer.value.fill_runs[index].fill = fill
  recordHistory()
}

function addTextLayer() {
  const order = Math.max(-1, ...scene.value.layers.map((layer) => layer.order)) + 1
  const id = `text_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
  scene.value.layers.push({
    id,
    layer_type: 'text',
    name: '新文字',
    text: '双击这里编辑文字',
    x: Math.round(scene.value.canvas.width * 0.15),
    y: Math.round(scene.value.canvas.height * 0.18),
    width: Math.round(scene.value.canvas.width * 0.7),
    height: 140,
    rotation: 0,
    opacity: 1,
    visible: true,
    locked: false,
    order,
    font_family: 'Noto Sans CJK SC',
    font_size: 64,
    font_weight: 700,
    font_style: 'normal',
    fill: '#FFFFFF',
    fill_runs: [],
    align: 'center',
    line_height: 1.2,
    letter_spacing: 0,
    stroke: true,
    stroke_color: '#222222',
    stroke_width: 2,
    shadow: true,
    shadow_color: '#000000',
    shadow_blur: 6,
    shadow_offset_x: 0,
    shadow_offset_y: 6,
    background_fill: null,
    background_opacity: 1,
    background_radius: 12,
    background_padding: 0
  })
  selectedLayerId.value = id
  recordHistory()
}

function duplicateLayer() {
  if (!selectedLayer.value) return
  const source = clone(selectedLayer.value)
  source.id = `text_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
  source.name = `${source.name} 副本`
  source.x += 24
  source.y += 24
  source.order = Math.max(-1, ...scene.value.layers.map((layer) => layer.order)) + 1
  scene.value.layers.push(source)
  selectedLayerId.value = source.id
  recordHistory()
}

function deleteLayer(layerId = selectedLayerId.value) {
  const index = scene.value.layers.findIndex((layer) => layer.id === layerId)
  if (index < 0) return
  scene.value.layers.splice(index, 1)
  selectedLayerId.value = scene.value.layers[Math.max(0, index - 1)]?.id || ''
  recordHistory()
}

function moveLayer(layerId, direction) {
  const ordered = [...scene.value.layers].sort((left, right) => left.order - right.order)
  const index = ordered.findIndex((layer) => layer.id === layerId)
  const target = index + direction
  if (index < 0 || target < 0 || target >= ordered.length) return
  ;[ordered[index].order, ordered[target].order] = [ordered[target].order, ordered[index].order]
  recordHistory()
}

function selectLayer(layer) {
  selectedLayerId.value = layer.id
  renderCanvas()
}

function toggleLayer(layer, field) {
  layer[field] = !layer[field]
  recordHistory()
}

function canvasPoint(event) {
  const rect = canvasRef.value.getBoundingClientRect()
  return {
    x: ((event.clientX - rect.left) / rect.width) * scene.value.canvas.width,
    y: ((event.clientY - rect.top) / rect.height) * scene.value.canvas.height
  }
}

function onPointerDown(event) {
  if (!scene.value) return
  const point = canvasPoint(event)
  const ordered = [...scene.value.layers].sort((left, right) => right.order - left.order)
  let layer = selectedLayer.value
  const nearResize =
    layer &&
    !layer.locked &&
    Math.hypot(point.x - (layer.x + layer.width), point.y - (layer.y + layer.height)) < 44
  if (!nearResize)
    layer = ordered.find((item) => item.visible && pointInLayer(item, point.x, point.y))
  if (!layer) {
    selectedLayerId.value = ''
    renderCanvas()
    return
  }
  selectedLayerId.value = layer.id
  renderCanvas()
  if (layer.locked) return
  dragState = {
    mode: nearResize ? 'resize' : 'move',
    start: point,
    layer,
    initial: { x: layer.x, y: layer.y, width: layer.width, height: layer.height }
  }
  canvasRef.value.setPointerCapture(event.pointerId)
}

function onPointerMove(event) {
  if (!dragState) return
  const point = canvasPoint(event)
  const dx = point.x - dragState.start.x
  const dy = point.y - dragState.start.y
  if (dragState.mode === 'resize') {
    dragState.layer.width = Math.max(80, dragState.initial.width + dx)
    dragState.layer.height = Math.max(40, dragState.initial.height + dy)
  } else {
    dragState.layer.x = dragState.initial.x + dx
    dragState.layer.y = dragState.initial.y + dy
  }
  renderCanvas()
}

function onPointerUp() {
  if (!dragState) return
  dragState = null
  recordHistory()
}

async function onCanvasDoubleClick(event) {
  if (!scene.value) return
  const point = canvasPoint(event)
  const layer = [...scene.value.layers]
    .sort((left, right) => right.order - left.order)
    .find((item) => item.visible && !item.locked && pointInLayer(item, point.x, point.y))
  if (!layer) return
  selectedLayerId.value = layer.id
  renderCanvas()
  await nextTick()
  textInputRef.value?.focus?.()
}

function handleKeydown(event) {
  const tag = event.target?.tagName?.toLowerCase()
  if (['input', 'textarea', 'select'].includes(tag)) return
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    event.shiftKey ? redo() : undo()
    return
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
    event.preventDefault()
    redo()
    return
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd') {
    event.preventDefault()
    duplicateLayer()
    return
  }
  if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault()
    deleteLayer()
    return
  }
  if (!selectedLayer.value || selectedLayer.value.locked || !event.key.startsWith('Arrow')) return
  event.preventDefault()
  const step = event.shiftKey ? 10 : 1
  if (event.key === 'ArrowLeft') selectedLayer.value.x -= step
  if (event.key === 'ArrowRight') selectedLayer.value.x += step
  if (event.key === 'ArrowUp') selectedLayer.value.y -= step
  if (event.key === 'ArrowDown') selectedLayer.value.y += step
  recordHistory()
}

const delay = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))

async function applyCover() {
  if (applying.value) return
  const saved = await saveNow()
  if (!saved) return
  applying.value = true
  renderProgress.value = 0
  try {
    const response = await contentApi.renderCoverEditorProject(project.value.id, {
      expected_revision: project.value.revision,
      idempotency_key: createRequestId()
    })
    let job = response.job
    for (let attempt = 0; attempt < 240 && !destroyed; attempt += 1) {
      renderProgress.value = Number(job.progress || 0)
      if (job.status === 'succeeded') break
      if (['failed', 'cancelled'].includes(job.status)) {
        throw new Error(job.error_message || '高清封面生成失败，草稿已保留')
      }
      await delay(750)
      job = (await contentApi.getCoverJob(job.id)).job
    }
    if (job.status !== 'succeeded') throw new Error('高清封面生成超时，草稿已保留')
    const assetId = job.result?.asset_ids?.[0]
    if (!assetId) throw new Error('封面任务没有返回输出图片')
    if (project.value.artifact_id) await contentApi.setCurrentCover(job.id, assetId)
    message.success('新封面已生成并应用，原封面仍保留在版本记录中')
    await router.push(returnPath.value)
  } catch (error) {
    message.error(error.message || '应用封面失败，草稿已保留')
  } finally {
    applying.value = false
  }
}

async function goBack() {
  await saveNow()
  await router.push(returnPath.value)
}

async function loadEditor() {
  loading.value = true
  try {
    const response = await contentApi.createCoverEditorProject({
      asset_id: route.params.assetId,
      artifact_id: route.query.artifactId || null
    })
    project.value = response.project
    scene.value = clone(response.project.scene)
    selectedLayerId.value =
      [...scene.value.layers].sort(
        (left, right) => Number(right.font_size) - Number(left.font_size)
      )[0]?.id || ''
    resetHistory()
    const fontPromise = loadSceneFonts()
    const fileResponse = await contentApi.getCoverAssetFile(project.value.base_asset_id)
    const blob = await fileResponse.blob()
    backgroundUrl.value = URL.createObjectURL(blob)
    const image = new window.Image()
    await new Promise((resolve, reject) => {
      image.onload = resolve
      image.onerror = reject
      image.src = backgroundUrl.value
    })
    backgroundImage.value = image
    await fontPromise
    saveStatus.value = '已自动保存'
  } catch (error) {
    message.error(error.message || '封面编辑器加载失败')
  } finally {
    loading.value = false
    await nextTick()
    renderCanvas()
  }
}

onBeforeRouteLeave(async () => {
  if (saveStatus.value === '已自动保存') return true
  const saved = await saveNow()
  return saved || window.confirm('草稿保存失败，仍要离开编辑器吗？')
})

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
  void loadEditor()
})

onBeforeUnmount(() => {
  destroyed = true
  window.clearTimeout(saveTimer)
  window.removeEventListener('keydown', handleKeydown)
  if (backgroundUrl.value) URL.revokeObjectURL(backgroundUrl.value)
})
</script>

<template>
  <div class="cover-editor-page">
    <header class="editor-topbar">
      <div class="editor-topbar-left">
        <a-button type="text" @click="goBack"><ArrowLeft :size="18" />返回结果</a-button>
        <div class="editor-title">
          <strong>封面编辑</strong>
          <span v-if="project">{{
            project.editability === 'structured' ? '结构化图层' : '扁平底图'
          }}</span>
        </div>
      </div>
      <div class="editor-history-actions">
        <a-button type="text" :disabled="!canUndo" title="撤销 Ctrl+Z" @click="undo"
          ><Undo2 :size="18"
        /></a-button>
        <a-button type="text" :disabled="!canRedo" title="重做 Ctrl+Y" @click="redo"
          ><Redo2 :size="18"
        /></a-button>
      </div>
      <div class="editor-topbar-right">
        <span class="save-indicator" :class="{ saving, failed: saveStatus === '保存失败' }">
          <LoaderCircle v-if="saving" class="spin" :size="14" />
          <Save v-else :size="14" />{{ saveStatus }}
        </span>
        <a-button
          type="primary"
          size="large"
          :loading="applying"
          :disabled="loading || !project"
          @click="applyCover"
        >
          {{ applying ? `正在生成 ${renderProgress}%` : '应用为当前封面' }}
        </a-button>
      </div>
    </header>

    <div v-if="loading" class="editor-loading">
      <LoaderCircle class="spin" :size="30" />正在准备可编辑封面
    </div>
    <main v-else-if="scene" class="editor-workspace">
      <aside class="editor-layers-panel">
        <div class="panel-heading">
          <strong>图层</strong><span>{{ scene.layers.length }}</span>
        </div>
        <a-button block type="dashed" @click="addTextLayer"><Plus :size="16" />添加文字</a-button>
        <div class="layer-list">
          <button
            v-for="layer in [...scene.layers].sort((a, b) => b.order - a.order)"
            :key="layer.id"
            type="button"
            class="layer-item"
            :class="{ active: layer.id === selectedLayerId }"
            @click="selectLayer(layer)"
          >
            <Type :size="16" />
            <span
              ><strong>{{ layer.name }}</strong
              ><small v-if="layer.name !== layer.text">{{ layer.text || '空文字' }}</small></span
            >
            <span class="layer-actions">
              <i @click.stop="toggleLayer(layer, 'visible')">
                <Eye v-if="layer.visible" :size="14" /><EyeOff v-else :size="14" />
              </i>
              <i @click.stop="toggleLayer(layer, 'locked')">
                <Lock v-if="layer.locked" :size="14" /><LockOpen v-else :size="14" />
              </i>
            </span>
          </button>
        </div>
        <div v-if="selectedLayer" class="layer-footer-actions">
          <a-button size="small" title="上移" @click="moveLayer(selectedLayer.id, 1)"
            ><ChevronUp :size="14"
          /></a-button>
          <a-button size="small" title="下移" @click="moveLayer(selectedLayer.id, -1)"
            ><ChevronDown :size="14"
          /></a-button>
          <a-button size="small" title="复制" @click="duplicateLayer"><Copy :size="14" /></a-button>
          <a-button size="small" danger title="删除" @click="deleteLayer()"
            ><Trash2 :size="14"
          /></a-button>
        </div>
      </aside>

      <section class="editor-canvas-stage">
        <a-alert
          v-if="project?.warnings?.length"
          type="warning"
          show-icon
          :message="project.warnings[0]"
          class="editor-warning"
        />
        <div class="canvas-scroller">
          <div class="canvas-shell" :style="{ width: `${zoom}%` }">
            <canvas
              ref="canvasRef"
              aria-label="封面编辑画布"
              @pointerdown="onPointerDown"
              @pointermove="onPointerMove"
              @pointerup="onPointerUp"
              @pointercancel="onPointerUp"
              @dblclick="onCanvasDoubleClick"
            />
          </div>
        </div>
        <div class="canvas-zoom">
          <ImageIcon :size="15" />
          <a-slider v-model:value="zoom" :min="28" :max="82" :step="1" />
          <span>{{ zoom }}%</span>
          <small>{{ scene.canvas.width }} × {{ scene.canvas.height }} PNG</small>
        </div>
      </section>

      <aside class="editor-properties-panel">
        <div class="panel-heading">
          <strong>属性</strong><span>{{ selectedLayer ? '文字' : '未选择' }}</span>
        </div>
        <template v-if="selectedLayer">
          <label class="property-field full"
            ><span>文字内容</span
            ><a-textarea
              ref="textInputRef"
              :value="selectedLayer.text"
              :rows="4"
              @update:value="updateLayer('text', $event)"
          /></label>
          <label class="property-field full"
            ><span>图层名称</span
            ><a-input :value="selectedLayer.name" @update:value="updateLayer('name', $event)"
          /></label>
          <div class="property-grid">
            <label class="property-field"
              ><span>字体</span>
              <a-select
                :value="selectedLayer.font_family"
                @update:value="updateLayer('font_family', $event)"
              >
                <a-select-option value="Noto Sans CJK SC">现代黑体</a-select-option>
                <a-select-option value="Noto Serif CJK SC">典雅宋体</a-select-option>
              </a-select>
            </label>
            <label class="property-field"
              ><span>字重</span>
              <a-select
                :value="selectedLayer.font_weight"
                @update:value="updateLayer('font_weight', $event)"
              >
                <a-select-option :value="400">常规</a-select-option
                ><a-select-option :value="600">半粗</a-select-option>
                <a-select-option :value="700">粗体</a-select-option
                ><a-select-option :value="900">特粗</a-select-option>
              </a-select>
            </label>
            <label class="property-field"
              ><span>字号</span
              ><a-input-number
                :value="selectedLayer.font_size"
                :min="8"
                :max="512"
                @update:value="updateLayer('font_size', $event)"
            /></label>
            <label class="property-field"
              ><span>透明度</span
              ><a-input-number
                :value="selectedLayer.opacity"
                :min="0"
                :max="1"
                :step="0.05"
                @update:value="updateLayer('opacity', $event)"
            /></label>
            <label class="property-field"
              ><span>字间距</span
              ><a-input-number
                :value="selectedLayer.letter_spacing"
                :min="-20"
                :max="80"
                @update:value="updateLayer('letter_spacing', $event)"
            /></label>
            <label class="property-field"
              ><span>行高</span
              ><a-input-number
                :value="selectedLayer.line_height"
                :min="0.8"
                :max="3"
                :step="0.1"
                @update:value="updateLayer('line_height', $event)"
            /></label>
            <label class="property-field"
              ><span>旋转</span
              ><a-input-number
                :value="selectedLayer.rotation"
                :min="-180"
                :max="180"
                @update:value="updateLayer('rotation', $event)"
            /></label>
            <label class="property-field"
              ><span>对齐</span>
              <a-select :value="selectedLayer.align" @update:value="updateLayer('align', $event)">
                <a-select-option value="left">左对齐</a-select-option
                ><a-select-option value="center">居中</a-select-option
                ><a-select-option value="right">右对齐</a-select-option>
              </a-select>
            </label>
          </div>
          <section class="style-section">
            <div>
              <strong>文字颜色</strong
              ><input
                type="color"
                :value="selectedLayer.fill"
                @input="updateLayer('fill', $event.target.value)"
              />
            </div>
            <div v-if="selectedLayer.fill_runs?.length" class="multicolor-row">
              <span><strong>分段颜色</strong><small>修改主色会统一全部文字</small></span>
              <span class="color-runs">
                <label
                  v-for="(run, index) in selectedLayer.fill_runs"
                  :key="`${run.start}-${run.end}`"
                  :title="`第 ${run.start + 1}-${run.end} 个字符`"
                >
                  <input
                    type="color"
                    :value="run.fill"
                    @input="updateFillRun(index, $event.target.value)"
                  />
                  <small>{{ run.start + 1 }}-{{ run.end }}</small>
                </label>
              </span>
            </div>
            <div>
              <strong>描边</strong
              ><a-switch :checked="selectedLayer.stroke" @change="updateLayer('stroke', $event)" />
            </div>
            <div v-if="selectedLayer.stroke" class="style-subrow">
              <input
                type="color"
                :value="selectedLayer.stroke_color"
                @input="updateLayer('stroke_color', $event.target.value)"
              /><a-input-number
                :value="selectedLayer.stroke_width"
                :min="0"
                :max="40"
                @update:value="updateLayer('stroke_width', $event)"
              />
            </div>
            <div>
              <strong>阴影</strong
              ><a-switch :checked="selectedLayer.shadow" @change="updateLayer('shadow', $event)" />
            </div>
            <div v-if="selectedLayer.shadow" class="style-subrow shadow-controls">
              <input
                type="color"
                :value="selectedLayer.shadow_color"
                title="阴影颜色"
                @input="updateLayer('shadow_color', $event.target.value)"
              />
              <label
                ><span>模糊</span
                ><a-input-number
                  :value="selectedLayer.shadow_blur"
                  :min="0"
                  :max="80"
                  @update:value="updateLayer('shadow_blur', $event)"
              /></label>
              <label
                ><span>X</span
                ><a-input-number
                  :value="selectedLayer.shadow_offset_x"
                  :min="-100"
                  :max="100"
                  @update:value="updateLayer('shadow_offset_x', $event)"
              /></label>
              <label
                ><span>Y</span
                ><a-input-number
                  :value="selectedLayer.shadow_offset_y"
                  :min="-100"
                  :max="100"
                  @update:value="updateLayer('shadow_offset_y', $event)"
              /></label>
            </div>
            <div>
              <strong>文字底色</strong
              ><a-switch
                :checked="Boolean(selectedLayer.background_fill)"
                @change="updateLayer('background_fill', $event ? '#FFFFFF' : null)"
              />
            </div>
            <div v-if="selectedLayer.background_fill" class="style-subrow background-controls">
              <input
                type="color"
                :value="selectedLayer.background_fill"
                title="底色"
                @input="updateLayer('background_fill', $event.target.value)"
              />
              <label
                ><span>内边距</span
                ><a-input-number
                  :value="selectedLayer.background_padding"
                  :min="0"
                  :max="200"
                  @update:value="updateLayer('background_padding', $event)"
              /></label>
              <label
                ><span>圆角</span
                ><a-input-number
                  :value="selectedLayer.background_radius"
                  :min="0"
                  :max="200"
                  @update:value="updateLayer('background_radius', $event)"
              /></label>
              <label
                ><span>透明度</span
                ><a-input-number
                  :value="selectedLayer.background_opacity"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  @update:value="updateLayer('background_opacity', $event)"
              /></label>
            </div>
          </section>
          <section class="geometry-section">
            <strong>位置与尺寸</strong>
            <div class="property-grid">
              <label class="property-field"
                ><span>X</span
                ><a-input-number :value="selectedLayer.x" @update:value="updateLayer('x', $event)"
              /></label>
              <label class="property-field"
                ><span>Y</span
                ><a-input-number :value="selectedLayer.y" @update:value="updateLayer('y', $event)"
              /></label>
              <label class="property-field"
                ><span>宽度</span
                ><a-input-number
                  :value="selectedLayer.width"
                  :min="1"
                  @update:value="updateLayer('width', $event)"
              /></label>
              <label class="property-field"
                ><span>高度</span
                ><a-input-number
                  :value="selectedLayer.height"
                  :min="1"
                  @update:value="updateLayer('height', $event)"
              /></label>
            </div>
          </section>
        </template>
        <div v-else class="properties-empty">
          <Type :size="28" /><strong>选择一个文字图层</strong
          ><span>可在画布或左侧图层列表中选择</span>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped lang="less">
.cover-editor-page {
  position: fixed;
  inset: 0;
  z-index: 1200;
  height: 100vh;
  min-height: 680px;
  display: flex;
  flex-direction: column;
  background: var(--color-secondary-50);
  color: var(--color-text);
}
.editor-topbar {
  height: 64px;
  flex: 0 0 64px;
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 0 18px;
  background: var(--color-bg-container);
  border-bottom: 1px solid var(--color-secondary-100);
  box-shadow: 0 2px 10px var(--shadow-1);
  z-index: 2;
}
.editor-topbar-left,
.editor-topbar-right,
.editor-history-actions,
.save-indicator {
  display: flex;
  align-items: center;
  gap: 10px;
}
.editor-topbar-right {
  justify-content: flex-end;
}
.editor-title {
  display: flex;
  flex-direction: column;
}
.editor-title span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.save-indicator {
  color: var(--color-text-secondary);
  font-size: 13px;
}
.save-indicator.failed {
  color: var(--color-error-500);
}
.editor-workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 248px minmax(480px, 1fr) 318px;
}
.editor-layers-panel,
.editor-properties-panel {
  min-height: 0;
  overflow: auto;
  padding: 18px;
  background: var(--color-bg-container);
}
.editor-layers-panel {
  border-right: 1px solid var(--color-secondary-100);
}
.editor-properties-panel {
  border-left: 1px solid var(--color-secondary-100);
}
.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.panel-heading span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.layer-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 14px;
}
.layer-item {
  width: 100%;
  border: 1px solid transparent;
  border-radius: 8px;
  background: var(--color-secondary-10);
  padding: 10px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  text-align: left;
  cursor: pointer;
}
.layer-item.active {
  border-color: var(--color-primary-500);
  background: var(--color-primary-50);
}
.layer-item > span {
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.layer-item small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-tertiary);
}
.layer-actions {
  flex-direction: row !important;
  gap: 6px;
}
.layer-actions i {
  display: inline-flex;
  padding: 3px;
  border-radius: 4px;
}
.layer-actions i:hover {
  background: var(--color-secondary-100);
}
.layer-footer-actions {
  display: flex;
  gap: 6px;
  margin-top: 12px;
}
.editor-canvas-stage {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  background: #dfe2e8;
}
.editor-warning {
  margin: 12px 18px 0;
}
.canvas-scroller {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 28px;
}
.canvas-shell {
  min-width: 280px;
  max-width: 820px;
  box-shadow: 0 18px 48px rgba(24, 31, 49, 0.22);
  background: white;
  line-height: 0;
}
.canvas-shell canvas {
  width: 100%;
  height: auto;
  touch-action: none;
  cursor: default;
}
.canvas-zoom {
  height: 52px;
  flex: 0 0 52px;
  display: grid;
  grid-template-columns: auto 180px 45px 1fr;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  background: rgba(255, 255, 255, 0.94);
  border-top: 1px solid var(--color-secondary-100);
}
.canvas-zoom small {
  justify-self: end;
  color: var(--color-text-tertiary);
}
.property-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.property-field :deep(.ant-input-number),
.property-field :deep(.ant-select) {
  width: 100%;
}
.property-field.full {
  margin-bottom: 14px;
}
.property-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.style-section,
.geometry-section {
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--color-secondary-100);
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.style-section > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.style-section input[type='color'] {
  width: 42px;
  height: 28px;
  padding: 1px;
  border: 1px solid var(--color-secondary-100);
  border-radius: 6px;
  background: transparent;
}
.style-subrow {
  padding-left: 14px;
}
.style-subrow :deep(.ant-input-number) {
  width: 96px;
}
.geometry-section > strong {
  margin-bottom: 2px;
}
.properties-empty,
.editor-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--color-text-tertiary);
}
.editor-loading {
  height: 100%;
}
.spin {
  animation: spin 1s linear infinite;
}
.multicolor-row {
  align-items: flex-start !important;
  padding: 10px;
  border: 1px solid var(--color-secondary-100);
  border-radius: 8px;
  background: var(--color-secondary-10);
}
.multicolor-row > span:first-child {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.multicolor-row small {
  color: var(--color-text-tertiary);
  font-size: 11px;
}
.color-runs {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}
.color-runs label {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.color-runs input[type='color'] {
  width: 34px;
}
.shadow-controls,
.background-controls {
  display: grid !important;
  grid-template-columns: auto 1fr 1fr 1fr;
  align-items: end !important;
}
.shadow-controls label,
.background-controls label {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}
.shadow-controls :deep(.ant-input-number),
.background-controls :deep(.ant-input-number) {
  width: 100%;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 1100px) {
  .editor-workspace {
    grid-template-columns: 210px minmax(420px, 1fr) 280px;
  }
  .editor-topbar {
    grid-template-columns: auto 1fr auto;
  }
  .save-indicator {
    display: none;
  }
}
@media (max-width: 820px) {
  .cover-editor-page {
    height: auto;
    min-height: calc(100vh - 56px);
  }
  .editor-workspace {
    display: flex;
    flex-direction: column;
  }
  .editor-layers-panel,
  .editor-properties-panel {
    border: 0;
    border-bottom: 1px solid var(--color-secondary-100);
    max-height: none;
  }
  .editor-canvas-stage {
    min-height: 620px;
    order: -1;
  }
  .editor-title span,
  .editor-history-actions {
    display: none;
  }
  .editor-topbar {
    padding: 0 8px;
  }
  .editor-topbar-right .ant-btn {
    font-size: 12px;
    padding-inline: 10px;
  }
}
</style>
