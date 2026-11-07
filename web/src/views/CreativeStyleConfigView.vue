<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, Search } from 'lucide-vue-next'

import { contentTypeApi } from '@/apis/content_type_api'
import { creativeStyleApi } from '@/apis/creative_style_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const emptyForm = () => ({
  content_type_id: undefined,
  name: '',
  description: '',
  prompt: '',
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
const styles = ref([])
const contentTypes = ref([])
const modalOpen = ref(false)
const promptOpen = ref(false)
const promptRecord = ref(null)
const editingId = ref('')
const form = reactive(emptyForm())

const modalTitle = computed(() => (editingId.value ? '编辑创作风格' : '新增创作风格'))
const contentTypeOptions = computed(() =>
  contentTypes.value.map((item) => ({ label: item.name, value: item.id }))
)
const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const promptPreview = (value) => String(value || '').replace(/\s+/g, ' ').trim()

const openPrompt = (item) => {
  promptRecord.value = item
  promptOpen.value = true
}

const formatCreatedAt = (value) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (num) => String(num).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const loadContentTypes = async () => {
  const response = await contentTypeApi.listContentTypes()
  contentTypes.value = response.content_types || []
}

const loadStyles = async () => {
  loading.value = true
  try {
    const response = await creativeStyleApi.listCreativeStyles({
      keyword: keyword.value,
      content_type_id: contentTypeFilter.value
    })
    styles.value = response.creative_styles || []
  } catch (error) {
    message.error(error.message || '加载创作风格失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  page.value = 1
  void loadStyles()
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
    content_type_id: item.content_type_id,
    name: item.name,
    description: item.description || '',
    prompt: item.prompt || '',
    enabled: item.enabled
  })
  modalOpen.value = true
}

const closeModal = () => {
  modalOpen.value = false
  editingId.value = ''
}

