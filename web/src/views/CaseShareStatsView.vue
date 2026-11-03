<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Download, RefreshCw, Search } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import { caseShareApi } from '@/apis/case_share_api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const loading = ref(false)
const loadError = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const summary = ref(null)
const exporting = ref(false)
const keyword = ref('')
const source = ref()
const dates = ref([])
const filters = ref({})
let version = 0
const canDelete = computed(() => userStore.can('customer_lead.delete'))
const pagination = computed(() => ({
  current: page.value, pageSize: pageSize.value, total: total.value,
  showSizeChanger: true, showTotal: (count) => `共 ${count} 条`, pageSizeOptions: ['10', '20', '50']
}))
const columns = [
  { title: '序号', key: 'index', width: 76 },
  { title: '来源', key: 'source', width: 220, ellipsis: true },
  { title: '分享人姓名', dataIndex: 'sharer_name', key: 'sharer_name', width: 140 },
  { title: '工号', dataIndex: 'employee_code', key: 'employee_code', width: 130 },
  { title: '所属门店', dataIndex: 'branch', key: 'branch', width: 180 },
  { title: '分享时间', key: 'time', width: 190 },
  { title: '操作', key: 'action', width: 96, fixed: 'right' }
]

function sourceLabel(value) {
  return { pc: 'PC端案例分享', mp: '小程序案例分享' }[value] || '来源未知'
}

async function load() {
  const current = ++version
  loading.value = true
  loadError.value = ''
  try {
    const [response, stats] = await Promise.all([
      caseShareApi.list({ ...filters.value, page: page.value, page_size: pageSize.value }),
      caseShareApi.summary()
    ])
    if (current !== version) return
    items.value = response.items
    total.value = response.total
    summary.value = stats
  } catch (error) {
    if (current === version) {
      items.value = []
      summary.value = null
      loadError.value = error.message || '加载案例分享失败'
    }
  } finally {
    if (current === version) loading.value = false
  }
}

function search() {
  filters.value = {
    keyword: keyword.value.trim(), source: source.value,
    start_date: dates.value?.[0], end_date: dates.value?.[1]
  }
  page.value = 1
  void load()
}

function reset() {
  keyword.value = ''
  source.value = undefined
  dates.value = []
  search()
}

async function exportRows() {
  exporting.value = true
  const current = version
  try {
    const response = await caseShareApi.export(filters.value)
    const blob = await response.blob()
    if (current !== version) return
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = '案例分享.xlsx'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    if (current === version) message.error(error.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

function changePage(value) {
  page.value = value.current
  pageSize.value = value.pageSize
  void load()
}

function remove(row) {
  Modal.confirm({
    title: '确认删除这条案例分享？', content: '删除后该分享链接将失效，累计及今日统计相应减少，同时删除该分享关联的全部预约客资。此操作不可撤销。',
    okText: '删除', cancelText: '取消', okType: 'danger',
    async onOk() {
      try {
        await caseShareApi.delete(row.id)
        message.success('已删除')
        if (items.value.length === 1 && page.value > 1) page.value -= 1
        await load()
      } catch (error) {
        message.error(error.message || '删除失败')
        throw error
      }
    }
  })
}

function formatTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  }).format(new Date(value))
}

onMounted(load)
watch(() => userStore.uid, () => {
  version += 1
  items.value = []
  summary.value = null
  reset()
})
onBeforeUnmount(() => { version += 1 })
</script>

