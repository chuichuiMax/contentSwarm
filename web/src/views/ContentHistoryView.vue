<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Copy, FilePlus2, History, RotateCcw, Trash2 } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import { useContentStudioStore } from '@/stores/contentStudio'

const router = useRouter()
const store = useContentStudioStore()
const page = ref(1)
const pageSize = ref(20)
const status = ref(undefined)

const statusLabels = {
  draft: '草稿',
  brief_ready: '简报完成',
  strategy_ready: '策略完成',
  queued: '排队中',
  waiting_human: '等待人工',
  failed: '失败',
  reviewed: '已审核',
  review_blocked: '审核阻断',
  completed: '已完成',
  cancelled: '已取消'
}

const load = async () => {
  try {
    await store.loadHistory({ page: page.value, page_size: pageSize.value, status: status.value })
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
      await contentApi.deleteTask(task.id)
      await load()
    }
  })
}

const handlePageChange = (nextPage, nextPageSize) => {
  page.value = nextPage
  pageSize.value = nextPageSize
  void load()
}

onMounted(load)
</script>

<template>
  <div class="content-history-page">
    <header>
      <div><span>Content Strategy Studio</span><h1><History :size="22" />生产历史</h1><p>恢复草稿、处理人工节点、查看失败原因或复用已有策略。</p></div>
      <a-button type="primary" @click="router.push('/content/new')"><FilePlus2 :size="16" />新建内容</a-button>
    </header>

    <section class="history-card">
      <div class="history-toolbar">
        <a-select v-model:value="status" allow-clear placeholder="全部状态" style="width: 180px" @change="load">
          <a-select-option v-for="(label, value) in statusLabels" :key="value" :value="value">{{ label }}</a-select-option>
        </a-select>
        <a-button @click="load"><RotateCcw :size="15" />刷新</a-button>
      </div>

      <a-table
        :data-source="store.history"
        :loading="store.loading.history"
        :pagination="{ current: page, pageSize, total: store.historyTotal, showSizeChanger: true }"
        row-key="id"
        @change="(pagination) => handlePageChange(pagination.current, pagination.pageSize)"
      >
        <a-table-column title="任务" key="name">
          <template #default="{ record }"><button type="button" class="task-link" @click="router.push(`/content/tasks/${record.id}`)"><strong>{{ record.name }}</strong><small>{{ record.id }}</small></button></template>
        </a-table-column>
        <a-table-column title="模式" data-index="mode" key="mode"><template #default="{ text }">{{ text === 'quick' ? '简化版' : '专业版' }}</template></a-table-column>
        <a-table-column title="目标" data-index="content_goal" key="goal" />
        <a-table-column title="状态" key="status"><template #default="{ record }"><span class="task-status" :class="record.status">{{ statusLabels[record.status] || record.status }}</span></template></a-table-column>
        <a-table-column title="更新时间" data-index="updated_at" key="updated" />
        <a-table-column title="操作" key="actions" width="150"><template #default="{ record }"><div class="row-actions"><a-button type="text" @click="duplicate(record)"><Copy :size="15" /></a-button><a-button type="text" danger @click="remove(record)"><Trash2 :size="15" /></a-button></div></template></a-table-column>
      </a-table>
    </section>
  </div>
</template>

<style scoped lang="less">
.content-history-page { min-height: 100vh; padding: 24px var(--page-padding) 48px; background: var(--gray-25); color: var(--color-text); }
header { max-width: 1180px; margin: 0 auto 18px; display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; }
header > div > span { color: var(--main-700); font-size: 12px; font-weight: 600; }
header h1 { display: flex; align-items: center; gap: 8px; margin: 4px 0; font-size: 24px; }
header p { margin: 0; color: var(--color-text-secondary); }
header :deep(.ant-btn), .history-toolbar :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; }
.history-card { max-width: 1180px; margin: 0 auto; padding: 18px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.history-toolbar { display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 14px; }
.task-link { display: flex; flex-direction: column; gap: 3px; border: 0; padding: 0; background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.task-link:hover strong { color: var(--main-color); }
.task-link small { color: var(--color-text-tertiary); }
.task-status { display: inline-flex; padding: 3px 8px; border-radius: 999px; background: var(--gray-100); color: var(--gray-600); font-size: 12px; }
.task-status.completed, .task-status.reviewed { background: var(--color-success-50); color: var(--color-success-700); }
.task-status.failed, .task-status.review_blocked { background: var(--color-error-50); color: var(--color-error-700); }
.task-status.waiting_human { background: var(--color-warning-50); color: var(--color-warning-900); }
.task-status.queued { background: var(--color-info-50); color: var(--color-info-700); }
.row-actions { display: flex; gap: 2px; }
@media (max-width: 700px) { header { flex-direction: column; } .history-card { padding: 12px; overflow-x: auto; } }
</style>
