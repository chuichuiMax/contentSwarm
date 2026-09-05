<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { AlertTriangle, Check, Eye, EyeOff, Plus, RefreshCw, Save, Trash2 } from 'lucide-vue-next'

import { contentApi } from '@/apis/content_api'
import {
  buildPosterReviewPayload,
  isLowConfidenceLayer,
  updateReviewBox
} from '@/utils/posterOcrReview'

const props = defineProps({
  open: { type: Boolean, default: false },
  item: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'confirmed', 'saved'])

const loading = ref(false)
const saving = ref(false)
const reanalyzing = ref(false)
const template = ref(null)
const layers = ref([])
const selectedLayerId = ref('')
const showRaw = ref(false)
const canvasRef = ref(null)
let pointerState = null

const selectedLayer = computed(
  () => layers.value.find((item) => item.id === selectedLayerId.value) || null
)
const rawLayers = computed(() => template.value?.ocr_raw_layers || [])
const metrics = computed(() => template.value?.recognition_metrics || {})
const lowConfidenceCount = computed(() => layers.value.filter(isLowConfidence).length)
const canConfirm = computed(
  () => Boolean(template.value?.product_box) && !saving.value && !reanalyzing.value
)

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function loadTemplateState(value) {
  template.value = value
  layers.value = clone(value?.text_slots || [])
  selectedLayerId.value = layers.value[0]?.id || ''
}

async function fetchTemplate() {
  if (!props.open || !props.item?.poster_template_id) return
  loading.value = true
  try {
    const response = await contentApi.getCoverPosterTemplate(props.item.poster_template_id)
    loadTemplateState(response.template)
  } catch (error) {
    message.error(error.message || 'OCR 识别结果加载失败')
    emit('update:open', false)
  } finally {
    loading.value = false
  }
}

function isLowConfidence(layer) {
  return isLowConfidenceLayer(layer)
}

function confidenceLabel(layer) {
  if (
    layer.review_state === 'user_added' ||
    layer.confidence === null ||
    layer.confidence === undefined
  )
    return '人工添加'
  return `${Math.round(Number(layer.confidence || 0) * 100)}%`
}

function boxStyle(box) {
  return {
    left: `${box.x * 100}%`,
    top: `${box.y * 100}%`,
    width: `${box.width * 100}%`,
    height: `${box.height * 100}%`
  }
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value))
}

function markEdited(layer) {
  if (layer.review_state !== 'user_added') layer.review_state = 'user_edited'
}

function addLayer() {
  const id = `manual-${Date.now().toString(36)}`
  const layer = {
    id,
    role: 'other',
    source_text: '新增文字',
    editable: true,
    box: { x: 0.2, y: 0.42, width: 0.6, height: 0.08 },
    style: {
      fill: '#FFFFFF',
      fill_runs: [],
      stroke: '#171717',
      stroke_width_ratio: 0.02,
      font_size_ratio: 0.05,
      bold: true,
      align: 'center',
      panel_fill: null,
      panel_opacity: 1,
      panel_radius_ratio: 0.22
    },
    max_chars: 60,
    max_lines: 2,
    confidence: null,
    candidate_count: 0,
    consensus_count: 0,
    source_variant: 'manual',
    alternatives: [],
    review_state: 'user_added'
  }
  layers.value.push(layer)
  selectedLayerId.value = id
}

function removeLayer(layer) {
  layers.value = layers.value.filter((item) => item.id !== layer.id)
  if (selectedLayerId.value === layer.id) selectedLayerId.value = layers.value[0]?.id || ''
}

function useAlternative(layer, alternative) {
  layer.source_text = alternative
  markEdited(layer)
}

function updateBoxNumber(layer, key, value) {
  layer.box = updateReviewBox(layer.box, key, value)
  markEdited(layer)
}

function updateTextFill(layer, value) {
  layer.style.fill = value.toUpperCase()
  layer.style.fill_runs = []
  markEdited(layer)
}

function togglePanelFill(layer, enabled) {
  layer.style.panel_fill = enabled ? layer.style.panel_fill || '#FFFFFF' : null
  layer.style.panel_opacity = 1
  markEdited(layer)
}

function updatePanelFill(layer, value) {
  layer.style.panel_fill = value.toUpperCase()
  layer.style.panel_opacity = 1
  markEdited(layer)
}

