<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Copy, FilePlus2, Grid2X2, History, Image, List, PencilLine, RotateCcw, Trash2 } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import XiaohongshuDistributionDrawer from '@/components/content/XiaohongshuDistributionDrawer.vue'
import { useContentStudioStore } from '@/stores/contentStudio'

import ContentResultView from './ContentResultView.vue'
import { formatDateTime } from '@/utils/time'

const router = useRouter()
const route = useRoute()
const activeTaskId = ref(typeof route.query.task === 'string' ? route.query.task : '')
const viewMode = ref(route.query.view === 'grid' ? 'grid' : 'list')
const generatedOnly = ref(true)
const selectTask = (id) => {
  activeTaskId.value = id || ''
  router.replace({ query: { ...route.query, task: id || undefined } })
}
const taskTitle = (task) => task.selected_title?.text || task.name || '未命名内容'
const cardTitle = (task) => task.artifact_title || taskTitle(task)
const store = useContentStudioStore()
const page = ref(1)
const pageSize = ref(20)
const status = ref(undefined)
const selectedTaskIds = ref([])
const deleting = ref(false)
const coverUrls = ref({})
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailTask = ref(null)
const detailArtifact = ref(null)
const detailCoverUrl = ref('')
const distributionOpen = ref(false)
let coverLoadId = 0
let detailLoadId = 0
const selectedCoverUrl = computed(() => coverUrls.value[detailTask.value?.id] || detailCoverUrl.value)
const canPublish = computed(() =>
  detailTask.value?.runtime_config_snapshot?.creation_mode === 'viral_rewrite' &&
  ['passed', 'warning'].includes(detailArtifact.value?.review_snapshot?.status)
)

const clearCoverUrls = () => {
  Object.values(coverUrls.value).forEach((url) => URL.revokeObjectURL(url))
  coverUrls.value = {}
}

const loadCardCovers = async () => {
  const loadId = ++coverLoadId
  clearCoverUrls()
  if (viewMode.value !== 'grid') return
  await Promise.all(store.history.filter((task) => task.cover_asset_id).map(async (task) => {
    try {
      const response = await contentApi.getCoverAssetFile(task.cover_asset_id)
      const url = URL.createObjectURL(await response.blob())
      if (loadId !== coverLoadId) URL.revokeObjectURL(url)
      else coverUrls.value = { ...coverUrls.value, [task.id]: url }
    } catch {
      // A missing cover keeps its placeholder without blocking the other cards.
    }
  }))
}

const setViewMode = async (mode) => {
  if (viewMode.value === mode) return
  viewMode.value = mode
  page.value = 1
  await router.replace({ query: { ...route.query, view: mode === 'grid' ? 'grid' : undefined } })
  await load()
}

const openCard = async (task) => {
  const loadId = ++detailLoadId
  detailOpen.value = true
  detailLoading.value = true
  detailTask.value = null
  detailArtifact.value = null
  if (detailCoverUrl.value) URL.revokeObjectURL(detailCoverUrl.value)
  detailCoverUrl.value = ''
  try {
    const response = await contentApi.getTask(task.id)
    if (!detailOpen.value || loadId !== detailLoadId) return
    detailTask.value = response.task
    detailArtifact.value = response.artifact
    if (response.artifact?.cover_asset_id && !coverUrls.value[task.id]) {
      const coverResponse = await contentApi.getCoverAssetFile(response.artifact.cover_asset_id)
      const url = URL.createObjectURL(await coverResponse.blob())
      if (detailOpen.value && loadId === detailLoadId) detailCoverUrl.value = url
      else URL.revokeObjectURL(url)
    }
  } catch (error) {
    if (loadId === detailLoadId) message.error(error.message || '加载生成详情失败')
  } finally {
    if (loadId === detailLoadId) detailLoading.value = false
  }
}

const copyText = async (value, label) => {
  try {
    await navigator.clipboard.writeText(value)
    message.success(`已复制${label}`)
  } catch {
    message.error(`复制${label}失败`)
  }
}

