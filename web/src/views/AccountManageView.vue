<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, Search, Zap } from 'lucide-vue-next'

import { accountApi } from '@/apis/account_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const ACCOUNT_TYPE_OPTIONS = [
  { value: 'enterprise', label: '企业号' },
  { value: 'personal', label: '个人号' }
]

const emptyForm = () => ({
  name: '',
  account_id: '',
  account_type: 'enterprise',
  following_count: 0,
  follower_count: 0,
  likes_count: 0,
  works_count: 0,
  enabled: true
})

const loading = ref(false)
const saving = ref(false)
const togglingId = ref('')
const keywordInput = ref('')
const keyword = ref('')
const accounts = ref([])
const modalOpen = ref(false)
const dataModalOpen = ref(false)
const editingId = ref('')
const viewingAccount = ref(null)
const form = reactive(emptyForm())

const modalTitle = computed(() => (editingId.value ? '编辑账号' : '新增账号'))

const accountTypeLabel = (type) =>
  ACCOUNT_TYPE_OPTIONS.find((item) => item.value === type)?.label || type || '-'

const loadAccounts = async () => {
  loading.value = true
  try {
    const response = await accountApi.listAccounts({ keyword: keyword.value })
    accounts.value = response.accounts || []
  } catch (error) {
    message.error(error.message || '加载账号失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  void loadAccounts()
}

const resetForm = (account) => {
  const next = account
    ? {
        name: account.name,
        account_id: account.account_id,
        account_type: account.account_type,
        following_count: account.following_count,
        follower_count: account.follower_count,
        likes_count: account.likes_count,
        works_count: account.works_count,
        enabled: account.enabled
      }
    : emptyForm()
  Object.assign(form, next)
}

const openCreate = () => {
  editingId.value = ''
  resetForm()
  modalOpen.value = true
}

const openEdit = (account) => {
  editingId.value = account.id
  resetForm(account)
  modalOpen.value = true
}

const openData = (account) => {
  viewingAccount.value = account
  dataModalOpen.value = true
}

const derivedName = computed(() => form.account_id.trim())

const saveAccount = async () => {
  const accountId = form.account_id.trim()
  if (!accountId) {
    message.warning('请输入ID')
    return
  }
  if (!form.account_type) {
    message.warning('请选择账号类型')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: derivedName.value,
      account_id: accountId,
      account_type: form.account_type,
      enabled: form.enabled
    }
    if (editingId.value) {
      await accountApi.updateAccount(editingId.value, payload)
      message.success('账号已更新')
    } else {
      await accountApi.createAccount(payload)
      message.success('账号已创建')
    }
    modalOpen.value = false
    await loadAccounts()
  } catch (error) {
    message.error(error.message || '保存账号失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (account, enabled) => {
  togglingId.value = account.id
  try {
    await accountApi.updateAccount(account.id, { enabled })
    account.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeAccount = (account) => {
  Modal.confirm({
    title: `删除账号「${account.name}」`,
    content: '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await accountApi.deleteAccount(account.id)
      message.success('账号已删除')
      await loadAccounts()
    }
  })
}

onMounted(loadAccounts)
</script>

<template>
  <div class="account-manage-view">
    <PageHeader title="账号管理" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>{{ accounts.length }} 个账号</span>
          <span>{{ accounts.filter((item) => item.enabled).length }} 个启用</span>
        </div>
      </template>
    </PageHeader>

    <div class="account-manage-content">
      <div class="account-toolbar">
        <a-input
          v-model:value="keywordInput"
          class="search-input"
          placeholder="输入搜索关键词"
          allow-clear
          @pressEnter="handleSearch"
        >
          <template #prefix><Search :size="14" /></template>
        </a-input>
        <a-button type="primary" @click="handleSearch">查询</a-button>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreate">
          <Plus :size="14" />
          新增账号
        </a-button>
      </div>

      <a-table
        class="account-table"
        :data-source="accounts"
        :loading="loading"
        :pagination="false"
        row-key="id"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ index + 1 }}</template>
        </a-table-column>
        <a-table-column title="账号名称" data-index="name" key="name" />
        <a-table-column title="ID" data-index="account_id" key="account_id" />
        <a-table-column title="账号类型" key="account_type" :width="110">
          <template #default="{ record }">{{ accountTypeLabel(record.account_type) }}</template>
        </a-table-column>
        <a-table-column title="关注数" data-index="following_count" key="following_count" :width="90" />
        <a-table-column title="粉丝数/个" data-index="follower_count" key="follower_count" :width="110" />
        <a-table-column title="获赞与收藏" data-index="likes_count" key="likes_count" :width="110" />
        <a-table-column title="作品数" data-index="works_count" key="works_count" :width="90" />
        <a-table-column title="数据分析" key="analysis" :width="120">
          <template #default="{ record }">
            <a-button type="link" class="data-btn lucide-icon-btn" @click="openData(record)">
              <Zap :size="13" />
              查看数据
            </a-button>
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
        <a-table-column title="操作" key="actions" :width="140">
          <template #default="{ record }">
            <div class="row-actions">
              <a-button type="link" danger @click="removeAccount(record)">删除</a-button>
              <a-button type="link" @click="openEdit(record)">编辑</a-button>
            </div>
          </template>
        </a-table-column>
      </a-table>
    </div>

    <a-modal
      v-model:open="modalOpen"
      :title="modalTitle"
      :confirm-loading="saving"
      :mask-closable="false"
      :footer="null"
      width="480px"
      class="account-form-modal"
    >
      <a-form
        class="account-form"
        :model="form"
        :label-col="{ style: { width: '92px' } }"
        :wrapper-col="{ style: { flex: 1 } }"
      >
        <a-form-item label="ID" required>
          <a-input v-model:value="form.account_id" placeholder="请输入ID" allow-clear />
        </a-form-item>
        <a-form-item label="账号名称" required>
          <a-input
            :value="derivedName"
            placeholder="根据ID默认显示账号名称"
            disabled
            class="derived-name-input"
          />
        </a-form-item>
        <a-form-item label="账号类型" required>
          <a-select v-model:value="form.account_type" placeholder="请选择账号类型">
            <a-select-option
              v-for="option in ACCOUNT_TYPE_OPTIONS"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="状态" required>
          <a-radio-group v-model:value="form.enabled">
            <a-radio :value="true">启用</a-radio>
            <a-radio :value="false">禁用</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
      <div class="account-form-footer">
        <a-button type="primary" @click="modalOpen = false">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveAccount">确定</a-button>
      </div>
    </a-modal>

    <a-modal v-model:open="dataModalOpen" title="账号数据" :footer="null" width="420px">
      <dl v-if="viewingAccount" class="account-data">
        <div>
          <dt>账号名称</dt>
          <dd>{{ viewingAccount.name }}</dd>
        </div>
        <div>
          <dt>关注数</dt>
          <dd>{{ viewingAccount.following_count }}</dd>
        </div>
        <div>
          <dt>粉丝数</dt>
          <dd>{{ viewingAccount.follower_count }}</dd>
        </div>
        <div>
          <dt>获赞与收藏</dt>
          <dd>{{ viewingAccount.likes_count }}</dd>
        </div>
        <div>
          <dt>作品数</dt>
          <dd>{{ viewingAccount.works_count }}</dd>
        </div>
      </dl>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.account-manage-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.summary-strip {
  display: flex;
  gap: 8px;

  span {
    padding: 6px 10px;
    border: 1px solid var(--gray-100);
    border-radius: 7px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
  }
}

.account-manage-content {
  padding: 16px var(--page-padding) 32px;
}

.account-toolbar {
  display: flex;
  align-items: center;
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

.account-table {
  :deep(.ant-table) {
    background: var(--gray-0);
  }

  :deep(.ant-table-thead > tr > th) {
    background: var(--gray-10);
    color: var(--gray-700);
    font-weight: 600;
  }
}

.data-btn {
  padding: 0;
  height: auto;
  color: var(--main-color);
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
  gap: 4px;

  :deep(.ant-btn) {
    padding: 0 4px;
    height: auto;
  }
}

.account-form {
  padding: 8px 12px 0;

  :deep(.ant-form-item) {
    margin-bottom: 18px;
  }

  :deep(.ant-form-item-label > label) {
    color: var(--color-text);
  }

  :deep(.ant-select) {
    width: 100%;
  }

  .derived-name-input {
    :deep(.ant-input[disabled]) {
      color: var(--gray-600);
      background: var(--gray-50);
      cursor: default;
    }
  }
}

.account-form-footer {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 8px 0 4px;
}

.account-data {
  display: grid;
  gap: 12px;
  margin: 0;

  div {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--gray-100);
  }

  dt {
    color: var(--color-text-secondary);
  }

  dd {
    margin: 0;
    color: var(--color-text);
    font-weight: 600;
  }
}
</style>
