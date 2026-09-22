<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Search } from 'lucide-vue-next'

import { businessVariableApi } from '@/apis/business_variable_api'
import { contentTypeApi } from '@/apis/content_type_api'
import { variableApi } from '@/apis/variable_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const SERVICE_ENTRIES = [
  { value: '装修家居', label: '装修家居' },
  { value: '好评笔记', label: '好评笔记' }
]

const PORT_OPTIONS = [
  { value: 'pc', label: 'PC' },
  { value: 'app', label: '小程序' }
]

const emptyForm = () => ({
  service_entry: '装修家居',
  content_type_id: undefined,
  variable_id: undefined,
  ports: ['pc', 'app'],
  required: true,
  enabled: true
})

const loading = ref(false)
const saving = ref(false)
const togglingId = ref('')
const keywordInput = ref('')
const keyword = ref('')
const contentTypeFilter = ref(undefined)
const page = ref(1)
const pageSize = ref(20)
const items = ref([])
const contentTypes = ref([])
const variables = ref([])
const modalOpen = ref(false)
const editingId = ref('')
const activeServiceEntry = ref('装修家居')
const form = reactive(emptyForm())

const needsContentType = computed(() => activeServiceEntry.value !== '好评笔记')
const modalTitle = computed(() => (editingId.value ? '编辑业务变量' : '新增业务变量'))
const displayedItems = computed(() =>
  items.value.filter((item) => item.service_entry === activeServiceEntry.value)
)

const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const contentTypeOptions = computed(() =>
  contentTypes.value.map((item) => ({ value: item.id, label: item.name }))
)
const variableOptions = computed(() => {
  const nameCounts = variables.value.reduce((acc, item) => {
    const name = item.name || ''
    acc[name] = (acc[name] || 0) + 1
    return acc
  }, {})
  return variables.value.map((item) => {
    const name = item.name || ''
    const needsEntry = (nameCounts[name] || 0) > 1
    return {
      value: item.id,
      label: needsEntry ? `${name}（${item.service_entry}）` : name
    }
  })
})

const loadOptions = async () => {
  const [typeResponse, variableResponse] = await Promise.all([
    contentTypeApi.listContentTypes({}),
    variableApi.listVariables({})
  ])
  contentTypes.value = typeResponse.content_types || []
  variables.value = variableResponse.variables || []
}

