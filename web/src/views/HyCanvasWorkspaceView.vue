<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, RefreshCw } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import { materialLibraryApi } from '@/apis/material_library_api'
import VisualWorkspaceHeader from '@/components/content/VisualWorkspaceHeader.vue'
import { normalizeHyCanvasSessionUrl } from '@/utils/hycanvasSession'

const route = useRoute()
const router = useRouter()
const editorUrl = ref('')
const loading = ref(true)
const errorMessage = ref('')
const editorOrigin = computed(() => editorUrl.value ? new URL(editorUrl.value).origin : '')
const sessionKey = typeof route.query.session === 'string' ? route.query.session : ''
let editContext = null

const blobToDataUrl = async (blob, preview = false) => {
  let output = blob
  if (preview && 'createImageBitmap' in window) {
    const bitmap = await createImageBitmap(blob)
    const scale = Math.min(1, 360 / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close()
    output = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82)) || blob
  }
  return await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(output)
  })
}

const replyToHyCanvas = (source, origin, requestId, payload) => {
  source?.postMessage({ type: 'contentswarm:materials:response', requestId, ...payload }, origin)
}

const ensureImageGallery = async (name) => {
  const normalizedName = String(name || '').trim()
  if (!normalizedName) return 'uncategorized'
  const current = await materialLibraryApi.listGalleries()
  const existing = current.galleries?.find((gallery) => gallery.name === normalizedName)
  if (existing) return existing.id
  const created = await materialLibraryApi.createCategory({
    material_type: 'image',
    name: normalizedName,
    description: '由 HyCanvas 上传文件夹同步创建'
  })
  return created.category.id
}

const handleMaterialRequest = async (event) => {
  const { requestId, action, payload = {} } = event.data
  try {
    if (action === 'list-galleries') {
      const result = await materialLibraryApi.listGalleries()
      replyToHyCanvas(event.source, event.origin, requestId, { ok: true, data: result })
      return
    }
    if (action === 'list-items') {
      const result = await materialLibraryApi.listItems({
        material_type: 'image',
        category: payload.category,
        query: payload.query,
        page: payload.page || 1,
        page_size: payload.page_size || 24,
        sort: 'newest'
      })
      replyToHyCanvas(event.source, event.origin, requestId, { ok: true, data: result })
      return
    }
    if (action === 'get-file') {
      const response = await materialLibraryApi.getItemFile(payload.item_id)
      const blob = await response.blob()
      const dataUrl = await blobToDataUrl(blob, payload.purpose === 'preview')
      replyToHyCanvas(event.source, event.origin, requestId, {
        ok: true,
        data: { data_url: dataUrl, content_type: blob.type }
      })
      return
    }
    if (action === 'ensure-gallery') {
      const categoryId = await ensureImageGallery(payload.name)
      replyToHyCanvas(event.source, event.origin, requestId, { ok: true, data: { category_id: categoryId } })
      return
    }
    if (action === 'upload-image') {
      const categoryId = await ensureImageGallery(payload.category_name)
      const response = await fetch(payload.data_url)
      const blob = await response.blob()
      const file = new File([blob], payload.name, { type: payload.content_type || blob.type || 'application/octet-stream' })
      const result = await materialLibraryApi.importImages([file], categoryId)
      replyToHyCanvas(event.source, event.origin, requestId, { ok: true, data: result })
      return
    }
    throw new Error('不支持的素材库操作')
  } catch (error) {
    replyToHyCanvas(event.source, event.origin, requestId, {
      ok: false,
      error: error.message || '素材库读取失败'
    })
  }
}

const loadHyCanvas = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    if (sessionKey) {
      const raw = sessionStorage.getItem(`hycanvas-editor:${sessionKey}`)
      if (!raw) throw new Error('编辑上下文已失效，请返回内容结果后重新进入')
      editContext = JSON.parse(raw)
      const session = await contentApi.createHyCanvasEditorSession(editContext.designId, {
        artifact_id: editContext.artifactId,
        return_url: editContext.returnUrl,
        return_label: editContext.returnLabel
      })
      editorUrl.value = normalizeHyCanvasSessionUrl(session.editor_url, window.location.href)
    } else {
      const session = await contentApi.createHyCanvasWorkspaceSession()
      editorUrl.value = normalizeHyCanvasSessionUrl(session.editor_url, window.location.href)
    }
  } catch (error) {
    errorMessage.value = error.message || 'HyCanvas 工作台加载失败'
  } finally {
    loading.value = false
  }
}

const returnToContent = () => {
  if (!editContext?.returnUrl) {
    router.push('/content/new')
    return
  }
  const target = new URL(editContext.returnUrl)
  router.push(`${target.pathname}${target.search}${target.hash}`)
}

const handleHyCanvasMessage = (event) => {
  if (event.origin !== editorOrigin.value) return
  if (event.data?.type === 'hycanvas:materials:request') {
    void handleMaterialRequest(event)
    return
  }
  if (event.data?.type !== 'hycanvas:return') return
  const target = new URL(event.data.returnUrl)
  if (target.origin !== window.location.origin) return
  sessionStorage.removeItem(`hycanvas-editor:${sessionKey}`)
  router.push(`${target.pathname}${target.search}${target.hash}`)
}

onMounted(() => {
  window.addEventListener('message', handleHyCanvasMessage)
  void loadHyCanvas()
})

onBeforeUnmount(() => window.removeEventListener('message', handleHyCanvasMessage))
</script>

<template>
  <main class="hycanvas-workspace">
    <VisualWorkspaceHeader :subtitle="sessionKey ? '编辑内容封面' : '设计、模板与品牌素材'">
      <template v-if="sessionKey" #actions>
        <button type="button" class="header-action" @click="returnToContent">
          <ArrowLeft :size="16" /> 返回内容结果
        </button>
      </template>
    </VisualWorkspaceHeader>

    <section v-if="loading" class="workspace-state">正在连接 HyCanvas…</section>
    <section v-else-if="errorMessage" class="workspace-state workspace-error">
      <p>{{ errorMessage }}</p>
      <button type="button" class="header-action" @click="loadHyCanvas">
        <RefreshCw :size="16" /> 重新加载
      </button>
    </section>
    <iframe
      v-else
      class="hycanvas-frame"
      :src="editorUrl"
      title="ContentSwarm 设计工作台"
      allow="clipboard-read; clipboard-write"
    />
  </main>
</template>

<style scoped lang="less">
.hycanvas-workspace {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-width: 0;
  background: var(--main-5);
}

.header-action {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-action {
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 7px 12px;
  color: var(--gray-700);
  background: var(--main-1);
  cursor: pointer;
}

.header-action:hover {
  color: var(--main-700);
  border-color: var(--main-300);
}

.hycanvas-frame {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
  border: 0;
  background: #fff;
}

.workspace-state {
  display: grid;
  flex: 1 1 auto;
  place-content: center;
  gap: 12px;
  color: var(--gray-500);
  text-align: center;
}

.workspace-error {
  color: var(--color-error-500);
}
</style>