const saveStyle = async () => {
  if (!form.content_type_id) {
    message.warning('请选择内容类型')
    return
  }
  if (!form.name.trim()) {
    message.warning('请输入创作风格')
    return
  }
  saving.value = true
  try {
    const payload = {
      content_type_id: form.content_type_id,
      name: form.name.trim(),
      description: form.description.trim(),
      prompt: form.prompt,
      enabled: form.enabled
    }
    if (editingId.value) {
      await creativeStyleApi.updateCreativeStyle(editingId.value, payload)
      message.success('创作风格已更新')
    } else {
      await creativeStyleApi.createCreativeStyle(payload)
      message.success('创作风格已创建')
    }
    closeModal()
    await loadStyles()
  } catch (error) {
    message.error(error.message || '保存创作风格失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (item, checked) => {
  togglingId.value = item.id
  try {
    await creativeStyleApi.updateCreativeStyle(item.id, { enabled: checked })
    item.enabled = checked
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeStyle = (item) => {
  Modal.confirm({
    title: `删除创作风格「${item.name}」`,
    content: '删除后，内容创作里将不再出现这个风格。',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      try {
        await creativeStyleApi.deleteCreativeStyle(item.id)
        message.success('创作风格已删除')
        await loadStyles()
      } catch (error) {
        message.error(error.message || '删除创作风格失败')
      }
    }
  })
}

onMounted(async () => {
  try {
    await loadContentTypes()
  } catch (error) {
    message.error(error.message || '加载内容类型失败')
  }
  await loadStyles()
})

watch(styles, (list) => {
  const maxPage = Math.max(1, Math.ceil(list.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})
</script>

<template>
  <div class="creative-style-config-view">
    <PageHeader title="创作风格管理" :show-border="true" />

    <div class="creative-style-config-content">
      <div class="creative-style-toolbar">
        <div class="toolbar-filters">
          <a-select
            v-model:value="contentTypeFilter"
            allow-clear
            placeholder="全部内容类型"
            :options="contentTypeOptions"
            class="type-filter"
            @change="handleSearch"
          />
          <a-input
            v-model:value="keywordInput"
            class="search-input"
            placeholder="搜索创作风格或内容类型"
            allow-clear
            @pressEnter="handleSearch"
            @clear="handleSearch"
          >
            <template #prefix><Search :size="14" /></template>
          </a-input>
        </div>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreate">
          <Plus :size="14" />
          新增
        </a-button>
      </div>

      <a-table
        class="creative-style-table"
        :data-source="styles"
        :loading="loading"
        :pagination="tablePagination"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="内容类型" data-index="content_type_name" key="content_type_name" :width="160" />
        <a-table-column title="创作风格" data-index="name" key="name" :width="180" />
        <a-table-column title="创作风格描述" data-index="description" key="description" :width="220" ellipsis />
        <a-table-column title="文案提示词" key="prompt">
          <template #default="{ record }">
            <div class="prompt-cell">
              <span class="prompt-preview">{{ promptPreview(record.prompt) || '—' }}</span>
              <button v-if="record.prompt" type="button" class="prompt-origin-link" @click="openPrompt(record)">
                查看原文
              </button>
            </div>
          </template>
        </a-table-column>
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
        <a-table-column title="创建时间" key="created_at" :width="200">
          <template #default="{ record }">{{ formatCreatedAt(record.created_at) }}</template>
        </a-table-column>
        <a-table-column title="操作" key="actions" :width="180">
          <template #default="{ record }">
            <div class="row-actions">
              <a-button type="primary" size="small" @click="openEdit(record)">编辑</a-button>
              <a-button type="primary" size="small" @click="removeStyle(record)">删除</a-button>
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
      width="720px"
      @cancel="closeModal"
    >
      <a-form
        class="creative-style-form"
        :model="form"
        :label-col="{ style: { width: '112px' } }"
        :wrapper-col="{ style: { flex: 1 } }"
      >
        <a-form-item label="内容类型" required>
          <a-select
            v-model:value="form.content_type_id"
            placeholder="请选择内容类型"
            :options="contentTypeOptions"
          />
        </a-form-item>
        <a-form-item label="创作风格" required>
          <a-input v-model:value="form.name" placeholder="请输入创作风格" allow-clear />
        </a-form-item>
        <a-form-item label="创作风格描述">
          <a-input v-model:value="form.description" placeholder="选填，显示在创作风格旁" allow-clear />
        </a-form-item>
        <a-form-item label="文案提示词" required>
          <a-textarea v-model:value="form.prompt" :rows="12" placeholder="选择该风格后填入内容创作的生成提示词" />
        </a-form-item>
        <a-form-item label="状态" required>
          <a-radio-group v-model:value="form.enabled" class="status-radios">
            <a-radio :value="true">启用</a-radio>
            <a-radio :value="false">禁用</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
      <div class="creative-style-form-footer">
        <a-button type="primary" @click="closeModal">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveStyle">确定</a-button>
      </div>
    </a-modal>

    <a-modal v-model:open="promptOpen" title="文案提示词" :footer="null" width="720px">
      <p v-if="promptRecord" class="prompt-origin-name">{{ promptRecord.name }}</p>
      <pre class="prompt-origin">{{ promptRecord?.prompt || '暂无文案提示词' }}</pre>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.creative-style-config-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.creative-style-config-content {
  padding: 16px var(--page-padding) 32px;
}

.creative-style-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 16px;

  .toolbar-filters {
    display: flex;
    gap: 8px;
  }

  .type-filter {
    width: 200px;
  }

  .search-input {
    width: 280px;
  }

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.creative-style-table {
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

.prompt-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
}

.prompt-preview {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  color: var(--color-text);
  line-height: 1.5;
  white-space: normal;
  word-break: break-word;
}

.prompt-origin-link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-color);
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
}

.prompt-origin-name {
  margin: 0 0 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.prompt-origin {
  max-height: 480px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.7;
}

.creative-style-form {
  padding: 8px 12px 0;

  :deep(.ant-form-item) {
    margin-bottom: 18px;
  }

  :deep(.ant-form-item-label > label) {
    color: var(--color-text);
  }

  .status-radios {
    display: flex;
    align-items: center;
    gap: 48px;
  }
}

.creative-style-form-footer {
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
