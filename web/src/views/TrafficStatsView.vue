<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { FileText, Image, Images, Search, Star } from 'lucide-vue-next'

import { employeeApi } from '@/apis/employee_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const router = useRouter()
const loading = ref(false)
const exporting = ref(false)
const keywordInput = ref('')
const keyword = ref('')
const branch = ref()
const department = ref()
const branches = ref([])
const departments = ref([])
const employees = ref([])
const summary = ref({
  decoration: 0,
  review_notes: 0,
  personal_generated: 0,
  enterprise_generated: 0
})
const page = ref(1)
const pageSize = ref(10)

const cards = computed(() => [
  { key: 'decoration', label: '装修家居', value: summary.value.decoration, unit: '篇', hint: '已生成的装修家居内容', icon: Star },
  { key: 'review_notes', label: '好评笔记', value: summary.value.review_notes, unit: '篇', hint: '已生成的好评笔记', icon: FileText },
  { key: 'personal_generated', label: '生图（个人素材）', value: summary.value.personal_generated, unit: '张', hint: '个人 AI 生图图库', icon: Image },
  { key: 'enterprise_generated', label: '生图（企业共享）', value: summary.value.enterprise_generated, unit: '张', hint: '企业共享生图图库', icon: Images }
])

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
      department: department.value
    })
    summary.value = response.summary || summary.value
    branches.value = response.branches || []
    departments.value = response.departments || []
    employees.value = response.employees || []
  } catch (error) {
    message.error(error.message || '加载流量统计失败')
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
  page.value = 1
  void load()
}

const handleTableChange = (pagination) => {
  page.value = pagination.current
  pageSize.value = pagination.pageSize
}

const exportStats = async () => {
  exporting.value = true
  try {
    const response = await employeeApi.exportTrafficStats({
      keyword: keyword.value,
      branch: branch.value,
      department: department.value
    })
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = '流量统计.xlsx'
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
        <a-button @click="reset">重置</a-button>
        <a-button type="primary" :loading="exporting" @click="exportStats">导出</a-button>
      </div>
      <a-table
        :data-source="employees"
        :loading="loading"
        :pagination="tablePagination"
        :scroll="{ x: 'max-content' }"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="姓名" data-index="name" key="name" />
        <a-table-column title="手机号码" data-index="login_account" key="login_account" />
        <a-table-column title="员工编码" data-index="employee_code" key="employee_code" />
        <a-table-column title="装修家居" key="decoration_count" align="center">
          <template #default="{ record }">
            <a-button type="link" @click="openHistory(record, '装修家居')">{{ record.decoration_count }}</a-button>
          </template>
        </a-table-column>
        <a-table-column title="好评笔记" key="review_notes_count" align="center">
          <template #default="{ record }">
            <a-button type="link" @click="openHistory(record, '好评笔记')">{{ record.review_notes_count }}</a-button>
          </template>
        </a-table-column>
        <a-table-column title="生图（个人素材）" key="personal_generated_count" align="center">
          <template #default="{ record }">
            <a-button type="link" @click="openGenerated(record)">{{ record.personal_generated_count }}</a-button>
          </template>
        </a-table-column>
        <a-table-column title="生图（企业共享）" key="enterprise_generated_count" align="center">
          <template #default="{ record }">
            <a-button type="link" @click="openGenerated(record, 'enterprise')">{{ record.enterprise_generated_count }}</a-button>
          </template>
        </a-table-column>
        <a-table-column title="所属门店" data-index="current_branch" key="current_branch" />
      </a-table>
    </section>
  </div>
</template>

<style scoped lang="less">
.traffic-page { min-height: 100%; padding: 8px 20px 24px; background: var(--gray-0); }
.page-hint { margin: 8px 0 16px; color: var(--color-text-tertiary); }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 18px; }
.summary-card { display: flex; justify-content: space-between; gap: 12px; padding: 16px 18px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.summary-card strong { color: var(--color-text); font-size: 14px; }
.summary-card p { display: flex; align-items: baseline; gap: 4px; margin: 8px 0 4px; }
.summary-card b { font-size: 28px; line-height: 1; }
.summary-card span, .summary-card small { color: var(--color-text-tertiary); }
.summary-icon { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; border-radius: 8px; background: var(--main-50); color: var(--main-700); }
.summary-icon[data-kind='review_notes'] { background: var(--color-warning-50); color: var(--color-warning-700); }
.summary-icon[data-kind='personal_generated'] { background: var(--color-info-50); color: var(--color-info-700); }
.summary-icon[data-kind='enterprise_generated'] { background: var(--gray-100); color: var(--color-success-700, var(--main-700)); }
.traffic-panel { padding: 16px; border: 1px solid var(--gray-150); border-radius: 8px; }
.traffic-toolbar { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.traffic-toolbar :deep(.ant-input-affix-wrapper) { width: 220px; }
.traffic-toolbar :deep(.ant-select) { width: 180px; }
@media (max-width: 1100px) { .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .summary-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
