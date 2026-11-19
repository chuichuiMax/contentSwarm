<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, Search } from 'lucide-vue-next'

import { contentApi } from '@/apis/content_api'
import { contentTypeApi } from '@/apis/content_type_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const loading = ref(false)
const keywordInput = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const covers = ref([])
const statusSaving = reactive(new Set())
const promptOpen = ref(false)
const promptTitle = ref('')
const promptText = ref('')

const promptOf = (item) => item.prompt_body || item.description || ''
const coverName = (item) =>
  item.variant === 'smart-random' ? item.label || item.title : item.title || item.label

const filteredCovers = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return covers.value
  return covers.value.filter((item) => {
    const types = (item.content_type_names || []).join('；')
    return [item.title, item.label, promptOf(item), types].some((value) =>
      String(value || '').toLowerCase().includes(text)
    )
  })
})

const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const loadCovers = async () => {
  loading.value = true
  try {
    const response = await contentApi.listAiCoverManage()
    covers.value = response.covers || []
  } catch (error) {
    message.error(error.message || '加载封面模板失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  page.value = 1
}

const handleTableChange = (pagination) => {
  page.value = pagination.current
  pageSize.value = pagination.pageSize
}

const openPrompt = (item) => {
  promptTitle.value = coverName(item) || '封面提示词'
  promptText.value = promptOf(item)
  promptOpen.value = true
}

const showFullPrompt = (item) => promptOf(item).length > 72

const formatCreatedAt = (value) => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (num) => String(num).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const createOpen = ref(false)
const saving = ref(false)
const editingId = ref('')
const contentTypeOptions = ref([])
const fileInput = ref(null)
const form = reactive({
  enabled: true,
  name: '',
  prompt: '',
  contentTypeNames: [],
  file: null,
  previewUrl: '',
  previewObject: false
})
const modalTitle = computed(() => (editingId.value ? '编辑封面' : '新增封面'))

const resetForm = () => {
  form.enabled = true
  if (form.previewObject && form.previewUrl) URL.revokeObjectURL(form.previewUrl)
  form.name = ''
  form.prompt = ''
  form.contentTypeNames = []
  form.file = null
  form.previewUrl = ''
  form.previewObject = false
}

const loadContentTypeOptions = async () => {
  if (contentTypeOptions.value.length) return
  try {
    const response = await contentTypeApi.listContentTypes()
    contentTypeOptions.value = (response.content_types || []).map((item) => ({
      value: item.name,
      label: item.name
    }))
  } catch (error) {
    message.error(error.message || '加载内容类型失败')
  }
}

const openCreate = async () => {
  editingId.value = ''
  resetForm()
  createOpen.value = true
  await loadContentTypeOptions()
}

const openEdit = async (item) => {
  resetForm()
  editingId.value = item.variant
  form.enabled = item.enabled !== false
  form.name = coverName(item)
  form.prompt = promptOf(item)
  form.contentTypeNames = [...(item.content_type_names || [])]
  form.previewUrl = item.preview_url || ''
  createOpen.value = true
  await loadContentTypeOptions()
}

const closeCreate = () => {
  createOpen.value = false
  editingId.value = ''
  resetForm()
}

const pickCoverFile = (event) => {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  const suffix = file.name.split('.').pop()?.toLowerCase()
  if (!['png', 'jpg', 'jpeg', 'webp'].includes(suffix)) {
    message.warning('封面模板仅支持 PNG、JPG、WebP')
    return
  }
  if (form.previewObject && form.previewUrl) URL.revokeObjectURL(form.previewUrl)
  form.file = file
  form.previewUrl = URL.createObjectURL(file)
  form.previewObject = true
}

const saveCover = async () => {
  if (!form.name.trim()) {
    message.warning('请输入封面名称')
    return
  }
  if (!form.prompt.trim()) {
    message.warning('请输入封面提示词')
    return
  }
  if (!form.contentTypeNames.length) {
    message.warning('请选择内容类型')
    return
  }
  if (!editingId.value && !form.file) {
    message.warning('请上传封面模板')
    return
  }
  const body = new FormData()
  body.append('name', form.name.trim())
  body.append('prompt', form.prompt.trim())
  if (editingId.value !== 'smart-random') body.append('enabled', String(form.enabled))
  form.contentTypeNames.forEach((name) => body.append('content_type_names', name))
  if (form.file) body.append('file', form.file, form.file.name)
  saving.value = true
  try {
    if (editingId.value) {
      await contentApi.updateAiCover(editingId.value, body)
      message.success('封面已更新')
    } else {
      await contentApi.createAiCover(body)
      message.success('封面已新增')
    }
    closeCreate()
    await loadCovers()
  } catch (error) {
    message.error(error.message || (editingId.value ? '更新封面失败' : '新增封面失败'))
  } finally {
    saving.value = false
  }
}

const removeCover = (item) => {
  const name = coverName(item)
  Modal.confirm({
    title: `删除封面「${name}」`,
    content: '删除后，内容生产选择 AI 封面时不再显示。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await contentApi.deleteAiCover(item.variant)
        message.success('封面已删除')
        await loadCovers()
      } catch (error) {
        message.error(error.message || '删除封面失败')
        return Promise.reject(error)
      }
    }
  })
}