function startPointer(event, layer, mode) {
  if (!canvasRef.value) return
  event.preventDefault()
  event.stopPropagation()
  selectedLayerId.value = layer.id
  const rect = canvasRef.value.getBoundingClientRect()
  pointerState = {
    layer,
    mode,
    startX: event.clientX,
    startY: event.clientY,
    canvasWidth: rect.width,
    canvasHeight: rect.height,
    box: { ...layer.box }
  }
  window.addEventListener('pointermove', movePointer)
  window.addEventListener('pointerup', stopPointer, { once: true })
}

function movePointer(event) {
  if (!pointerState) return
  const dx = (event.clientX - pointerState.startX) / pointerState.canvasWidth
  const dy = (event.clientY - pointerState.startY) / pointerState.canvasHeight
  const { layer, box, mode } = pointerState
  if (mode === 'move') {
    layer.box.x = clamp(box.x + dx, 0, 1 - box.width)
    layer.box.y = clamp(box.y + dy, 0, 1 - box.height)
  } else {
    layer.box.width = clamp(box.width + dx, 0.01, 1 - box.x)
    layer.box.height = clamp(box.height + dy, 0.01, 1 - box.y)
  }
  markEdited(layer)
}

function stopPointer() {
  pointerState = null
  window.removeEventListener('pointermove', movePointer)
}

async function saveReview(confirm) {
  if (!template.value?.product_box) return message.warning('请先完成产品图片底层区域标注')
  if (layers.value.some((item) => !item.source_text.trim()))
    return message.warning('文字图层内容不能为空')
  saving.value = true
  try {
    const response = await contentApi.reviewCoverPosterTemplate(
      template.value.id,
      buildPosterReviewPayload(template.value, layers.value, confirm)
    )
    loadTemplateState(response.template)
    message.success(confirm ? 'OCR 图层已确认，模板已启用' : '校对草稿已保存')
    emit(confirm ? 'confirmed' : 'saved', response.template)
    if (confirm) emit('update:open', false)
  } catch (error) {
    message.error(error.message || 'OCR 校对结果保存失败')
    if (String(error.message || '').includes('刷新')) await fetchTemplate()
  } finally {
    saving.value = false
  }
}

async function reanalyze() {
  reanalyzing.value = true
  try {
    const response = await contentApi.analyzeCoverPosterTemplate(template.value.id)
    loadTemplateState(response.template)
    message.success('高精度 OCR 已重新识别，请继续校对')
  } catch (error) {
    message.error(error.message || '重新识别失败')
  } finally {
    reanalyzing.value = false
  }
}

watch(() => [props.open, props.item?.poster_template_id], fetchTemplate, { immediate: true })
onBeforeUnmount(stopPointer)
</script>

