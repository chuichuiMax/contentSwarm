<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Search } from 'lucide-vue-next'

import { variableApi } from '@/apis/variable_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const DEFAULT_SERVICE_ENTRY = '装修家居'
const DEFAULT_PORTS = ['pc', 'app']
const DEFAULT_EDITIONS = ['quick', 'pro']

const emptyForm = () => ({
  name: '',
  service_entry: DEFAULT_SERVICE_ENTRY,
  ports: [...DEFAULT_PORTS],
  editions: [...DEFAULT_EDITIONS],
  enabled: true
})

const loading = ref(false)
const saving = ref(false)
const togglingId = ref('')
const keywordInput = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const variables = ref([])
const modalOpen = ref(false)
const editingId = ref('')
const form = reactive(emptyForm())

const modalTitle = computed(() => (editingId.value ? '编辑业务参数' : '新增业务参数'))
const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const formatCreatedAt = (value) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (num) => String(num).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const loadVariables = async () => {
  loading.value = true
  try {
    const response = await variableApi.listVariables({ keyword: keyword.value })
    variables.value = response.variables || []
  } catch (error) {
    message.error(error.message || '加载业务参数失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  page.value = 1
  void loadVariables()
}

const handleTableChange = (pagination) => {
  page.value = pagination.current
  pageSize.value = pagination.pageSize
}

const openCreate = () => {
  editingId.value = ''
  Object.assign(form, emptyForm())
  modalOpen.value = true
}

const openEdit = (item) => {
  editingId.value = item.id
  Object.assign(form, {
    name: item.name,
    service_entry: item.service_entry || DEFAULT_SERVICE_ENTRY,
    ports: Array.isArray(item.ports) && item.ports.length ? [...item.ports] : [...DEFAULT_PORTS],
    editions: Array.isArray(item.editions) && item.editions.length ? [...item.editions] : [...DEFAULT_EDITIONS],
    enabled: item.enabled
  })
  modalOpen.value = true
}

const closeModal = () => {
  modalOpen.value = false
  editingId.value = ''
  Object.assign(form, emptyForm())
}

const saveVariable = async () => {
  const name = form.name.trim()
  if (!name) {
    message.warning('请输入参数名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name,
      service_entry: form.service_entry || DEFAULT_SERVICE_ENTRY,
      ports: form.ports.length ? form.ports : [...DEFAULT_PORTS],
      editions: form.editions.length ? form.editions : [...DEFAULT_EDITIONS],
      enabled: form.enabled
    }
    if (editingId.value) {
      await variableApi.updateVariable(editingId.value, payload)
      message.success('业务参数已更新')
    } else {
      await variableApi.createVariable(payload)
      message.success('业务参数已创建')
    }
    closeModal()
    await loadVariables()
  } catch (error) {
    message.error(error.message || '保存业务参数失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (item, enabled) => {
  togglingId.value = item.id
  try {
    await variableApi.updateVariable(item.id, { enabled })
    item.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeVariable = (item) => {
  Modal.confirm({
    title: `删除业务参数「${item.name}」`,
    content: '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await variableApi.deleteVariable(item.id)
        message.success('业务参数已删除')
        await loadVariables()
      } catch (error) {
        message.error(error.message || '删除业务参数失败')
        return Promise.reject(error)
      }
    }
  })
}

onMounted(loadVariables)

watch([variables, pageSize], () => {
  const maxPage = Math.max(1, Math.ceil(variables.value.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})
</script>

<template>
  <div class="variable-config-view">
    <PageHeader title="业务参数配置" :show-border="true" />

    <div class="variable-config-content">
      <div class="variable-toolbar">
        <a-input
          v-model:value="keywordInput"
          class="search-input"
          placeholder="输入搜索关键词"
          allow-clear
          @pressEnter="handleSearch"
          @clear="handleSearch"
        >
          <template #suffix>
            <button type="button" class="search-btn" aria-label="查询" @click="handleSearch">
              <Search :size="16" />
            </button>
          </template>
        </a-input>
        <a-button type="primary" class="add-btn" @click="openCreate">新增</a-button>
      </div>

      <a-table
        class="variable-table"
        :data-source="variables"
        :loading="loading"
        :pagination="tablePagination"
        :bordered="true"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="80" align="center">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="编码" data-index="variable_code" key="variable_code" :width="128" />
        <a-table-column title="业务参数" key="name" :width="160">
          <template #default="{ record }">{{ record.name }}</template>
        </a-table-column>
        <a-table-column title="状态" key="enabled" :width="132">
          <template #default="{ record }">
            <div class="status-cell">
              <a-switch
                size="small"
                :checked="record.enabled"
                :loading="togglingId === record.id"
                @change="(checked) => toggleEnabled(record, checked)"
              />
              <span :class="record.enabled ? 'status-on' : 'status-off'">
                {{ record.enabled ? '启用' : '禁用' }}
              </span>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="创建时间" key="created_at" :width="180">
          <template #default="{ record }">{{ formatCreatedAt(record.created_at) }}</template>
        </a-table-column>
        <a-table-column title="操作" key="actions" :width="168" align="center">
          <template #default="{ record }">
            <div class="row-actions">
              <a-button type="primary" size="small" @click="openEdit(record)">编辑</a-button>
              <a-button type="primary" size="small" @click="removeVariable(record)">删除</a-button>
            </div>
          </template>
        </a-table-column>
      </a-table>
    </div>

    <a-modal
      v-model:open="modalOpen"
      :title="modalTitle"
      :mask-closable="false"
      :footer="null"
      width="480px"
      @cancel="closeModal"
    >
      <a-form
        class="variable-form"
        :model="form"
        :label-col="{ style: { width: '92px' } }"
        :wrapper-col="{ style: { flex: 1 } }"
      >
        <a-form-item label="业务参数" required>
          <a-input v-model:value="form.name" placeholder="请输入业务参数名称" allow-clear />
        </a-form-item>
        <a-form-item label="状态" required>
          <a-radio-group v-model:value="form.enabled" class="status-radios">
            <a-radio :value="true">启用</a-radio>
            <a-radio :value="false">禁用</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
      <div class="variable-form-footer">
        <a-button type="primary" @click="closeModal">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveVariable">确定</a-button>
      </div>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.variable-config-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.variable-config-content {
  padding: 20px var(--page-padding) 32px;
}

.variable-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;

  .search-input {
    width: 360px;

    :deep(.ant-input-affix-wrapper) {
      height: 36px;
      padding-inline: 14px;
      border-radius: 8px;
      border-color: var(--gray-200);
      background: var(--gray-0);
    }
  }

  .search-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--gray-500);
    cursor: pointer;
  }

  .add-btn {
    min-width: 72px;
    height: 36px;
    padding: 0 20px;
    border-radius: 8px;
  }
}

.variable-table {
  :deep(.ant-table) {
    background: var(--gray-0);
  }

  :deep(.ant-table-container) {
    border-radius: 8px;
    border-color: var(--gray-150);
  }

  :deep(.ant-table-thead > tr > th) {
    padding: 12px 16px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-weight: 600;
    border-color: var(--gray-150);
  }

  :deep(.ant-table-tbody > tr > td) {
    padding: 12px 16px;
    color: var(--gray-900);
    border-color: var(--gray-150);
  }

  :deep(.ant-switch-checked) {
    background: var(--color-success-500);
  }

  :deep(.ant-table-pagination) {
    margin: 16px 0 0;
  }
}

.status-cell {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.status-on {
  color: var(--color-success-700);
}

.status-off {
  color: var(--gray-500);
}

.row-actions {
  display: inline-flex;
  justify-content: center;
  gap: 8px;
}

.variable-form {
  margin-top: 8px;
}

.status-radios {
  display: flex;
  gap: 16px;
}

.variable-form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}
</style>
