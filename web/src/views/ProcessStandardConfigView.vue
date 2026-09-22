<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Search } from 'lucide-vue-next'

import { processStandardApi } from '@/apis/process_standard_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const emptyForm = () => ({
  name: '',
  detail: '',
  enabled: true
})

const loading = ref(false)
const saving = ref(false)
const togglingId = ref('')
const keywordInput = ref('')
const keyword = ref('')
const nameFilter = ref(undefined)
const nameOptions = ref([])
const page = ref(1)
const pageSize = ref(20)
const items = ref([])
const modalOpen = ref(false)
const editingId = ref('')
const form = reactive(emptyForm())

const modalTitle = computed(() => (editingId.value ? '编辑工艺标准' : '新增工艺标准'))
const nameSelectOptions = computed(() => nameOptions.value.map((name) => ({ value: name, label: name })))
const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const loadItems = async () => {
  loading.value = true
  try {
    const response = await processStandardApi.listProcessStandards({
      keyword: keyword.value,
      name: nameFilter.value
    })
    items.value = response.process_standards || []
    nameOptions.value = response.names || []
  } catch (error) {
    message.error(error.message || '加载工艺标准失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  page.value = 1
  void loadItems()
}

const handleNameFilterChange = () => {
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
  if (nameFilter.value) form.name = nameFilter.value
  modalOpen.value = true
}

const openEdit = (item) => {
  editingId.value = item.id
  Object.assign(form, {
    name: item.name,
    detail: item.detail,
    enabled: item.enabled
  })
  modalOpen.value = true
}

const closeModal = () => {
  modalOpen.value = false
  editingId.value = ''
  Object.assign(form, emptyForm())
}

const saveItem = async () => {
  const name = form.name.trim()
  const detail = form.detail.trim()
  if (!name) {
    message.warning('请输入工艺名称')
    return
  }
  if (!detail) {
    message.warning('请输入工艺详情')
    return
  }
  saving.value = true
  try {
    const payload = { name, detail, enabled: form.enabled }
    if (editingId.value) {
      await processStandardApi.updateProcessStandard(editingId.value, payload)
      message.success('工艺标准已更新')
    } else {
      await processStandardApi.createProcessStandard(payload)
      message.success('工艺标准已创建')
    }
    closeModal()
    await loadItems()
  } catch (error) {
    message.error(error.message || '保存工艺标准失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (item, enabled) => {
  togglingId.value = item.id
  try {
    await processStandardApi.updateProcessStandard(item.id, { enabled })
    item.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeItem = (item) => {
  Modal.confirm({
    title: `删除工艺标准「${item.detail}」`,
    content: '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await processStandardApi.deleteProcessStandard(item.id)
        message.success('工艺标准已删除')
        await loadItems()
      } catch (error) {
        message.error(error.message || '删除工艺标准失败')
        return Promise.reject(error)
      }
    }
  })
}

onMounted(loadItems)

watch([items, pageSize], () => {
  const maxPage = Math.max(1, Math.ceil(items.value.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})
</script>

<template>
  <div class="process-standard-view">
    <PageHeader title="工艺标准列表" :show-border="true" />

    <div class="process-standard-content">
      <div class="toolbar">
        <div class="toolbar-filters">
          <a-select
            v-model:value="nameFilter"
            class="name-filter"
            allow-clear
            placeholder="工艺名称"
            show-search
            option-filter-prop="label"
            :options="nameSelectOptions"
            @change="handleNameFilterChange"
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

      <a-table
        class="data-table"
        :data-source="items"
        :loading="loading"
        :pagination="tablePagination"
        :bordered="true"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72" align="center">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="工艺名称" data-index="name" key="name" :width="180" />
        <a-table-column title="工艺详情" data-index="detail" key="detail" />
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
        <a-form-item label="工艺名称" required>
          <a-input v-model:value="form.name" placeholder="请输入工艺名称" allow-clear />
        </a-form-item>
        <a-form-item label="工艺详情" required>
          <a-input v-model:value="form.detail" placeholder="请输入工艺详情" allow-clear />
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
.process-standard-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.process-standard-content {
  padding: 20px var(--page-padding) 32px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;

  .toolbar-filters {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  .name-filter {
    width: 200px;
  }

  .search-input {
    width: 280px;

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

.data-table {
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
    text-align: center;
  }

  :deep(.ant-table-tbody > tr > td) {
    padding: 12px 16px;
    color: var(--gray-900);
    border-color: var(--gray-150);
    text-align: center;
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

.form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}
</style>
