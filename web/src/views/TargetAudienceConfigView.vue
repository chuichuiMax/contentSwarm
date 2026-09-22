<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, Search } from 'lucide-vue-next'

import { targetAudienceApi } from '@/apis/target_audience_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const emptyForm = () => ({
  name: '',
  enabled: true
})

const loading = ref(false)
const saving = ref(false)
const togglingId = ref('')
const keywordInput = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const items = ref([])
const modalOpen = ref(false)
const editingId = ref('')
const form = reactive(emptyForm())

const modalTitle = computed(() => (editingId.value ? '编辑目标人群类型' : '新增目标人群类型'))
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

const loadItems = async () => {
  loading.value = true
  try {
    const response = await targetAudienceApi.listTargetAudiences({ keyword: keyword.value })
    items.value = response.target_audiences || []
  } catch (error) {
    message.error(error.message || '加载目标人群失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
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
  modalOpen.value = true
}

const openEdit = (item) => {
  editingId.value = item.id
  Object.assign(form, { name: item.name, enabled: item.enabled })
  modalOpen.value = true
}

const closeModal = () => {
  modalOpen.value = false
  editingId.value = ''
  Object.assign(form, emptyForm())
}

const saveItem = async () => {
  const name = form.name.trim()
  if (!name) {
    message.warning('请输入目标人群类型名称')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await targetAudienceApi.updateTargetAudience(editingId.value, { name, enabled: form.enabled })
      message.success('目标人群已更新')
    } else {
      await targetAudienceApi.createTargetAudience({ name, enabled: form.enabled })
      message.success('目标人群已创建')
    }
    closeModal()
    await loadItems()
  } catch (error) {
    message.error(error.message || '保存目标人群失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (item, enabled) => {
  togglingId.value = item.id
  try {
    await targetAudienceApi.updateTargetAudience(item.id, { enabled })
    item.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeItem = (item) => {
  Modal.confirm({
    title: `删除目标人群「${item.name}」`,
    content: '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await targetAudienceApi.deleteTargetAudience(item.id)
        message.success('目标人群已删除')
        await loadItems()
      } catch (error) {
        message.error(error.message || '删除目标人群失败')
        return Promise.reject(error)
      }
    }
  })
}

onMounted(loadItems)

watch(items, (list) => {
  const maxPage = Math.max(1, Math.ceil(list.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})
</script>

<template>
  <div class="target-audience-config-view">
    <PageHeader title="目标人群配置" :show-border="true" />

    <div class="target-audience-config-content">
      <div class="toolbar">
        <a-input
          v-model:value="keywordInput"
          class="search-input"
          placeholder="输入搜索关键词"
          allow-clear
          @pressEnter="handleSearch"
          @clear="handleSearch"
        >
          <template #prefix><Search :size="14" /></template>
        </a-input>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreate">
          <Plus :size="14" />
          新增
        </a-button>
      </div>

      <a-table
        class="target-audience-table"
        :data-source="items"
        :loading="loading"
        :pagination="tablePagination"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="目标人群类型" data-index="name" key="name" />
        <a-table-column title="状态" key="enabled" :width="140">
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
        <a-table-column title="操作" key="actions" :width="180">
          <template #default="{ record }">
            <div class="row-actions">
              <a-button type="primary" size="small" @click="openEdit(record)">编辑</a-button>
              <a-button type="primary" size="small" @click="removeItem(record)">删除</a-button>
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
        class="target-audience-form"
        :model="form"
        :label-col="{ style: { width: '108px' } }"
        :wrapper-col="{ style: { flex: 1 } }"
      >
        <a-form-item label="目标人群名称" required>
          <a-input v-model:value="form.name" placeholder="请输入目标人群类型名称" allow-clear />
        </a-form-item>
        <a-form-item label="状态" required>
          <a-radio-group v-model:value="form.enabled" class="status-radios">
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
.target-audience-config-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.target-audience-config-content {
  padding: 16px var(--page-padding) 32px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 16px;

  .search-input {
    width: 280px;
  }

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.target-audience-table {
  :deep(.ant-table) {
    background: var(--gray-0);
  }

  :deep(.ant-table-thead > tr > th) {
    background: var(--gray-10);
    color: var(--gray-700);
    font-weight: 600;
  }

  :deep(.ant-table-pagination) {
    margin: 16px 0 0;
  }
}

.status-cell {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.status-on {
  color: var(--color-success-700);
}

.status-off {
  color: var(--gray-500);
}

.row-actions {
  display: inline-flex;
  gap: 8px;
}

.target-audience-form {
  padding: 8px 12px 0;

  :deep(.ant-form-item) {
    margin-bottom: 18px;
  }

  :deep(.ant-form-item-label > label) {
    color: var(--color-text);
  }

  :deep(.ant-form-item-label > label.ant-form-item-required:not(.ant-form-item-required-mark-optional)::before) {
    margin-inline-end: 4px;
    color: var(--color-error-500);
  }

  :deep(.ant-input-affix-wrapper),
  :deep(.ant-input) {
    border-radius: 20px;
  }

  .status-radios {
    display: flex;
    align-items: center;
    gap: 48px;
  }
}

.form-footer {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 8px 0 4px;

  :deep(.ant-btn) {
    min-width: 88px;
    height: 36px;
    border-radius: 8px;
  }
}
</style>
