<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  AlertCircle,
  FileImage,
  ImageUp,
  LoaderCircle,
  RefreshCw,
  Save,
  ScanText,
  X
} from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'

const props = defineProps({
  taskId: { type: String, default: '' }
})

const open = defineModel('open', { type: Boolean, default: false })
const records = ref([])
const selectedId = ref('')
const draftText = ref('')
const previewUrl = ref('')
const loading = ref(false)
const uploading = ref(false)
const saving = ref(false)
const retrying = ref(false)
const isDragging = ref(false)

const MAX_UPLOAD_SIZE_MB = 50
const MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
const SUPPORTED_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'tif', 'tiff']

const selected = computed(() => records.value.find((item) => item.id === selectedId.value) || null)
const averageConfidence = computed(() => {
  const scores = (selected.value?.blocks || [])
    .map((item) => item.confidence)
    .filter((value) => typeof value === 'number')
  if (!scores.length) return null
  return `${(scores.reduce((sum, value) => sum + value, 0) / scores.length * 100).toFixed(1)}%`
})
const hasChanges = computed(
  () => selected.value?.status === 'completed' && draftText.value !== selected.value.effective_text
)

const statusLabel = (status) => ({
  processing: '识别中',
  completed: '已完成',
  failed: '识别失败'
}[status] || status)