const statusLabels = {
  draft: '草稿',
  brief_ready: '简报完成',
  strategy_ready: '策略完成',
  queued: '排队中',
  waiting_human: '等待人工',
  failed: '失败',
  review_required: '待审核',
  reviewed: '已审核',
  review_blocked: '审核阻断',
  completed: '已完成',
  cancelled: '已取消'
}

const load = async () => {
  try {
    await store.loadHistory({ page: page.value, page_size: pageSize.value, status: viewMode.value === 'grid' ? undefined : status.value, generated_only: viewMode.value === 'grid' || generatedOnly.value })
    if (viewMode.value === 'list' && !store.history.some(task => task.id === activeTaskId.value)) selectTask(store.history[0]?.id)
    void loadCardCovers()
  } catch (error) {
    message.error(error.message || '加载生产历史失败')
  }
}

const duplicate = async (task) => {
  try {
    const response = await contentApi.duplicateTask(task.id)
    message.success('已复制任务')
    router.push(`/content/tasks/${response.task.id}`)
  } catch (error) {
    message.error(error.message || '复制任务失败')
  }
}

const remove = (task) => {
  Modal.confirm({
    title: '删除内容任务',
    content: `确定删除“${task.name}”吗？内容任务会软删除，正式审计记录仍保留。`,
    okText: '删除',
    cancelText: '取消',
    okType: 'danger',
    onOk: async () => {
      try {
        await contentApi.deleteTask(task.id)
        selectedTaskIds.value = selectedTaskIds.value.filter((id) => id !== task.id)
        if (store.history.length === 1 && page.value > 1) page.value -= 1
        await load()
        message.success('生成历史已删除')
      } catch (error) {
        message.error(error.message || '删除生成历史失败')
        throw error
      }
    }
  })
}

const removeSelected = () => {
  const taskIds = [...selectedTaskIds.value]
  if (!taskIds.length) return
  Modal.confirm({
    title: `批量删除 ${taskIds.length} 条生成历史`,
    content: '确定删除所选内容任务吗？内容任务会软删除，正式审计记录仍保留。',
    okText: '删除',
    cancelText: '取消',
    okType: 'danger',
    onOk: async () => {
      deleting.value = true
      try {
        const response = await contentApi.deleteTasks(taskIds)
        selectedTaskIds.value = []
        if (store.history.length <= response.deleted_count && page.value > 1) page.value -= 1
        await load()
        message.success(`已删除 ${response.deleted_count} 条生成历史`)
      } catch (error) {
        message.error(error.message || '批量删除失败')
        throw error
      } finally {
        deleting.value = false
      }
    }
  })
}

const handlePageChange = (nextPage, nextPageSize) => {
  page.value = nextPage
  pageSize.value = nextPageSize
  void load()
}

const handleSelectionChange = (keys) => {
  selectedTaskIds.value = keys
}

onMounted(load)
onBeforeUnmount(() => {
  coverLoadId += 1
  detailLoadId += 1
  clearCoverUrls()
  if (detailCoverUrl.value) URL.revokeObjectURL(detailCoverUrl.value)
})
</script>

