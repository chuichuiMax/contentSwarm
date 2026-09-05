<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Image as ImageIcon, Layers3 } from 'lucide-vue-next'

import { calculatePosterImagePlacement } from '@/utils/posterCanvasPreview'

const props = defineProps({
  templateUrl: { type: String, default: '' },
  backgroundUrl: { type: String, default: '' },
  precisePreviewUrl: { type: String, default: '' },
  templateType: { type: String, default: 'alpha_overlay' },
  productBox: { type: Object, default: null },
  transform: { type: Object, default: () => ({}) },
  canvasWidth: { type: Number, default: 1080 },
  canvasHeight: { type: Number, default: 1440 }
})

const canvasRef = ref(null)
const rendering = ref(false)
const renderFailed = ref(false)
let renderVersion = 0

const hasComposition = computed(() => Boolean(props.templateUrl && props.backgroundUrl))
const previewLabel = computed(() => props.precisePreviewUrl ? '后端精确预览' : '实时构图参考')

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = reject
    image.src = url
  })
}

async function renderCanvas() {
  const version = ++renderVersion
  renderFailed.value = false
  if (!hasComposition.value || props.precisePreviewUrl) return
  rendering.value = true
  try {
    const [template, background] = await Promise.all([
      loadImage(props.templateUrl),
      loadImage(props.backgroundUrl)
    ])
    if (version !== renderVersion) return
    await nextTick()
    const canvas = canvasRef.value
    if (!canvas) return
    const context = canvas.getContext('2d')
    context.clearRect(0, 0, props.canvasWidth, props.canvasHeight)
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, props.canvasWidth, props.canvasHeight)

    if (props.templateType !== 'alpha_overlay') {
      context.drawImage(template, 0, 0, props.canvasWidth, props.canvasHeight)
    }

    const placement = calculatePosterImagePlacement({
      imageWidth: background.naturalWidth,
      imageHeight: background.naturalHeight,
      canvasWidth: props.canvasWidth,
      canvasHeight: props.canvasHeight,
      box: props.productBox,
      transform: props.transform
    })
    context.save()
    context.beginPath()
    context.rect(placement.left, placement.top, placement.width, placement.height)
    context.clip()
    context.imageSmoothingEnabled = true
    context.imageSmoothingQuality = 'high'
    context.drawImage(
      background,
      placement.imageX,
      placement.imageY,
      placement.imageWidth,
      placement.imageHeight
    )
    context.restore()

    if (props.templateType === 'alpha_overlay') {
      context.drawImage(template, 0, 0, props.canvasWidth, props.canvasHeight)
    }
  } catch {
    if (version === renderVersion) renderFailed.value = true
  } finally {
    if (version === renderVersion) rendering.value = false
  }
}

watch(
  () => [
    props.templateUrl,
    props.backgroundUrl,
    props.precisePreviewUrl,
    props.templateType,
    JSON.stringify(props.productBox || {}),
    JSON.stringify(props.transform || {})
  ],
  renderCanvas,
  { immediate: true }
)

onBeforeUnmount(() => { renderVersion += 1 })
</script>

<template>
  <div class="poster-canvas-preview">
    <header>
      <span><Layers3 :size="15" />{{ previewLabel }}</span>
      <small>{{ canvasWidth }} × {{ canvasHeight }}</small>
    </header>
    <div class="canvas-shell">
      <img
        v-if="precisePreviewUrl"
        :src="precisePreviewUrl"
        alt="后端生成的大字报精确预览"
      />
      <canvas
        v-else-if="hasComposition"
        ref="canvasRef"
        :width="canvasWidth"
        :height="canvasHeight"
        aria-label="素材图片与封面蒙版实时合成预览"
      />
      <div v-else class="canvas-empty">
        <ImageIcon :size="28" />
        <strong>{{ templateUrl ? '请选择素材库底图' : '请先选择封面模板' }}</strong>
        <span>选择完成后会在这里显示完整大字报构图</span>
      </div>
      <div v-if="rendering" class="canvas-status">正在更新构图…</div>
      <div v-else-if="renderFailed" class="canvas-status error">预览加载失败，请重新选择素材</div>
    </div>
    <p v-if="!precisePreviewUrl && hasComposition">实时画布用于调整底图构图，文字替换和最终像素效果以后端精确预览为准。</p>
  </div>
</template>

<style scoped lang="less">
.poster-canvas-preview { min-width: 0; display: grid; gap: 8px; }
.poster-canvas-preview header { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--color-text-secondary); font-size: 12px; }
.poster-canvas-preview header span { display: inline-flex; align-items: center; gap: 6px; color: var(--color-text); font-weight: 600; }
.poster-canvas-preview header small { color: var(--color-text-tertiary); }
.canvas-shell { position: relative; width: min(100%, 360px); aspect-ratio: 3 / 4; margin: 0 auto; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; background-color: var(--gray-50); background-image: linear-gradient(45deg, var(--gray-100) 25%, transparent 25%), linear-gradient(-45deg, var(--gray-100) 25%, transparent 25%), linear-gradient(45deg, transparent 75%, var(--gray-100) 75%), linear-gradient(-45deg, transparent 75%, var(--gray-100) 75%); background-position: 0 0, 0 8px, 8px -8px, -8px 0; background-size: 16px 16px; }
.canvas-shell canvas, .canvas-shell > img { display: block; width: 100%; height: 100%; object-fit: contain; background: var(--gray-0); }
.canvas-empty { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center; gap: 7px; padding: 24px; color: var(--color-text-secondary); background: color-mix(in srgb, var(--gray-0) 88%, transparent); text-align: center; }
.canvas-empty strong { color: var(--color-text); font-size: 13px; }
.canvas-empty span { max-width: 220px; font-size: 11px; line-height: 1.6; }
.canvas-status { position: absolute; right: 8px; bottom: 8px; left: 8px; padding: 7px 9px; border-radius: 6px; color: var(--main-0); background: var(--dark-70); text-align: center; font-size: 11px; }
.canvas-status.error { background: var(--color-error-700); }
.poster-canvas-preview > p { margin: 0; color: var(--color-text-tertiary); font-size: 11px; line-height: 1.6; }
</style>
