<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, Search } from 'lucide-vue-next'

import { materialAudienceApi } from '@/apis/material_audience_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const emptyForm = () => ({
  code: '',
  name: '',
  match_keywords_text: '',
  sort_order: 100,
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

const modalTitle = computed(() => (editingId.value ? '编辑图库受众' : '新增图库受众'))
const filteredItems = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return items.value
  return items.value.filter((item) =>
    [item.name, item.code, ...(item.match_keywords || [])].join(' ').toLowerCase().includes(text)
  )
})
const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const parseKeywords = (text) =>
  String(text || '')
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)

const formatCreatedAt = (value) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (num) => String(num).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const loadItems = async () => {
  loading.value = true
  try {
    const response = await materialAudienceApi.listAudiences()
    items.value = response.audiences || []
  } catch (error) {
    message.error(error.message || '加载图库受众失败')
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

const openCreate = () => {
  editingId.value = ''
  Object.assign(form, emptyForm())
  modalOpen.value = true
}

const openEdit = (item) => {
  editingId.value = item.id
  Object.assign(form, {
    code: item.code,
    name: item.name,
    match_keywords_text: (item.match_keywords || []).join('，'),
    sort_order: item.sort_order,
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
  if (!name) {
    message.warning('请输入受众名称')
    return
  }
  const keywords = parseKeywords(form.match_keywords_text)
  const editing = items.value.find((item) => item.id === editingId.value)
  if (!editing?.is_default && !keywords.length) {
    message.warning('非默认受众至少填写一个分部匹配关键词')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await materialAudienceApi.updateAudience(editingId.value, {
        name,
        match_keywords: editing?.is_default ? [] : keywords,
        sort_order: form.sort_order,
        enabled: form.enabled
      })
      message.success('图库受众已更新')
    } else {
      const code = form.code.trim().toLowerCase()
      if (!code) {
        message.warning('请输入受众编码')
        return
      }
      await materialAudienceApi.createAudience({
        code,
        name,
        match_keywords: keywords,
        sort_order: form.sort_order,
        enabled: form.enabled
      })
      message.success('图库受众已创建')
    }
    closeModal()
    await loadItems()
  } catch (error) {
    message.error(error.message || '保存图库受众失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (item, enabled) => {
  togglingId.value = item.id
  try {
    await materialAudienceApi.updateAudience(item.id, { enabled })
    item.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeItem = (item) => {
  Modal.confirm({
    title: `删除图库受众「${item.name}」`,
    content: '删除后不可恢复。仍有图库归属该受众时无法删除。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await materialAudienceApi.deleteAudience(item.id)
        message.success('图库受众已删除')
        await loadItems()
      } catch (error) {
        message.error(error.message || '删除图库受众失败')
        return Promise.reject(error)
      }
    }
  })
}

onMounted(loadItems)

watch(filteredItems, (list) => {
  const maxPage = Math.max(1, Math.ceil(list.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})
</script>

<template>
  <div class="material-audience-config-view">
    <PageHeader title="企业图库受众" :show-border="true" />

    <div class="material-audience-config-content">
      <p class="hint">
        按员工「当前分部」包含匹配划分企业共享图库可见范围；命中事业部受众后只看该受众图库，与默认员工互斥。管理员可看全部。
      </p>
      <div class="toolbar">
        <a-input
          v-model:value="keywordInput"
          class="search-input"
          placeholder="搜索名称、编码或关键词"
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
        class="material-audience-table"
        :data-source="filteredItems"
        :loading="loading"
        :pagination="tablePagination"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="名称" data-index="name" key="name" />
        <a-table-column title="编码" data-index="code" key="code" :width="140" />
        <a-table-column title="分部匹配关键词" key="match_keywords">
          <template #default="{ record }">
            <span v-if="record.is_default">（默认：未命中其他规则）</span>
            <span v-else>{{ (record.match_keywords || []).join('，') || '-' }}</span>
          </template>
        </a-table-column>
        <a-table-column title="排序" data-index="sort_order" key="sort_order" :width="88" />
        <a-table-column title="状态" key="enabled" :width="140">
          <template #default="{ record }">
            <div class="status-cell">
              <a-switch
                size="small"
                :checked="record.enabled"
                :disabled="record.is_default"
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
            <a-button type="link" @click="openEdit(record)">编辑</a-button>
            <a-button
              v-if="!record.is_default"
              type="link"
              danger
              @click="removeItem(record)"
            >删除</a-button>
          </template>
        </a-table-column>
      </a-table>
    </div>

    <a-modal
      v-model:open="modalOpen"
      :title="modalTitle"
      :confirm-loading="saving"
      ok-text="保存"
      destroy-on-close
      @ok="saveItem"
      @cancel="closeModal"
    >
      <div class="form">
        <label v-if="!editingId">
          <span>编码 <b>*</b></span>
          <a-input v-model:value="form.code" maxlength="64" placeholder="例如 softfurnish" />
        </label>
        <label>
          <span>名称 <b>*</b></span>
          <a-input v-model:value="form.name" maxlength="80" placeholder="例如 软装事业部" />
        </label>
        <label v-if="!(editingId && items.find((item) => item.id === editingId)?.is_default)">
          <span>分部匹配关键词 <b>*</b></span>
          <a-textarea
            v-model:value="form.match_keywords_text"
            :rows="3"
            placeholder="用逗号分隔，员工当前分部包含任一关键词即命中"
          />
        </label>
        <label>
          <span>排序</span>
          <a-input-number v-model:value="form.sort_order" :min="0" :max="100000" style="width: 100%" />
        </label>
        <label class="switch-row">
          <span>启用</span>
          <a-switch v-model:checked="form.enabled" />
        </label>
      </div>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.material-audience-config-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
}

.material-audience-config-content {
  flex: 1;
  overflow: auto;
  padding: 16px var(--page-padding) 32px;
}

.hint {
  margin: 0 0 14px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

.search-input {
  max-width: 320px;
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-on {
  color: var(--color-success-700);
}

.status-off {
  color: var(--color-text-tertiary);
}

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--color-text);
  font-weight: 500;
}

.form b {
  color: var(--color-error-700);
  font-weight: 600;
}

.switch-row {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
}
</style>