<template>
  <div class="content-history-page">
    <header>
      <div class="history-heading">
        <h1><History :size="22" />生成历史</h1>
        <div class="view-switch" role="group" aria-label="生成历史布局">
          <button type="button" :class="{ active: viewMode === 'list' }" :aria-pressed="viewMode === 'list'" @click="setViewMode('list')"><List :size="15" />列表</button>
          <button type="button" :class="{ active: viewMode === 'grid' }" :aria-pressed="viewMode === 'grid'" @click="setViewMode('grid')"><Grid2X2 :size="15" />卡片</button>
        </div>
      </div>
      <a-button type="primary" @click="router.push('/content/new')"><FilePlus2 :size="16" />新建内容</a-button>
    </header>
    <div v-if="viewMode === 'list'" class="history-workspace">
      <aside class="history-card" aria-label="历史文章列表">
        <div class="history-toolbar">
          <a-checkbox v-model:checked="generatedOnly" @change="page = 1; load()">仅看已生成</a-checkbox>
          <a-button aria-label="刷新历史" @click="load"><RotateCcw :size="15" /></a-button>
        </div>
        <a-select v-model:value="status" allow-clear placeholder="全部状态" @change="page = 1; load()">
          <a-select-option v-for="(label, value) in statusLabels" :key="value" :value="value">{{ label }}</a-select-option>
        </a-select>
        <a-button v-if="selectedTaskIds.length" danger :loading="deleting" @click="removeSelected">
          <Trash2 :size="15" />删除所选（{{ selectedTaskIds.length }}）
        </a-button>
        <a-spin :spinning="store.loading.history">
          <div class="history-list">
            <div v-for="record in store.history" :key="record.id" class="history-item" :class="{ active: activeTaskId === record.id }">
              <a-checkbox :checked="selectedTaskIds.includes(record.id)" :aria-label="`选择 ${taskTitle(record)}`" @change="event => handleSelectionChange(event.target.checked ? [...selectedTaskIds, record.id] : selectedTaskIds.filter(id => id !== record.id))" />
              <button type="button" class="task-link" :aria-current="activeTaskId === record.id ? 'true' : undefined" @click="selectTask(record.id)">
                <strong>{{ taskTitle(record) }}</strong>
                <small class="task-code">任务编码 {{ record.id }}</small>
                <small>{{ statusLabels[record.status] || record.status }} · {{ formatDateTime(record.updated_at) }}</small>
              </button>
              <div class="row-actions">
                <a-button v-if="record.runtime_config_snapshot?.creation_mode === 'viral_rewrite'" type="text" aria-label="复制任务" @click="duplicate(record)"><Copy :size="15" /></a-button>
                <a-button type="text" danger aria-label="删除任务" @click="remove(record)"><Trash2 :size="15" /></a-button>
              </div>
            </div>
            <a-empty v-if="!store.history.length && !store.loading.history" description="暂无符合条件的内容" />
          </div>
        </a-spin>
        <a-pagination :current="page" :page-size="pageSize" :total="store.historyTotal" simple @change="handlePageChange" />
      </aside>
      <ContentResultView v-if="activeTaskId" :key="activeTaskId" :task-id="activeTaskId" class="history-result" />
      <div v-else class="history-empty"><a-empty description="暂无文章，生成内容后可在这里查看" /></div>
    </div>
    <section v-else class="history-gallery" aria-label="生成历史卡片">
      <div class="gallery-toolbar">
        <span>已生成内容 · {{ store.historyTotal }} 篇</span>
        <a-button aria-label="刷新历史" @click="load"><RotateCcw :size="15" />刷新</a-button>
      </div>
      <a-spin :spinning="store.loading.history">
        <div class="gallery-grid">
          <button v-for="record in store.history" :key="record.id" type="button" class="gallery-card" @click="openCard(record)">
            <div class="gallery-cover">
              <img v-if="coverUrls[record.id]" :src="coverUrls[record.id]" :alt="`${cardTitle(record)}的封面`" loading="lazy" />
              <div v-else class="gallery-cover-empty"><Image :size="30" /><span>暂无封面</span></div>
            </div>
            <div class="gallery-card-info">
              <strong>{{ cardTitle(record) }}</strong>
              <small>{{ formatDateTime(record.updated_at) }}</small>
            </div>
          </button>
        </div>
        <a-empty v-if="!store.history.length && !store.loading.history" description="暂无已生成内容" />
      </a-spin>
      <a-pagination :current="page" :page-size="pageSize" :total="store.historyTotal" show-less-items @change="handlePageChange" />
    </section>

    <a-modal v-model:open="detailOpen" class="history-detail-modal" title="生成结果详情" :width="920" centered destroy-on-close>
      <a-spin :spinning="detailLoading">
        <div v-if="detailArtifact" class="history-detail-layout">
          <section class="detail-cover" aria-label="内容封面">
            <div class="detail-cover-actions">
              <span><Image :size="17" />封面</span>
              <a-button v-if="detailArtifact.cover_asset_id" type="text" size="small" @click="router.push({ path: `/content/tasks/${detailTask.id}`, query: { resultDetail: '1' } })"><PencilLine :size="15" />编辑</a-button>
            </div>
            <div class="detail-cover-frame">
              <img v-if="selectedCoverUrl" :src="selectedCoverUrl" alt="当前内容封面" />
              <div v-else class="gallery-cover-empty"><Image :size="30" /><span>暂无封面</span></div>
            </div>
          </section>
          <div class="detail-content">
            <section class="detail-section">
              <div class="detail-section-heading"><strong>发布标题</strong><a-button type="text" size="small" @click="copyText(detailArtifact.title || '', '标题')"><Copy :size="14" />复制标题</a-button></div>
              <h2 class="history-detail-title">{{ detailArtifact.title || '暂无标题' }}</h2>
            </section>
            <section class="detail-section detail-body-section">
              <div class="detail-section-heading"><strong>正文文案</strong><a-button type="text" size="small" @click="copyText(detailArtifact.body || '', '正文')"><Copy :size="14" />复制正文</a-button></div>
              <div class="detail-body"><MarkdownPreview v-if="detailArtifact.body" :content="detailArtifact.body" /><p v-else>暂无正文内容</p></div>
            </section>
            <section class="detail-section detail-topics-section">
              <div class="detail-section-heading"><strong>发布标签</strong><a-button type="text" size="small" @click="copyText((detailArtifact.topics || []).map(topic => `#${topic}`).join(' '), '标签')"><Copy :size="14" />复制标签</a-button></div>
              <div class="detail-topics"><span v-for="topic in detailArtifact.topics || []" :key="topic">#{{ topic }}</span><p v-if="!detailArtifact.topics?.length">暂无发布标签</p></div>
            </section>
          </div>
        </div>
        <a-empty v-else-if="!detailLoading" description="内容资产尚未生成完成" />
      </a-spin>
      <template #footer>
        <div class="detail-footer"><a-button @click="detailOpen = false">关闭</a-button><a-button type="primary" :disabled="!canPublish" @click="detailOpen = false; distributionOpen = true">发布</a-button></div>
      </template>
    </a-modal>
    <XiaohongshuDistributionDrawer v-model:open="distributionOpen" :artifact="detailArtifact" />
  </div>
