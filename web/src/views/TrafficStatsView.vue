<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { FileText, GripVertical, Image, Images, Search, Settings, Share2, Star } from 'lucide-vue-next'

import { employeeApi } from '@/apis/employee_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import {
  TRAFFIC_COLUMN_DEFAULTS,
  TRAFFIC_COLUMN_SETTINGS_VERSION,
  cloneColumnSettings,
  moveColumnSetting,
  normalizeColumnSettings
} from '@/utils/trafficColumnSettings'

const COLUMN_STORAGE_KEY = 'traffic-stats-column-settings'
const COUNT_COLUMN_KEYS = new Set([
  'decoration_count',
  'review_notes_count',
  'personal_generated_count',
  'enterprise_generated_count',
  'case_share_count'
])

const router = useRouter()
const loading = ref(false)
const exporting = ref(false)
const keywordInput = ref('')
const keyword = ref('')
const branch = ref()
const department = ref()
const dateRange = ref([])
const branches = ref([])
const departments = ref([])
const employees = ref([])
const summary = ref({
  decoration: 0,
  review_notes: 0,
  personal_generated: 0,
  enterprise_generated: 0,
  case_shares: 0
})
function readStoredColumns() {
  try {
    return JSON.parse(localStorage.getItem(COLUMN_STORAGE_KEY) || 'null')
  } catch {
    return null
  }
}

const page = ref(1)
const pageSize = ref(10)
const sortState = ref({})
const columnPanelOpen = ref(false)
const columnSettings = ref(normalizeColumnSettings(readStoredColumns()))
const columnDraft = ref(cloneColumnSettings(columnSettings.value))
const dragIndex = ref(-1)

const cards = computed(() => [
  { key: 'decoration', label: '装修家居', value: summary.value.decoration, unit: '篇', hint: '已生成的装修家居内容', icon: Star },
  { key: 'review_notes', label: '好评笔记', value: summary.value.review_notes, unit: '篇', hint: '已生成的好评笔记', icon: FileText },
  { key: 'personal_generated', label: '生图（个人素材）', value: summary.value.personal_generated, unit: '张', hint: '个人 AI 生图图库', icon: Image },
  { key: 'enterprise_generated', label: '生图（企业共享）', value: summary.value.enterprise_generated, unit: '张', hint: '扩充公共图库', icon: Images },
  { key: 'case_shares', label: '案例分享', value: summary.value.case_shares, unit: '次', hint: '案例分享的越多成交的越多', icon: Share2 }
])

const dateParams = computed(() => (
  dateRange.value?.length === 2
    ? { date_from: dateRange.value[0], date_to: dateRange.value[1] }
    : {}
))

const compareText = (left, right) => String(left || '').localeCompare(String(right || ''), 'zh')
const columnSorters = {
  index: (left, right) => employees.value.indexOf(left) - employees.value.indexOf(right),
  name: (left, right) => compareText(left.name, right.name),
  login_account: (left, right) => compareText(left.login_account, right.login_account),
  employee_code: (left, right) => compareText(left.employee_code, right.employee_code),
  decoration_count: (left, right) => (left.decoration_count ?? 0) - (right.decoration_count ?? 0),
  review_notes_count: (left, right) => (left.review_notes_count ?? 0) - (right.review_notes_count ?? 0),
  personal_generated_count: (left, right) => (left.personal_generated_count ?? 0) - (right.personal_generated_count ?? 0),
  enterprise_generated_count: (left, right) => (left.enterprise_generated_count ?? 0) - (right.enterprise_generated_count ?? 0),
  case_share_count: (left, right) => (left.case_share_count ?? 0) - (right.case_share_count ?? 0),
  current_branch: (left, right) => compareText(left.current_branch, right.current_branch),
  current_department: (left, right) => compareText(left.current_department, right.current_department)
}