const changeStatus = async (item, enabled) => {
  if (statusSaving.has(item.variant)) return
  statusSaving.add(item.variant)
  try {
    const response = await contentApi.updateAiCoverStatus(item.variant, enabled)
    item.enabled = response.cover.enabled
    message.success(item.enabled ? '封面已启用' : '封面已禁用')
  } catch (error) {
    message.error(error.message || '更新封面状态失败')
  } finally {
    statusSaving.delete(item.variant)
  }
}

onMounted(loadCovers)

watch(filteredCovers, (list) => {
  const maxPage = Math.max(1, Math.ceil(list.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})
</script>

<template>
  <div class="cover-manage-view">
    <PageHeader title="封面管理" :show-border="true" />

    <div class="cover-manage-content">
      <div class="cover-manage-toolbar">
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
        <p class="cover-manage-note">内容生产选择「AI封面」时仅展示启用的模板。上传后使用本地照片文件名，相同文件名不能重复上传。</p>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreate">
          <Plus :size="14" />
          新增封面
        </a-button>
      </div>

      <a-table
        class="cover-manage-table"
        :data-source="filteredCovers"
        :loading="loading"
        :pagination="tablePagination"
        row-key="variant"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="封面名称" key="title" :width="160">
          <template #default="{ record }">{{ coverName(record) }}</template>
        </a-table-column>
        <a-table-column title="封面提示词" key="prompt">
          <template #default="{ record }">
            <div class="prompt-cell">
              <p class="prompt-text">{{ promptOf(record) }}</p>
              <button
                v-if="showFullPrompt(record)"
                type="button"
                class="prompt-more"
                @click="openPrompt(record)"
              >
                查看全文
              </button>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="封面模板示例" key="preview" :width="140">
          <template #default="{ record }">
            <img
              class="cover-thumb"
              :src="record.preview_url"
              :alt="record.title || '封面模板'"
              loading="lazy"
            />
          </template>
        </a-table-column>
        <a-table-column title="关联内容类型" key="types" :width="240">
          <template #default="{ record }">
            <span class="type-text">{{ (record.content_type_names || []).join('；') }}</span>
          </template>
        </a-table-column>
        <a-table-column title="创建时间" key="created_at" :width="180">
          <template #default="{ record }">{{ formatCreatedAt(record.created_at) }}</template>
        </a-table-column>
        <a-table-column title="状态" key="enabled" :width="110">
          <template #default="{ record }">
            <span v-if="record.variant === 'smart-random'">—</span>
            <a-switch
              v-else
              :checked="record.enabled !== false"
              :loading="statusSaving.has(record.variant)"
              :disabled="statusSaving.has(record.variant)"
              :aria-label="`${coverName(record)}状态`"
              @change="changeStatus(record, $event)"
            />
          </template>
        </a-table-column>
        <a-table-column title="操作" key="actions" :width="160">
          <template #default="{ record }">
            <div class="row-actions">
              <a-button type="primary" size="small" :disabled="statusSaving.has(record.variant)" @click="openEdit(record)">编辑</a-button>
              <a-button type="primary" size="small" :disabled="statusSaving.has(record.variant)" @click="removeCover(record)">删除</a-button>
            </div>
          </template>
        </a-table-column>
      </a-table>
    </div>

    <a-modal
      v-model:open="createOpen"
      :title="modalTitle"
      :mask-closable="false"
      :footer="null"
      width="720px"
      @cancel="closeCreate"
    >
      <a-form class="cover-form" :label-col="{ style: { width: '128px' } }" :wrapper-col="{ style: { flex: 1 } }">
        <a-form-item label="封面名称" required>
          <a-input v-model:value="form.name" placeholder="请输入封面名称" allow-clear />
        </a-form-item>
        <a-form-item label="封面提示词" required>
          <a-textarea v-model:value="form.prompt" placeholder="请输入封面提示词" :rows="5" />
        </a-form-item>
        <a-form-item label="关联内容类型" required>
          <a-select
            v-model:value="form.contentTypeNames"
            mode="multiple"
            placeholder="请选择内容类型"
            :options="contentTypeOptions"
            allow-clear
          />
        </a-form-item>
        <a-form-item label="上传封面模板" :required="!editingId">
          <button type="button" class="upload-tile" @click="fileInput?.click()">
            <img v-if="form.previewUrl" :src="form.previewUrl" alt="封面模板" />
            <Plus v-else :size="22" />
          </button>
          <input
            ref="fileInput"
            class="upload-input"
            type="file"
            accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
            @change="pickCoverFile"
          />
          <p v-if="form.file" class="upload-name">{{ form.file.name }}</p>
        </a-form-item>
        <a-form-item v-if="editingId !== 'smart-random'" label="状态" required>
          <a-radio-group v-model:value="form.enabled">
            <a-radio :value="true">启用</a-radio>
            <a-radio :value="false">禁用</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
      <div class="cover-form-footer">
        <a-button type="primary" @click="closeCreate">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveCover">确定</a-button>
      </div>
    </a-modal>

    <a-modal v-model:open="promptOpen" :title="promptTitle" :footer="null" width="720px">
      <pre class="prompt-full">{{ promptText }}</pre>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.cover-manage-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.cover-manage-content {
  padding: 16px var(--page-padding) 32px;
}

.cover-manage-toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;

  .search-input {
    width: 280px;
    flex: none;
  }

  .lucide-icon-btn {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.cover-manage-note {
  margin: 0;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1.5;
}

.cover-manage-table {
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

.prompt-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  max-width: 420px;
}

.prompt-text {
  margin: 0;
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  white-space: pre-wrap;
  line-height: 1.5;
  color: var(--gray-800);
}

.prompt-more {
  align-self: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-color);
  cursor: pointer;
}

.cover-thumb {
  display: block;
  width: 72px;
  height: 96px;
  object-fit: cover;
  border-radius: 4px;
  background: var(--gray-50);
}

.row-actions {
  display: inline-flex;
  gap: 8px;
}

.type-text {
  line-height: 1.5;
}

.prompt-full {
  margin: 0;
  max-height: 60vh;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.6;
  color: var(--gray-900);
}

.cover-form {
  padding: 8px 8px 0;

  :deep(.ant-form-item) {
    margin-bottom: 18px;
  }

  :deep(.ant-input),
  :deep(.ant-input-affix-wrapper),
  :deep(.ant-select-selector) {
    border-radius: 8px;
  }
}

.upload-tile {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 104px;
  height: 104px;
  padding: 0;
  border: 1px dashed var(--gray-300);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-500);
  cursor: pointer;
  overflow: hidden;

  &:hover {
    border-color: var(--main-color);
    color: var(--main-color);
  }

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
}

.upload-input {
  display: none;
}

.upload-name {
  margin: 8px 0 0;
  color: var(--gray-600);
  font-size: 12px;
}

.cover-form-footer {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 8px 0 4px;

  :deep(.ant-btn) {
    min-width: 96px;
    height: 36px;
    border-radius: 8px;
  }
}
</style>