</template>

<style scoped lang="less">
.content-history-page { min-height: 100vh; padding: 20px var(--page-padding); background: var(--gray-25); color: var(--color-text); }
header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 16px; }
.history-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 24px; }
header h1 { display: flex; align-items: center; gap: 8px; margin: 0; font-size: 22px; }
.view-switch { display: inline-flex; gap: 2px; padding: 3px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-100); }
.view-switch button { display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 5px 10px; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.view-switch button.active { background: var(--gray-0); color: var(--main-700); box-shadow: 0 1px 4px var(--shadow-1); }
.view-switch button:focus-visible, .gallery-card:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
header :deep(.ant-btn), .history-card :deep(.ant-btn) { display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
.history-workspace { display: grid; grid-template-columns: 260px minmax(0, 1fr); align-items: start; gap: 20px; }
.history-card { min-width: 0; padding: 12px; display: flex; flex-direction: column; gap: 12px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); position: sticky; top: 16px; }
.history-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.history-list { max-height: calc(100dvh - 280px); overflow-y: auto; }
.history-item { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px; padding: 12px 8px; border-bottom: 1px solid var(--gray-150); border-radius: 6px; }
.history-item.active { background: var(--main-50); }
.task-link { min-width: 0; display: flex; flex-direction: column; gap: 7px; border: 0; padding: 0; background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.task-link strong { overflow-wrap: anywhere; line-height: 1.6; }
.task-link:hover strong, .history-item.active strong { color: var(--main-700); }
.task-link small { color: var(--color-text-tertiary); font-size: 11px; }
.task-link .task-code { overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.row-actions { grid-column: 2; display: flex; justify-content: flex-end; }
.history-result { padding: 0; min-height: 0; }
.history-empty { padding: 60px 20px; }
.history-gallery { max-width: 1500px; margin: 0 auto; }
.gallery-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 18px; color: var(--color-text-secondary); }
.gallery-toolbar :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; }
.gallery-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 20px; }
.gallery-card { min-width: 0; padding: 0; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 10px; background: var(--gray-0); color: var(--color-text); text-align: left; cursor: pointer; transition: border-color .18s ease, transform .18s ease; }
.gallery-card:hover { border-color: var(--main-color); transform: translateY(-2px); }
.gallery-cover { aspect-ratio: 3 / 4; overflow: hidden; background: var(--gray-100); }
.gallery-cover img { display: block; width: 100%; height: 100%; object-fit: cover; }
.gallery-cover-empty { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--color-text-tertiary); }
.gallery-card-info { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px 12px; }
.gallery-card-info strong { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-height: 1.5; }
.gallery-card-info small { color: var(--color-text-tertiary); }
.history-gallery :deep(.ant-pagination) { display: flex; justify-content: center; margin: 24px 0; }
@media (max-width: 1400px) { .gallery-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (max-width: 1100px) { .gallery-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 1500px) { .history-result :deep(.result-layout) { grid-template-columns: minmax(0, 1fr); } .history-result :deep(.result-header) { flex-wrap: wrap; } }
@media (max-width: 900px) { .history-workspace { grid-template-columns: minmax(0, 1fr); } .history-card { position: static; } .history-list { max-height: 240px; } }
@media (max-width: 700px) { .gallery-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; } .history-heading { gap: 10px; } }
</style>