const loadItems = async () => {
  loading.value = true
  try {
    const response = await businessVariableApi.listBusinessVariables({
      keyword: keyword.value,
      content_type_id: needsContentType.value ? contentTypeFilter.value : undefined
    })
    items.value = response.business_variables || []
  } catch (error) {
    message.error(error.message || '加载业务变量失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  page.value = 1
  void loadItems()
}

const handleContentTypeFilterChange = () => {
  page.value = 1
  void loadItems()
}

const handleTableChange = (pagination) => {
  page.value = pagination.current
  pageSize.value = pagination.pageSize
}

const openCreate = () => {
  editingId.value = ''
  Object.assign(form, emptyForm())
  form.service_entry = activeServiceEntry.value
  modalOpen.value = true
}

const openEdit = (item) => {
  editingId.value = item.id
  Object.assign(form, {
    service_entry: item.service_entry || activeServiceEntry.value,
    content_type_id: item.content_type_id || undefined,
    variable_id: item.variable_id,
    ports: Array.isArray(item.ports) && item.ports.length ? [...item.ports] : ['pc', 'app'],
    required: Boolean(item.required),
    enabled: Boolean(item.enabled)
  })
  modalOpen.value = true
}

const closeModal = () => {
  modalOpen.value = false
  editingId.value = ''
  Object.assign(form, emptyForm())
  form.service_entry = activeServiceEntry.value
}

const selectServiceEntry = (entry) => {
  activeServiceEntry.value = entry
  contentTypeFilter.value = undefined
  page.value = 1
  void loadItems()
}

const saveItem = async () => {
  form.service_entry = activeServiceEntry.value
  if (needsContentType.value && !form.content_type_id) {
    message.warning('请选择内容类型')
    return
  }
  if (!form.variable_id) {
    message.warning('请选择业务参数')
    return
  }
  if (!form.ports.length) {
    message.warning('请选择端口')
    return
  }
  saving.value = true
  try {
    const payload = {
      content_type_id: needsContentType.value ? form.content_type_id : null,
      variable_id: form.variable_id,
      ports: form.ports,
      required: form.required,
      enabled: form.enabled
    }
    if (editingId.value) {
      await businessVariableApi.updateBusinessVariable(editingId.value, payload)
      message.success('业务变量已更新')
    } else {
      await businessVariableApi.createBusinessVariable({
        service_entry: activeServiceEntry.value,
        ...payload
      })
      message.success('业务变量已创建')
    }
    closeModal()
    await loadItems()
  } catch (error) {
    message.error(error.message || '保存业务变量失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (item, enabled) => {
  togglingId.value = item.id
  try {
    await businessVariableApi.updateBusinessVariable(item.id, { enabled })
    item.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeItem = (item) => {
  const label =
    item.content_type_name && item.content_type_name !== '-'
      ? `${item.content_type_name} / ${item.variable_name}`
      : item.variable_name
  Modal.confirm({
    title: `删除业务变量「${label}」`,
    content: '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await businessVariableApi.deleteBusinessVariable(item.id)
        message.success('业务变量已删除')
        await loadItems()
      } catch (error) {
        message.error(error.message || '删除业务变量失败')
        return Promise.reject(error)
      }
    }
  })
}

watch(
  () => activeServiceEntry.value,
  () => {
    form.service_entry = activeServiceEntry.value
    form.content_type_id = undefined
    form.variable_id = undefined
  }
)

onMounted(async () => {
  try {
    await loadOptions()
  } catch (error) {
    message.error(error.message || '加载选项失败')
  }
  await loadItems()
})

watch([displayedItems, pageSize], () => {
  const maxPage = Math.max(1, Math.ceil(displayedItems.value.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})
</script>

<template>
  <div class="business-variable-config-view">
    <PageHeader title="业务变量配置" :show-border="true" />

    <div class="business-variable-config-content">
      <div class="toolbar">
        <div class="toolbar-filters">
          <a-select
            v-if="needsContentType"
            v-model:value="contentTypeFilter"
            class="content-type-filter"
            allow-clear
            placeholder="内容类型"
            show-search
            option-filter-prop="label"
            :options="contentTypeOptions"
            @change="handleContentTypeFilterChange"
          />
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
        </div>
        <a-button type="primary" class="add-btn" @click="openCreate">新增</a-button>
      </div>

      <nav class="service-entry-tabs" aria-label="服务入口">
        <button
          v-for="entry in SERVICE_ENTRIES"
          :key="entry.value"
          type="button"
          class="service-entry-tab"
          :class="{ active: activeServiceEntry === entry.value }"
          @click="selectServiceEntry(entry.value)"
        >
          {{ entry.label }}
        </button>
      </nav>

      <a-table
        class="data-table"
        :data-source="displayedItems"
        :loading="loading"
        :pagination="tablePagination"
        :bordered="true"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72" align="center">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="内容类型" data-index="content_type_name" key="content_type_name" :width="160" />
        <a-table-column title="服务入口" data-index="service_entry" key="service_entry" :width="120" />
        <a-table-column title="端口" data-index="ports_label" key="ports_label" :width="140" />
        <a-table-column title="业务参数" data-index="variable_name" key="variable_name" :width="140" />
        <a-table-column title="是否必填" key="required" :width="100" align="center">
          <template #default="{ record }">{{ record.required ? '是' : '否' }}</template>
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
        <a-table-column title="操作" key="actions" :width="148" align="center">
          <template #default="{ record }">
            <div class="row-actions">
              <a-button type="link" @click="openEdit(record)">编辑</a-button>
              <a-button type="link" @click="removeItem(record)">删除</a-button>
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
        class="form"
        :model="form"
        :label-col="{ style: { width: '92px' } }"
        :wrapper-col="{ style: { flex: 1 } }"
      >
        <a-form-item v-if="needsContentType" label="内容类型" required>
          <a-select
            v-model:value="form.content_type_id"
            placeholder="请选择内容类型"
            allow-clear
            show-search
            option-filter-prop="label"
            :options="contentTypeOptions"
          />
        </a-form-item>
        <a-form-item label="业务参数" required>
          <a-select
            v-model:value="form.variable_id"
            placeholder="请选择业务参数"
            allow-clear
            show-search
            option-filter-prop="label"
            :options="variableOptions"
          />
        </a-form-item>
        <a-form-item label="端口" required>
          <a-checkbox-group v-model:value="form.ports" class="port-checks">
            <a-checkbox v-for="option in PORT_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </a-checkbox>
          </a-checkbox-group>
        </a-form-item>
        <a-form-item label="是否必填" required>
          <a-radio-group v-model:value="form.required">
            <a-radio :value="true">是</a-radio>
            <a-radio :value="false">否</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="状态" required>
          <a-radio-group v-model:value="form.enabled">
            <a-radio :value="true">启用</a-radio>
            <a-radio :value="false">禁用</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
      <div class="form-footer">
        <a-button type="primary" @click="closeModal">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveItem">确定</a-button>
      </div>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.business-variable-config-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.business-variable-config-content {
  padding: 20px var(--page-padding) 32px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;

  .toolbar-filters {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    flex: 1;
  }

  .content-type-filter {
    width: 200px;
  }

  .search-input {
    width: 360px;
    max-width: 100%;

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

.service-entry-tabs {
  display: flex;
  gap: 32px;
  margin-bottom: 0;
  padding: 0 4px;
  border-bottom: 1px solid var(--gray-150);
}

.service-entry-tab {
  position: relative;
  padding: 10px 2px 12px;
  border: none;
  background: transparent;
  color: var(--gray-500);
  font-size: 14px;
  font-weight: 500;
  line-height: 22px;
  cursor: pointer;

  &:hover {
    color: var(--gray-800);
  }

  &.active {
    color: var(--main-color);

    &::after {
      content: '';
      position: absolute;
      left: 0;
      right: 0;
      bottom: -1px;
      height: 2px;
      background: var(--main-color);
      border-radius: 1px;
    }
  }
}

.data-table {
  :deep(.ant-table) {
    background: var(--gray-0);
  }

  :deep(.ant-table-container) {
    border-radius: 0;
    border-color: var(--gray-150);
    border-top: none;
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
  gap: 4px;
}

.form {
  margin-top: 8px;
}

.port-checks {
  display: flex;
  gap: 16px;
}

.form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}
</style>