<template>
  <div class="case-share-stats-page">
    <PageHeader title="案例分享" :loading="loading">
      <template #actions>
        <a-button :loading="loading" @click="load"><RefreshCw :size="15" />刷新</a-button>
      </template>
    </PageHeader>
    <main class="report-content">
      <section class="summary-cards" aria-label="分享统计">
        <div class="summary-card"><span>累计案例分享</span><strong>{{ summary?.total ?? '—' }}</strong><small>当前权限范围内成功生成的分享数</small></div>
        <div class="summary-card"><span>今日分享</span><strong>{{ summary?.today ?? '—' }}</strong><small>北京时间今日成功生成的分享数</small></div>
      </section>
      <a-form class="filters" layout="vertical">
        <a-form-item label="关键词">
          <a-input v-model:value="keyword" placeholder="分享人姓名、工号、所属门店" allow-clear :maxlength="80" @pressEnter="search" @change="!$event.target.value && search()">
            <template #suffix>
              <button type="button" class="keyword-search" aria-label="搜索案例分享" title="搜索" @click="search"><Search :size="16" /></button>
            </template>
          </a-input>
        </a-form-item>
        <a-form-item label="来源">
          <a-select v-model:value="source" placeholder="全部来源" allow-clear :options="[{ value: 'pc', label: 'PC端案例分享' }, { value: 'mp', label: '小程序案例分享' }]" @change="search" />
        </a-form-item>
        <a-form-item label="分享时间">
          <a-range-picker v-model:value="dates" value-format="YYYY-MM-DD" @change="search" />
        </a-form-item>
        <div class="filter-actions">
          <a-button @click="reset">重置</a-button>
          <a-button :loading="exporting" :disabled="loading || !!loadError" @click="exportRows"><Download :size="15" />导出</a-button>
        </div>
      </a-form>
      <a-result v-if="loadError" status="error" title="案例分享暂时无法加载" :sub-title="loadError">
        <template #extra><a-button type="primary" @click="load">重新加载</a-button></template>
      </a-result>
      <a-table v-else :columns="columns" :data-source="items" :loading="loading" row-key="id"
        :pagination="pagination" :scroll="{ x: 1032 }" :locale="{ emptyText: '暂无案例分享' }" @change="changePage">
        <template #bodyCell="{ column, record, index }">
          <template v-if="column.key === 'index'">{{ (page - 1) * pageSize + index + 1 }}</template>
          <template v-else-if="column.key === 'source'">
            <a v-if="record.source_url" :href="record.source_url" target="_blank" rel="noopener noreferrer">
              {{ sourceLabel(record.source_platform) }}
            </a>
            <span v-else>{{ sourceLabel(record.source_platform) }}</span>
          </template>
          <template v-else-if="['sharer_name', 'employee_code', 'branch'].includes(column.key)">{{ record[column.key] || '—' }}</template>
          <template v-else-if="column.key === 'time'">{{ formatTime(record.shared_at) }}</template>
          <template v-else-if="column.key === 'action'">
            <a-button v-if="canDelete" type="link" danger size="small" @click="remove(record)">删除</a-button>
          </template>
        </template>
      </a-table>
    </main>
  </div>
</template>

<style scoped lang="less">
.case-share-stats-page { min-height: 100%; background: var(--gray-25); }
.report-content { padding: 24px var(--page-padding); }
.summary-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-bottom: 20px; max-width: 760px; }
.summary-card { display: flex; flex-direction: column; gap: 8px; padding: 20px; border: 1px solid var(--gray-150); border-radius: 10px; background: var(--gray-0); color: var(--gray-600);
  strong { font-size: 28px; color: var(--gray-900); }
  small { font-size: 12px; }
}
.filters { display: grid; grid-template-columns: minmax(220px, 280px) 140px 240px max-content; align-items: end; gap: 10px; overflow-x: auto; padding: 16px; margin-bottom: 16px; border: 1px solid var(--gray-150); border-radius: 10px; background: var(--gray-0);
  :deep(.ant-form-item) { margin-bottom: 0; min-width: 0; }
  :deep(.ant-select), :deep(.ant-picker) { width: 100%; }
}
.filter-actions { display: flex; gap: 8px; white-space: nowrap; }
.keyword-search { display: inline-flex; align-items: center; justify-content: center; padding: 2px; border: 0; border-radius: 3px; background: transparent; color: var(--gray-600); cursor: pointer;
  &:hover { color: var(--main-color); }
  &:focus-visible { outline: 2px solid var(--main-color); outline-offset: 1px; }
}
:deep(.ant-table-cell) { font-variant-numeric: tabular-nums; }
:deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; }
</style>