<style lang="less">
.history-detail-modal {
  .history-detail-layout { height: min(760px, calc(100dvh - 190px)); min-height: 0; display: grid; grid-template-columns: minmax(320px, .9fr) minmax(0, 1.1fr); overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; }
  .detail-cover { min-width: 0; min-height: 0; display: flex; flex-direction: column; padding: 20px; border-right: 1px solid var(--gray-150); background: var(--gray-25); }
  .detail-cover-actions, .detail-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .detail-cover-actions > span, .detail-section-heading .ant-btn { display: inline-flex; align-items: center; gap: 5px; color: var(--main-700); }
  .detail-cover-frame { min-height: 0; flex: 1; display: flex; align-items: center; justify-content: center; margin-top: 12px; overflow: hidden; }
  .detail-cover-frame img { display: block; max-width: 100%; max-height: 100%; aspect-ratio: 3 / 4; object-fit: contain; border-radius: 6px; background: var(--gray-100); }
  .detail-content { min-width: 0; min-height: 0; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; padding: 0 22px; overflow: hidden; background: var(--gray-0); }
  .detail-section { min-height: 0; padding: 14px 0; border-bottom: 1px solid var(--gray-150); }
  .detail-section:last-child { border: 0; }
  .detail-section h2 { margin: 8px 0 0; padding: 9px 12px; border: 1px solid var(--gray-150); border-radius: 6px; font-size: 15px; line-height: 1.5; overflow-wrap: anywhere; white-space: pre-line; }
  .detail-body-section { display: flex; flex-direction: column; overflow: hidden; }
  .detail-body { min-height: 0; flex: 1; margin-top: 8px; overflow-y: auto; overscroll-behavior: contain; font-size: 13px; line-height: 1.68; overflow-wrap: anywhere; }
  .detail-body p:first-child { margin-top: 0; }
  .detail-topics { display: flex; flex-wrap: wrap; gap: 4px 8px; margin-top: 8px; color: var(--main-700); font-size: 13px; }
  .detail-topics p { margin: 0; color: var(--color-text-tertiary); }
  .detail-footer { display: flex; justify-content: space-between; }
  .detail-footer .ant-btn { min-width: 132px; }
  @media (max-width: 700px) {
    .history-detail-layout { height: calc(100dvh - 170px); display: block; overflow-y: auto; }
    .detail-cover { height: min(440px, 55dvh); border-right: 0; border-bottom: 1px solid var(--gray-150); }
    .detail-content { display: block; overflow: visible; }
    .detail-body-section, .detail-body { overflow: visible; }
  }
}
</style>
