<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Copy, FilePlus2, History, RotateCcw, Trash2 } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import { useContentStudioStore } from '@/stores/contentStudio'

import ContentResultView from './ContentResultView.vue'
import { formatDateTime } from '@/utils/time'

const router = useRouter()
const route = useRoute()
const activeTaskId = ref(typeof route.query.task === 'string' ? route.query.task : '')
const generatedOnly = ref(true)
const selectTask = (id) => {
  activeTaskId.value = id || ''
  router.replace({ query: { ...route.query, task: id || undefined } })
}
const taskTitle = (task) => task.selected_title?.text || task.name || '未命名内容'
const store = useContentStudioStore()
const page = ref(1)
const pageSize = ref(20)
const status = ref(undefined)
const selectedTaskIds = ref([])
const deleting = ref(false)

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
    await store.loadHistory({ page: page.value, page_size: pageSize.value, status: status.value, generated_only: generatedOnly.value })
    if (!store.history.some(task => task.id === activeTaskId.value)) selectTask(store.history[0]?.id)
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
</script>

<template>
  <div class="content-history-page">
    <header>
      <h1><History :size="22" />生成历史</h1>
      <a-button type="primary" @click="router.push('/content/new')"><FilePlus2 :size="16" />新建内容</a-button>
    </header>
    <div class="history-workspace">
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
                <small>{{ statusLabels[record.status] || record.status }} · {{ formatDateTime(record.updated_at) }}</small>
              </button>
              <div class="row-actions">
                <a-button type="text" aria-label="复制任务" @click="duplicate(record)"><Copy :size="15" /></a-button>
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
  </div>
</template>

<style scoped lang="less">
.content-history-page { min-height: 100vh; padding: 20px var(--page-padding); background: var(--gray-25); color: var(--color-text); }
header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 16px; }
header h1 { display: flex; align-items: center; gap: 8px; margin: 0; font-size: 22px; }
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
.row-actions { grid-column: 2; display: flex; justify-content: flex-end; }
.history-result { padding: 0; min-height: 0; }
.history-empty { padding: 60px 20px; }
@media (max-width: 1500px) { .history-result :deep(.result-layout) { grid-template-columns: minmax(0, 1fr); } .history-result :deep(.result-header) { flex-wrap: wrap; } }
@media (max-width: 900px) { .history-workspace { grid-template-columns: minmax(0, 1fr); } .history-card { position: static; } .history-list { max-height: 240px; } }
</style>