<template>
  <a-modal
    :open="open"
    width="min(1180px, 96vw)"
    :footer="null"
    :mask-closable="false"
    title="OCR 识别结果校对"
    wrap-class-name="poster-ocr-review-modal"
    @cancel="$emit('update:open', false)"
  >
    <a-spin :spinning="loading">
      <div v-if="template" class="review-shell">
        <header class="review-summary">
          <div>
            <strong>{{ template.name }}</strong>
            <span
              >识别到 {{ layers.length }} 个文字图层 · 原始候选
              {{ metrics.raw_layer_count ?? rawLayers.length }} 个</span
            >
          </div>
          <div class="quality-summary" :class="{ warning: lowConfidenceCount }">
            <AlertTriangle v-if="lowConfidenceCount" :size="16" />
            <Check v-else :size="16" />
            <span>{{
              lowConfidenceCount
                ? `${lowConfidenceCount} 层需要重点核对`
                : '当前图层均已通过置信度检查'
            }}</span>
          </div>
        </header>

        <div class="review-workspace">
          <section class="canvas-column">
            <div class="canvas-toolbar">
              <span>点击文字框选择，拖动调整位置，右下角调整大小</span>
              <button type="button" @click="showRaw = !showRaw">
                <EyeOff v-if="showRaw" :size="14" /><Eye v-else :size="14" />{{
                  showRaw ? '隐藏原始候选' : '显示原始候选'
                }}
              </button>
            </div>
            <div ref="canvasRef" class="ocr-canvas" @pointerdown="selectedLayerId = ''">
              <img :src="item?.previewUrl" :alt="template.name" draggable="false" />
              <span class="background-badge">产品图片：全画布底层</span>
              <span
                v-for="raw in showRaw ? rawLayers : []"
                :key="raw.id"
                class="raw-box"
                :style="boxStyle(raw.box)"
                :title="`${raw.text} · ${Math.round(Number(raw.confidence || 0) * 100)}%`"
              />
              <button
                v-for="layer in layers"
                :key="layer.id"
                type="button"
                class="ocr-box"
                :class="{ active: selectedLayerId === layer.id, warning: isLowConfidence(layer) }"
                :style="boxStyle(layer.box)"
                @pointerdown="startPointer($event, layer, 'move')"
              >
                <span>{{ layer.source_text }}</span>
                <i @pointerdown="startPointer($event, layer, 'resize')" />
              </button>
            </div>
          </section>

          <aside class="layer-column">
            <div class="layer-head">
              <div><strong>文字图层</strong><small>直接显示真实识别文字</small></div>
              <button type="button" @click="addLayer"><Plus :size="14" />补充漏识别文字</button>
            </div>
            <div v-if="layers.length" class="layer-list">
              <article
                v-for="(layer, index) in layers"
                :key="layer.id"
                :class="{ active: selectedLayerId === layer.id, warning: isLowConfidence(layer) }"
                @click="selectedLayerId = layer.id"
              >
                <span class="layer-index">{{ index + 1 }}</span>
                <div>
                  <a-input
                    v-model:value="layer.source_text"
                    maxlength="240"
                    @input="markEdited(layer)"
                  />
                  <p>
                    <em>{{ confidenceLabel(layer) }}</em>
                    <span>多路共识 {{ layer.consensus_count || 0 }}</span>
                    <span v-if="layer.review_state === 'user_edited'">已人工修正</span>
                  </p>
                  <div v-if="layer.alternatives?.length" class="alternatives">
                    <span>候选：</span>
                    <button
                      v-for="text in layer.alternatives"
                      :key="text"
                      type="button"
                      @click.stop="useAlternative(layer, text)"
                    >
                      {{ text }}
                    </button>
                  </div>
                  <div class="recognized-colors">
                    <span
                      v-for="run in layer.style.fill_runs || []"
                      :key="`${run.start}-${run.end}-${run.fill}`"
                      :style="{ background: run.fill }"
                      :title="`文字 ${run.start + 1}-${run.end}：${run.fill}`"
                    />
                    <span
                      v-if="!layer.style.fill_runs?.length"
                      :style="{ background: layer.style.fill }"
                      :title="`文字颜色：${layer.style.fill}`"
                    />
                    <span
                      v-if="layer.style.panel_fill"
                      class="panel-swatch"
                      :style="{ background: layer.style.panel_fill }"
                      :title="`文字底色：${layer.style.panel_fill}`"
                    />
                    <small>{{ layer.style.fill_runs?.length > 1 ? '已识别多色文字' : '文字颜色' }}</small>
                    <small v-if="layer.style.panel_fill">· 已识别文字底色</small>
                  </div>
                </div>
                <button
                  type="button"
                  class="delete-layer"
                  title="删除误识别图层"
                  @click.stop="removeLayer(layer)"
                >
                  <Trash2 :size="14" />
                </button>
              </article>
            </div>
            <div v-else class="empty-layers">
              没有识别到文字。请补充漏识别图层，或确认这是无文字模板。
            </div>

            <div v-if="selectedLayer" class="position-editor">
              <strong>选中图层位置</strong>
              <div class="position-grid">
                <label v-for="key in ['x', 'y', 'width', 'height']" :key="key">
                  <span>{{ { x: 'X', y: 'Y', width: '宽', height: '高' }[key] }}</span>
                  <a-input-number
                    :value="Number(selectedLayer.box[key].toFixed(4))"
                    :min="0"
                    :max="1"
                    :step="0.001"
                    @change="updateBoxNumber(selectedLayer, key, $event)"
                  />
                </label>
              </div>
              <label class="editable-toggle"
                ><a-switch
                  v-model:checked="selectedLayer.editable"
                  @change="markEdited(selectedLayer)"
                />生成内容时允许替换这层文字</label
              >
              <div class="color-editor">
                <strong>识别样式</strong>
                <label>
                  <span>文字主色</span>
                  <input
                    type="color"
                    :value="selectedLayer.style.fill"
                    @input="updateTextFill(selectedLayer, $event.target.value)"
                  />
                  <code>{{ selectedLayer.style.fill }}</code>
                </label>
                <div v-if="selectedLayer.style.fill_runs?.length > 1" class="fill-run-row">
                  <span>多色分段</span>
                  <i
                    v-for="run in selectedLayer.style.fill_runs"
                    :key="`${run.start}-${run.end}`"
                    :style="{ background: run.fill }"
                    :title="`${run.start + 1}-${run.end} 字：${run.fill}`"
                  />
                </div>
                <label>
                  <span>文字底色</span>
                  <a-switch
                    :checked="Boolean(selectedLayer.style.panel_fill)"
                    @change="togglePanelFill(selectedLayer, $event)"
                  />
                  <input
                    v-if="selectedLayer.style.panel_fill"
                    type="color"
                    :value="selectedLayer.style.panel_fill"
                    @input="updatePanelFill(selectedLayer, $event.target.value)"
                  />
                  <code v-if="selectedLayer.style.panel_fill">{{ selectedLayer.style.panel_fill }}</code>
                </label>
              </div>
            </div>
          </aside>
        </div>

        <footer class="review-actions">
          <div>
            <button type="button" :disabled="reanalyzing || saving" @click="reanalyze">
              <RefreshCw :size="15" />{{ reanalyzing ? '正在重新识别…' : '重新高精度识别' }}
            </button>
            <span>未确认前模板不会进入内容生产工作流。</span>
          </div>
          <div>
            <a-button @click="$emit('update:open', false)">稍后处理</a-button>
            <a-button :loading="saving" class="lucide-icon-btn" @click="saveReview(false)"
              ><Save :size="15" />保存草稿</a-button
            >
            <a-button
              type="primary"
              :loading="saving"
              :disabled="!canConfirm"
              class="lucide-icon-btn"
              @click="saveReview(true)"
              ><Check :size="15" />确认图层并启用</a-button
            >
          </div>
        </footer>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped lang="less">