const formatSize = (size) => {
  if (!size) return '0 KB'
  return size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`
}

const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : ''

const clearPreview = () => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

const loadPreview = async (record) => {
  clearPreview()
  if (!record) return
  try {
    const response = await contentApi.getOcrImage(record.id)
    previewUrl.value = URL.createObjectURL(await response.blob())
  } catch (error) {
    message.error(error.message || '原图加载失败')
  }
}

const selectRecord = async (record) => {
  selectedId.value = record.id
  draftText.value = record.effective_text || ''
  await loadPreview(record)
}

const loadRecords = async () => {
  if (!props.taskId) return
  loading.value = true
  try {
    const response = await contentApi.listOcrResults(props.taskId)
    records.value = response.items || []
    const current = records.value.find((item) => item.id === selectedId.value) || records.value[0]
    if (current) await selectRecord(current)
    else {
      selectedId.value = ''
      draftText.value = ''
      clearPreview()
    }
  } catch (error) {
    message.error(error.message || 'OCR 记录加载失败')
  } finally {
    loading.value = false
  }
}

const isSupportedImage = (file) => {
  const extension = file.name?.split('.').pop()?.toLowerCase()
  return SUPPORTED_IMAGE_EXTENSIONS.includes(extension) && (!file.type || file.type.startsWith('image/'))
}

const uploadFile = async (file) => {
  if (!file || !isSupportedImage(file)) {
    message.error('请选择 JPG、PNG、WebP、BMP 或 TIFF 图片')
    return
  }
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    message.error(`图片大小不能超过 ${MAX_UPLOAD_SIZE_MB} MB`)
    return
  }
  uploading.value = true
  try {
    const response = await contentApi.createOcrResult(props.taskId, file)
    const record = response.item
    records.value = [record, ...records.value.filter((item) => item.id !== record.id)]
    await selectRecord(record)
    if (record.status === 'completed') message.success('图片识别完成，结果已保存')
    else message.error(record.error_message || '图片识别失败，失败记录已保存')
  } catch (error) {
    message.error(error.message || '图片上传失败')
  } finally {
    uploading.value = false
  }
}

const beforeUpload = (file) => {
  void uploadFile(file)
  return false
}

const handleDragEnter = (event) => {
  if (Array.from(event.dataTransfer?.types || []).includes('Files')) isDragging.value = true
}

const handleDrop = (event) => {
  isDragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) void uploadFile(file)
}

const saveCorrection = async () => {
  if (!selected.value) return
  saving.value = true
  try {
    const response = await contentApi.updateOcrResult(selected.value.id, { corrected_text: draftText.value })
    const record = response.item
    records.value = records.value.map((item) => item.id === record.id ? record : item)
    draftText.value = record.effective_text
    message.success('校对结果已保存，原始识别文本保持不变')
  } catch (error) {
    message.error(error.message || '校对结果保存失败')
  } finally {
    saving.value = false
  }
}

const retryRecognition = async () => {
  if (!selected.value) return
  retrying.value = true
  try {
    const response = await contentApi.retryOcrResult(selected.value.id)
    const record = response.item
    records.value = records.value.map((item) => item.id === record.id ? record : item)
    draftText.value = record.effective_text || ''
    if (record.status === 'completed') message.success('重新识别完成')
    else message.error(record.error_message || '重新识别失败')
  } catch (error) {
    message.error(error.message || '重新识别失败')
  } finally {
    retrying.value = false
  }
}

watch(open, (value) => {
  if (value) void loadRecords()
  else clearPreview()
})

watch(() => props.taskId, () => {
  records.value = []
  selectedId.value = ''
  clearPreview()
  if (open.value) void loadRecords()
})

onBeforeUnmount(clearPreview)
</script>

<template>
  <a-modal
    v-model:open="open"
    class="ocr-fullscreen-modal"
    root-class-name="ocr-fullscreen-root"
    :width="'100vw'"
    :centered="false"
    :style="{ top: 0, paddingBottom: 0 }"
    :body-style="{ height: '100vh', padding: 0 }"
    :footer="null"
    :closable="false"
    :mask-closable="false"
    :destroy-on-close="false"
    wrap-class-name="ocr-fullscreen-modal"
  >
    <div
      class="ocr-shell"
      @dragenter.prevent="handleDragEnter"
      @dragover.prevent
      @dragleave.prevent="isDragging = false"
      @drop.prevent="handleDrop"
    >
      <div v-if="isDragging" class="ocr-drop-overlay">
        <div class="ocr-drop-message">
          <ImageUp :size="30" />
          <strong>松开鼠标上传图片</strong>
          <span>支持 JPG、PNG、WebP、BMP 和 TIFF，单张不超过 {{ MAX_UPLOAD_SIZE_MB }} MB</span>
        </div>
      </div>
      <header class="ocr-toolbar">
        <div class="ocr-toolbar-title">
          <span class="ocr-title-icon"><ScanText :size="21" /></span>
          <div>
            <h2>图片 OCR 识别</h2>
            <p>原图与校对结果并排展示，方便逐项核对；原始识别结果始终保留。</p>
          </div>
        </div>
        <div class="ocr-toolbar-actions">
          <a-upload
            accept=".jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff"
            :show-upload-list="false"
            :before-upload="beforeUpload"
            :disabled="uploading"
          >
            <a-button type="primary" :loading="uploading">
              <ImageUp :size="16" />上传并识别
            </a-button>
          </a-upload>
          <a-button class="ocr-close-button" @click="open = false">
            <X :size="17" />关闭
          </a-button>
        </div>
      </header>

      <div class="ocr-body">
        <div v-if="loading" class="ocr-loading">
          <LoaderCircle class="spin" :size="26" />正在加载 OCR 记录
        </div>
        <div v-else-if="!records.length" class="ocr-empty">
          <FileImage :size="42" />
          <strong>当前任务还没有识别图片</strong>
          <span>点击右上角“上传并识别”，也可以将图片拖入全屏区域；支持 JPG、PNG、WebP、BMP 和 TIFF，单张不超过 {{ MAX_UPLOAD_SIZE_MB }} MB。</span>
        </div>
        <div v-else class="ocr-workspace">
          <aside class="ocr-record-panel">
            <div class="panel-heading">
              <div><strong>识别记录</strong><small>{{ records.length }} 张图片</small></div>
            </div>
            <div class="ocr-record-list">
              <button
                v-for="record in records"
                :key="record.id"
                type="button"
                class="ocr-record"
                :class="{ selected: record.id === selectedId }"
                @click="selectRecord(record)"
              >
                <FileImage :size="18" />
                <span>
                  <strong>{{ record.source_image.file_name }}</strong>
                  <small>{{ formatSize(record.source_image.file_size) }} · {{ formatTime(record.created_at) }}</small>
                </span>
                <em :class="record.status">{{ statusLabel(record.status) }}</em>
              </button>
            </div>
          </aside>

          <section v-if="selected" class="ocr-image-panel ocr-panel">
            <div class="panel-heading">
              <div><strong>原始图片</strong><small>{{ selected.source_image.file_name }}</small></div>
              <span class="panel-hint">可滚动查看</span>
            </div>
            <div class="image-preview">
              <img v-if="previewUrl" :src="previewUrl" :alt="selected.source_image.file_name" />
              <LoaderCircle v-else class="spin" :size="26" />
            </div>
            <div class="image-caption">
              <FileImage :size="15" />
              <span>{{ selected.source_image.width }} × {{ selected.source_image.height }} px</span>
              <span>{{ formatSize(selected.source_image.file_size) }}</span>
            </div>
          </section>

          <section v-if="selected" class="ocr-editor-panel ocr-panel">
            <div class="panel-heading">
              <div><strong>校对结果</strong><small>可直接编辑并保存</small></div>
              <em :class="['status-badge', selected.status]">{{ statusLabel(selected.status) }}</em>
            </div>

            <div v-if="selected.status === 'failed'" class="ocr-error">
              <AlertCircle :size="20" />
              <div><strong>识别失败</strong><p>{{ selected.error_message }}</p></div>
              <a-button :loading="retrying" @click="retryRecognition">
                <RefreshCw :size="15" />重新识别
              </a-button>
            </div>

            <template v-else>
              <div class="ocr-meta">
                <span>引擎 <strong>{{ selected.engine_version }}</strong></span>
                <span>文字块 <strong>{{ selected.blocks.length }}</strong></span>
                <span v-if="averageConfidence">平均置信度 <strong>{{ averageConfidence }}</strong></span>
                <span v-if="selected.processing_ms">耗时 <strong>{{ selected.processing_ms }} ms</strong></span>
              </div>
              <label class="ocr-editor">
                <span>校对结果文本</span>
                <a-textarea
                  v-model:value="draftText"
                  class="ocr-textarea"
                  :rows="12"
                  placeholder="当前图片未识别到文字"
                />
              </label>
              <div class="ocr-actions">
                <small>保存后，未来 Skills 将优先读取校对结果；原始 OCR 文本不会被覆盖。</small>
                <a-button type="primary" :loading="saving" :disabled="!hasChanges" @click="saveCorrection">
                  <Save :size="15" />保存校对
                </a-button>
              </div>
              <a-collapse v-if="selected.corrected_text !== null" ghost>
                <a-collapse-panel key="raw" header="查看 RapidOCR 原始文本">
                  <pre class="raw-text">{{ selected.raw_text || '未识别到文字' }}</pre>
                </a-collapse-panel>
              </a-collapse>
            </template>
          </section>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<style scoped lang="less">
:global(.ocr-fullscreen-root) {
  position: fixed;
  inset: 0;
}

:global(.ocr-fullscreen-root .ant-modal-wrap) {
  overflow: hidden;
}

:global(.ocr-fullscreen-root .ant-modal) {
  width: 100vw !important;
  max-width: none !important;
  height: 100vh;
  margin: 0;
  padding-bottom: 0;
}

:global(.ocr-fullscreen-root .ant-modal-content) {
  height: 100vh;
  padding: 0;
  border-radius: 0;
  overflow: hidden;
}

:global(.ocr-fullscreen-root .ant-modal-body) {
  height: 100%;
  padding: 0 !important;
}

:deep(.ocr-fullscreen-modal) {
  padding-bottom: 0;
}

:deep(.ocr-fullscreen-modal .ant-modal) {
  top: 0;
  width: 100vw !important;
  max-width: none;
  height: 100vh;
  margin: 0;
  padding-bottom: 0;
}

:deep(.ocr-fullscreen-modal .ant-modal-content) {
  height: 100vh;
  padding: 0;
  overflow: hidden;
  border-radius: 0;
}

:deep(.ocr-fullscreen-modal .ant-modal-body) {
  height: 100%;
  padding: 0 !important;
}

.ocr-shell { position: relative; height: 100%; display: flex; flex-direction: column; background: var(--gray-25); color: var(--color-text); }
.ocr-drop-overlay { position: absolute; z-index: 5; inset: 0; display: flex; align-items: center; justify-content: center; background: color-mix(in srgb, var(--main-50) 88%, white); border: 3px dashed var(--main-500); pointer-events: none; }
.ocr-drop-message { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 28px 42px; border-radius: 14px; background: var(--gray-0); color: var(--main-700); box-shadow: 0 12px 32px rgba(0, 0, 0, 0.12); text-align: center; }
.ocr-drop-message span { color: var(--color-text-secondary); font-size: 12px; }
.ocr-toolbar { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 24px; min-height: 72px; padding: 14px 28px; border-bottom: 1px solid var(--gray-150); background: var(--gray-0); }
.ocr-toolbar-title, .ocr-toolbar-actions { display: flex; align-items: center; gap: 12px; }
.ocr-toolbar-title { min-width: 0; }
.ocr-toolbar-title h2 { margin: 0; font-size: 18px; line-height: 1.3; }
.ocr-toolbar-title p { margin: 4px 0 0; color: var(--color-text-secondary); font-size: 12px; }
.ocr-title-icon { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 38px; border-radius: 10px; background: var(--main-50); color: var(--main-700); }
.ocr-toolbar-actions :deep(.ant-btn) { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.ocr-close-button { color: var(--color-text-secondary); }
.ocr-body { flex: 1 1 auto; min-height: 0; padding: 18px 24px 24px; overflow: hidden; }
.ocr-loading, .ocr-empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--color-text-secondary); }
.ocr-empty strong { color: var(--color-text); }
.ocr-empty span { max-width: 480px; text-align: center; }
.ocr-workspace { height: 100%; display: grid; grid-template-columns: 250px minmax(0, 1.1fr) minmax(380px, 0.9fr); gap: 16px; min-height: 0; }
.ocr-record-panel, .ocr-panel { min-width: 0; min-height: 0; border: 1px solid var(--gray-150); border-radius: 10px; background: var(--gray-0); }
.ocr-record-panel { display: flex; flex-direction: column; overflow: hidden; }
.ocr-record-list { display: flex; flex: 1 1 auto; flex-direction: column; gap: 8px; min-height: 0; padding: 12px; overflow: auto; }
.panel-heading { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 54px; padding: 12px 16px; border-bottom: 1px solid var(--gray-150); }
.panel-heading > div { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.panel-heading strong { font-size: 14px; }
.panel-heading small, .panel-hint { overflow: hidden; color: var(--color-text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.ocr-record { width: 100%; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 11px; text-align: left; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); color: var(--color-text); cursor: pointer; }
.ocr-record:hover { border-color: var(--main-300); background: var(--main-10); }
.ocr-record.selected { border-color: var(--main-color); background: var(--main-30); box-shadow: 0 0 0 2px var(--main-50); }
.ocr-record span { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.ocr-record strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.ocr-record small { color: var(--color-text-tertiary); font-size: 11px; }
.ocr-record em, .status-badge { padding: 2px 7px; border-radius: 999px; background: var(--gray-100); color: var(--gray-600); font-size: 11px; font-style: normal; white-space: nowrap; }
.ocr-record em.completed, .status-badge.completed { background: var(--color-success-50); color: var(--color-success-700); }
.ocr-record em.processing, .status-badge.processing { background: var(--color-info-50); color: var(--color-info-700); }
.ocr-record em.failed, .status-badge.failed { background: var(--color-error-50); color: var(--color-error-700); }
.ocr-panel { display: flex; flex-direction: column; overflow: hidden; }
.image-preview { flex: 1 1 auto; min-height: 0; display: flex; align-items: center; justify-content: center; overflow: auto; background: var(--gray-25); }
.image-preview img { display: block; max-width: 100%; max-height: 100%; object-fit: contain; }
.image-caption { flex: 0 0 auto; display: flex; align-items: center; gap: 10px; padding: 10px 16px; border-top: 1px solid var(--gray-150); color: var(--color-text-secondary); font-size: 12px; }
.image-caption span + span { padding-left: 10px; border-left: 1px solid var(--gray-200); }
.ocr-editor-panel { overflow: auto; }
.ocr-meta { flex: 0 0 auto; display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 12px 16px 0; color: var(--color-text-secondary); font-size: 12px; }
.ocr-meta strong { color: var(--color-text); }
.ocr-editor { display: flex; flex: 1 1 auto; flex-direction: column; gap: 7px; min-height: 0; padding: 14px 16px 0; }
.ocr-editor > span { font-size: 13px; font-weight: 600; }
.ocr-textarea { flex: 1 1 auto; min-height: 250px; resize: vertical; }
.ocr-editor-panel :deep(.ant-input) { height: 100%; min-height: 250px; resize: vertical; }
.ocr-actions { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 14px 16px; }
.ocr-actions small { color: var(--color-text-tertiary); line-height: 1.5; }
.ocr-error { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: start; gap: 10px; margin: 16px; padding: 14px; border: 1px solid var(--color-error-100); border-radius: 8px; background: var(--color-error-50); color: var(--color-error-700); }
.ocr-error p { margin: 3px 0 0; color: var(--color-text-secondary); }
.ocr-error :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; }
.raw-text { max-height: 180px; margin: 0; padding: 12px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: 6px; background: var(--gray-25); color: var(--color-text-secondary); font: 12px/1.6 monospace; }
.ocr-editor-panel :deep(.ant-collapse) { flex: 0 0 auto; margin: 0 8px 8px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 1100px) {
  .ocr-workspace { grid-template-columns: 220px minmax(0, 1fr); }
  .ocr-editor-panel { grid-column: 2; }
}

@media (max-width: 760px) {
  .ocr-toolbar { align-items: flex-start; flex-direction: column; gap: 12px; padding: 14px 16px; }
  .ocr-toolbar-actions { width: 100%; justify-content: space-between; }
  .ocr-body { padding: 12px; overflow: auto; }
  .ocr-workspace { height: auto; grid-template-columns: 1fr; }
  .ocr-record-panel { max-height: 220px; }
  .ocr-image-panel, .ocr-editor-panel { min-height: 520px; }
  .ocr-editor-panel { grid-column: auto; }
  .ocr-actions, .ocr-error { align-items: stretch; flex-direction: column; }
  .ocr-error { display: flex; }
}
</style>