const tableColumns = computed(() =>
  columnSettings.value
    .filter((column) => column.visible)
    .map((column) => ({
      title: column.title,
      key: column.key,
      dataIndex: column.key === 'index' ? undefined : column.key,
      width: column.width === 'auto' ? (column.fixed ? 120 : undefined) : Number(column.width),
      align: COUNT_COLUMN_KEYS.has(column.key) ? 'center' : undefined,
      fixed: column.fixed ? 'left' : undefined,
      sorter: column.sortable ? columnSorters[column.key] : undefined,
      sortDirections: COUNT_COLUMN_KEYS.has(column.key) ? ['descend', 'ascend'] : undefined,
      sortOrder: sortState.value.columnKey === column.key ? sortState.value.order : null
    }))
)

const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: employees.value.length,
  showSizeChanger: true,
  showTotal: (total) => `每页显示 ${pageSize.value} 条，共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const load = async () => {
  loading.value = true
  try {
    const response = await employeeApi.listTrafficStats({
      keyword: keyword.value,
      branch: branch.value,
      department: department.value,
      ...dateParams.value
    })
    summary.value = response.summary || summary.value
    branches.value = response.branches || []
    departments.value = response.departments || []
    employees.value = response.employees || []
  } catch (error) {
    message.error(error.message || '加载数据概览失败')
  } finally {
    loading.value = false
  }
}

const search = () => {
  keyword.value = keywordInput.value.trim()
  page.value = 1
  void load()
}

const reset = () => {
  keywordInput.value = ''
  keyword.value = ''
  branch.value = undefined
  department.value = undefined
  dateRange.value = []
  sortState.value = {}
  page.value = 1
  void load()
}

const applyDateRange = () => {
  page.value = 1
  void load()
}

const handleTableChange = (pagination, _filters, sorter, extra) => {
  sortState.value = { columnKey: sorter.columnKey, order: sorter.order }
  page.value = extra.action === 'sort' ? 1 : pagination.current
  pageSize.value = pagination.pageSize
}

const exportStats = async () => {
  exporting.value = true
  try {
    const response = await employeeApi.exportTrafficStats({
      keyword: keyword.value,
      branch: branch.value,
      department: department.value,
      ...dateParams.value
    })
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = '数据概览.xlsx'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(error.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

const openHistory = (employee, entry) => {
  router.push({
    path: '/content/history',
    query: {
      view: 'grid',
      entry,
      employee_id: employee.id,
      employee_name: employee.name
    }
  })
}

const saveColumns = () => {
  const next = normalizeColumnSettings({ version: TRAFFIC_COLUMN_SETTINGS_VERSION, columns: columnDraft.value })
  if (!next.some((column) => column.visible)) {
    message.warning('至少保留一列')
    return
  }
  columnSettings.value = next
  columnDraft.value = cloneColumnSettings(next)
  if (!next.some((column) => column.visible && column.sortable && column.key === sortState.value.columnKey)) {
    sortState.value = {}
  }
  localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify({ version: TRAFFIC_COLUMN_SETTINGS_VERSION, columns: next }))
  columnPanelOpen.value = false
  message.success('列设置已保存')
}

const resetColumns = () => {
  columnDraft.value = cloneColumnSettings(TRAFFIC_COLUMN_DEFAULTS)
}

const onColumnDragStart = (index) => {
  dragIndex.value = index
}

const onColumnDrop = (index) => {
  columnDraft.value = moveColumnSetting(columnDraft.value, dragIndex.value, index)
  dragIndex.value = -1
}

const openGenerated = (employee, scope) => {
  router.push({
    path: '/materials/images',
    query: {
      employee_id: employee.id,
      gallery: 'generated',
      ...(scope === 'enterprise' ? { scope } : {})
    }
  })
}

const openCaseShares = (employee) => {
  router.push({
    path: '/case-shares',
    query: {
      sharer_uid: employee.case_share_uid,
      employee_name: employee.name,
      ...(dateRange.value?.length === 2
        ? { start_date: dateRange.value[0], end_date: dateRange.value[1] }
        : {})
    }
  })
}

watch(columnPanelOpen, (open) => {
  if (open) columnDraft.value = cloneColumnSettings(columnSettings.value)
})

onMounted(load)
</script>

<template>
  <div class="traffic-page">
    <PageHeader title="数据概览" show-border />
    <p class="page-hint">统计平台产生内容所消耗的流量</p>
    <section class="summary-grid" aria-label="流量汇总">
      <article v-for="card in cards" :key="card.key" class="summary-card">
        <div>
          <strong>{{ card.label }}</strong>
          <p><b>{{ card.value }}</b><span>{{ card.unit }}</span></p>
          <small>{{ card.hint }}</small>
        </div>
        <span class="summary-icon" :data-kind="card.key"><component :is="card.icon" :size="18" /></span>
      </article>
    </section>
    <section class="traffic-panel">
      <div class="traffic-toolbar">
        <a-input v-model:value="keywordInput" allow-clear placeholder="输入搜索关键词" @pressEnter="search">
          <template #prefix><Search :size="14" /></template>
        </a-input>
        <a-select v-model:value="branch" allow-clear placeholder="请选择所属门店" @change="page = 1; load()">
          <a-select-option v-for="item in branches" :key="item" :value="item">{{ item }}</a-select-option>
        </a-select>
        <a-select v-model:value="department" allow-clear placeholder="请选择所属部门" @change="page = 1; load()">
          <a-select-option v-for="item in departments" :key="item" :value="item">{{ item }}</a-select-option>
        </a-select>
        <span class="date-range-label">日期区间</span>
        <a-range-picker
          v-model:value="dateRange"
          class="date-range-filter"
          value-format="YYYY-MM-DD"
          :placeholder="['开始日期', '结束日期']"
          allow-clear
          aria-label="日期区间"
          @change="applyDateRange"
        />
        <a-button @click="reset">重置</a-button>
        <a-button type="primary" :loading="exporting" @click="exportStats">导出</a-button>
      </div>
      <a-table
        :columns="tableColumns"
        :data-source="employees"
        :loading="loading"
        :pagination="tablePagination"
        :scroll="{ x: 'max-content' }"
        row-key="id"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record, index }">
          <template v-if="column.key === 'index'">{{ (page - 1) * pageSize + index + 1 }}</template>
          <a-button v-else-if="column.key === 'decoration_count'" type="link" @click="openHistory(record, '装修家居')">
            {{ record.decoration_count }}
          </a-button>
          <a-button v-else-if="column.key === 'review_notes_count'" type="link" @click="openHistory(record, '好评笔记')">
            {{ record.review_notes_count }}
          </a-button>
          <a-button v-else-if="column.key === 'personal_generated_count'" type="link" @click="openGenerated(record)">
            {{ record.personal_generated_count }}
          </a-button>
          <a-button
            v-else-if="column.key === 'enterprise_generated_count'"
            type="link"
            @click="openGenerated(record, 'enterprise')"
          >
            {{ record.enterprise_generated_count }}
          </a-button>
          <a-button v-else-if="column.key === 'case_share_count'" type="link" @click="openCaseShares(record)">
            {{ record.case_share_count }}
          </a-button>
          <template v-else>{{ record[column.dataIndex] }}</template>
        </template>
      </a-table>
      <div class="column-settings">
        <section v-if="columnPanelOpen" class="column-settings-panel">
          <header>
            <strong>列设置</strong>
            <button type="button" @click="columnPanelOpen = false">收起</button>
          </header>
          <div class="column-settings-head">
            <span></span>
            <span>显示</span>
            <span>名称</span>
            <span>宽度</span>
            <span>排序</span>
            <span>固定</span>
          </div>
          <ul>
            <li
              v-for="(column, index) in columnDraft"
              :key="column.key"
              @dragover.prevent
              @drop="onColumnDrop(index)"
            >
              <button
                type="button"
                class="column-grip"
                draggable="true"
                aria-label="调整列顺序"
                @dragstart="onColumnDragStart(index)"
              >
                <GripVertical :size="14" />
              </button>
              <a-switch v-model:checked="column.visible" size="small" :aria-label="`显示${column.title}`" />
              <span class="column-name">{{ column.title }}</span>
              <a-input v-model:value="column.width" size="small" aria-label="宽度" />
              <a-switch v-model:checked="column.sortable" size="small" :aria-label="`排序${column.title}`" />
              <a-switch v-model:checked="column.fixed" size="small" :aria-label="`固定${column.title}`" />
            </li>
          </ul>
          <footer>
            <a-button @click="resetColumns">重置</a-button>
            <a-button type="primary" @click="saveColumns">保存</a-button>
          </footer>
        </section>
        <button
          type="button"
          class="column-settings-toggle"
          :aria-expanded="columnPanelOpen"
          aria-label="列设置"
          @click="columnPanelOpen = !columnPanelOpen"
        >
          <Settings :size="18" />
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped lang="less">
.traffic-page { min-height: 100%; padding: 8px 20px 24px; background: var(--gray-0); }
.page-hint { margin: 8px 0 16px; color: var(--color-text-tertiary); }
.summary-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 16px; margin-bottom: 18px; }
.summary-card { display: flex; justify-content: space-between; gap: 12px; padding: 16px 18px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.summary-card strong { color: var(--color-text); font-size: 14px; }
.summary-card p { display: flex; align-items: baseline; gap: 4px; margin: 8px 0 4px; }
.summary-card b { font-size: 28px; line-height: 1; }
.summary-card span, .summary-card small { color: var(--color-text-tertiary); }
.summary-icon { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; border-radius: 8px; background: var(--main-50); color: var(--main-700); }
.summary-icon[data-kind='review_notes'] { background: var(--color-warning-50); color: var(--color-warning-700); }
.summary-icon[data-kind='personal_generated'] { background: var(--color-info-50); color: var(--color-info-700); }
.summary-icon[data-kind='enterprise_generated'] { background: var(--gray-100); color: var(--color-success-700, var(--main-700)); }
.summary-icon[data-kind='case_shares'] { background: var(--main-50); color: var(--main-700); }
.traffic-panel { padding: 16px; border: 1px solid var(--gray-150); border-radius: 8px; }
.column-settings { position: fixed; right: 28px; bottom: 24px; z-index: 20; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.column-settings-toggle { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; border: 1px solid var(--gray-200); border-radius: 18px; background: var(--gray-0); color: var(--color-text-secondary); box-shadow: 0 4px 16px rgb(15 23 42 / 8%); }
.column-settings-toggle[aria-expanded='true'] { color: var(--main-color); border-color: var(--main-color); }
.column-settings-panel { width: min(460px, calc(100vw - 48px)); padding: 14px 14px 12px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); box-shadow: 0 8px 24px rgb(15 23 42 / 12%); }
.column-settings-panel header, .column-settings-panel footer { display: flex; align-items: center; justify-content: space-between; }
.column-settings-panel header strong { color: var(--color-text); font-size: 14px; }
.column-settings-panel header button { border: 0; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.column-settings-panel ul { max-height: 320px; margin: 8px 0 12px; padding: 0; overflow: auto; list-style: none; }
.column-settings-head, .column-settings-panel li { display: grid; grid-template-columns: 20px 36px minmax(72px, 1fr) 72px 36px 36px; align-items: center; gap: 8px; }
.column-settings-head { margin-top: 12px; color: var(--color-text-tertiary); font-size: 12px; }
.column-settings-panel li { min-height: 36px; }
.column-grip { display: inline-flex; padding: 0; border: 0; background: transparent; color: var(--gray-400); cursor: grab; }
.column-name { overflow: hidden; color: var(--color-text); font-size: 13px; white-space: nowrap; text-overflow: ellipsis; }
.column-settings-panel footer { justify-content: flex-end; gap: 8px; }
.traffic-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 16px; }
.traffic-toolbar :deep(.ant-input-affix-wrapper) { width: 220px; }
.traffic-toolbar :deep(.ant-select) { width: 180px; }
.date-range-label { color: var(--color-text-secondary); font-size: 14px; white-space: nowrap; }
.date-range-filter { width: 260px; }
@media (max-width: 1300px) { .summary-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 1100px) { .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .summary-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