.review-shell {
  display: grid;
  gap: 14px;
  color: var(--color-text);
}
.review-summary,
.review-actions,
.canvas-toolbar,
.layer-head,
.quality-summary {
  display: flex;
  align-items: center;
}
.review-summary {
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--gray-25);
}
.review-summary > div:first-child {
  display: grid;
  gap: 3px;
}
.review-summary span,
.layer-head small {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.quality-summary {
  gap: 6px;
  color: var(--color-success-700);
}
.quality-summary.warning {
  color: var(--color-warning-900);
}
.review-workspace {
  min-height: 610px;
  display: grid;
  grid-template-columns: minmax(420px, 1.2fr) minmax(350px, 0.8fr);
  gap: 14px;
}
.canvas-column,
.layer-column {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 9px;
  background: var(--gray-25);
}
.canvas-column {
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 10px;
}
.canvas-toolbar {
  justify-content: space-between;
  gap: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.canvas-toolbar button,
.layer-head button,
.review-actions button {
  border: 0;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--main-700);
  background: transparent;
  cursor: pointer;
}
.ocr-canvas {
  position: relative;
  height: min(68vh, 610px);
  aspect-ratio: 3 / 4;
  margin: 0 auto;
  overflow: hidden;
  border: 1px solid var(--gray-200);
  border-radius: 7px;
  background: var(--gray-100);
  user-select: none;
  touch-action: none;
}
.ocr-canvas > img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: fill;
  pointer-events: none;
}
.background-badge {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 4px 7px;
  border-radius: 5px;
  color: var(--main-0);
  background: var(--dark-70);
  font-size: 10px;
  pointer-events: none;
}
.ocr-box,
.raw-box {
  position: absolute;
  box-sizing: border-box;
}
.raw-box {
  border: 1px dashed var(--color-warning-700);
  background: color-mix(in srgb, var(--color-warning-100) 22%, transparent);
  pointer-events: none;
}
.ocr-box {
  padding: 0;
  border: 1.5px solid var(--main-500);
  color: var(--main-0);
  background: color-mix(in srgb, var(--main-500) 12%, transparent);
  cursor: move;
}
.ocr-box.warning {
  border-color: var(--color-warning-700);
  background: color-mix(in srgb, var(--color-warning-100) 20%, transparent);
}
.ocr-box.active {
  z-index: 3;
  box-shadow: 0 0 0 2px var(--main-100);
}
.ocr-box span {
  position: absolute;
  left: 0;
  top: -20px;
  max-width: 180px;
  padding: 2px 5px;
  overflow: hidden;
  border-radius: 3px;
  color: var(--main-0);
  background: var(--main-700);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ocr-box i {
  position: absolute;
  right: -5px;
  bottom: -5px;
  width: 10px;
  height: 10px;
  border: 1px solid var(--main-0);
  border-radius: 2px;
  background: var(--main-700);
  cursor: nwse-resize;
}
.layer-column {
  display: grid;
  grid-template-rows: auto minmax(240px, 1fr) auto;
  gap: 10px;
  overflow: hidden;
}
.layer-head {
  justify-content: space-between;
  gap: 10px;
}
.layer-head > div {
  display: grid;
  gap: 2px;
}
.layer-list {
  overflow-y: auto;
  display: grid;
  gap: 7px;
  align-content: start;
  padding-right: 3px;
}
.layer-list article {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) 28px;
  gap: 7px;
  padding: 8px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-0);
  cursor: pointer;
}
.layer-list article.active {
  border-color: var(--main-500);
  box-shadow: 0 0 0 2px var(--main-50);
}
.layer-list article.warning {
  border-left: 3px solid var(--color-warning-700);
}
.layer-index {
  width: 22px;
  height: 22px;
  display: grid;
  place-content: center;
  border-radius: 50%;
  color: var(--gray-600);
  background: var(--gray-100);
  font-size: 11px;
}
.layer-list article > div {
  min-width: 0;
}
.layer-list p {
  margin: 5px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 5px 9px;
  color: var(--color-text-tertiary);
  font-size: 10px;
}
.layer-list em {
  color: var(--main-700);
  font-style: normal;
}
.layer-list article.warning em {
  color: var(--color-warning-900);
}
.delete-layer {
  border: 0;
  color: var(--color-error-700);
  background: transparent;
  cursor: pointer;
}
.alternatives {
  margin-top: 5px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  color: var(--color-text-tertiary);
  font-size: 10px;
}
.alternatives button {
  padding: 2px 5px;
  border: 1px solid var(--gray-150);
  border-radius: 4px;
  color: var(--main-700);
  background: var(--gray-25);
  cursor: pointer;
}
.recognized-colors,
.fill-run-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  color: var(--color-text-tertiary);
}
.recognized-colors > span,
.fill-run-row i {
  width: 15px;
  height: 15px;
  border: 1px solid var(--gray-200);
  border-radius: 4px;
  box-shadow: inset 0 0 0 1px var(--gray-0);
}
.recognized-colors .panel-swatch {
  margin-left: 4px;
  border-radius: 2px;
}
.recognized-colors small {
  font-size: 10px;
}
.empty-layers {
  padding: 24px;
  border: 1px dashed var(--gray-200);
  border-radius: 7px;
  color: var(--color-text-secondary);
  text-align: center;
}
.position-editor {
  padding-top: 10px;
  border-top: 1px solid var(--gray-150);
  display: grid;
  gap: 8px;
}
.position-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}
.position-editor label {
  display: grid;
  gap: 3px;
  color: var(--color-text-secondary);
  font-size: 10px;
}
.position-editor :deep(.ant-input-number) {
  width: 100%;
}
.editable-toggle {
  display: flex !important;
  align-items: center;
  gap: 7px;
}
.color-editor {
  display: grid;
  gap: 7px;
  padding-top: 8px;
  border-top: 1px dashed var(--gray-150);
}
.color-editor > label,
.fill-run-row {
  display: flex;
  align-items: center;
  gap: 7px;
}
.color-editor > label > span,
.fill-run-row > span {
  min-width: 58px;
  color: var(--color-text-secondary);
  font-size: 10px;
}
.color-editor input[type='color'] {
  width: 34px;
  height: 25px;
  padding: 1px;
  border: 1px solid var(--gray-150);
  border-radius: 5px;
  background: transparent;
}
.color-editor code {
  color: var(--color-text-tertiary);
  font-size: 10px;
}
.review-actions {
  justify-content: space-between;
  gap: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--gray-150);
}
.review-actions > div {
  display: flex;
  align-items: center;
  gap: 9px;
}
.review-actions span {
  color: var(--color-text-secondary);
  font-size: 11px;
}
@media (max-width: 900px) {
  .review-workspace {
    grid-template-columns: 1fr;
  }
  .ocr-canvas {
    height: auto;
    width: min(100%, 420px);
  }
  .layer-column {
    max-height: 600px;
  }
  .review-actions,
  .review-summary {
    align-items: stretch;
    flex-direction: column;
  }
  .review-actions > div {
    flex-wrap: wrap;
  }
}
</style>
