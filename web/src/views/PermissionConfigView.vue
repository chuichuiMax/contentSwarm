<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { Plus, Search } from 'lucide-vue-next'

import { roleApi } from '@/apis/role_api'
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
const roles = ref([])
const modalOpen = ref(false)
const membersOpen = ref(false)
const membersLoading = ref(false)
const members = ref([])
const viewingRole = ref(null)
const memberKeywordInput = ref('')
const memberKeyword = ref('')
const form = reactive(emptyForm())
const router = useRouter()

const formatCreatedAt = (value) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (num) => String(num).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const loadRoles = async () => {
  loading.value = true
  try {
    const response = await roleApi.listRoles({ keyword: keyword.value })
    roles.value = response.roles || []
  } catch (error) {
    message.error(error.message || '加载角色失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  void loadRoles()
}

const openCreate = () => {
  Object.assign(form, emptyForm())
  modalOpen.value = true
}

const closeCreate = () => {
  modalOpen.value = false
  Object.assign(form, emptyForm())
}

const saveRole = async () => {
  const name = form.name.trim()
  if (!name) {
    message.warning('请输入角色名称')
    return
  }
  saving.value = true
  try {
    await roleApi.createRole({ name, enabled: form.enabled })
    message.success('角色已创建')
    closeCreate()
    await loadRoles()
  } catch (error) {
    message.error(error.message || '保存角色失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (role, enabled) => {
  if (role.is_system && role.role_code === 'superadmin') {
    message.warning('超级管理员角色不能停用')
    return
  }
  togglingId.value = role.id
  try {
    await roleApi.updateRole(role.id, { enabled })
    role.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const loadMembers = async () => {
  if (!viewingRole.value) return
  membersLoading.value = true
  try {
    const response = await roleApi.listRoleEmployees(viewingRole.value.id, {
      keyword: memberKeyword.value
    })
    members.value = response.employees || []
  } catch (error) {
    message.error(error.message || '加载关联人员失败')
    members.value = []
  } finally {
    membersLoading.value = false
  }
}

const openAuthorize = (role) => {
  router.push({ name: 'RoleAuthorizeComp', params: { roleId: role.id } })
}

const openMembers = async (role) => {
  viewingRole.value = role
  memberKeywordInput.value = ''
  memberKeyword.value = ''
  membersOpen.value = true
  await loadMembers()
}

const searchMembers = () => {
  memberKeyword.value = memberKeywordInput.value.trim()
  void loadMembers()
}

const closeMembers = () => {
  membersOpen.value = false
  viewingRole.value = null
  memberKeywordInput.value = ''
  memberKeyword.value = ''
  members.value = []
}

const removeRole = (role) => {
  Modal.confirm({
    title: `删除角色「${role.name}」`,
    content: '已关联员工或系统用户的角色无法删除。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await roleApi.deleteRole(role.id)
        message.success('角色已删除')
        await loadRoles()
      } catch (error) {
        message.error(error.message || '删除角色失败')
        return Promise.reject(error)
      }
    }
  })
}

onMounted(loadRoles)
</script>

<template>
  <div class="permission-config-view">
    <PageHeader title="权限配置" :show-border="true" />

    <div class="permission-config-content">
      <div class="permission-toolbar">
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
        class="permission-table"
        :data-source="roles"
        :loading="loading"
        :pagination="false"
        row-key="id"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ index + 1 }}</template>
        </a-table-column>
        <a-table-column title="角色编码" data-index="role_code" key="role_code" />
        <a-table-column title="角色名称" data-index="name" key="name" />
        <a-table-column title="关联人员" key="member_count" :width="120">
          <template #default="{ record }">
            <a-button type="link" class="member-link" @click="openMembers(record)">
              {{ record.member_count }}
            </a-button>
          </template>
        </a-table-column>
        <a-table-column title="类型" data-index="role_type" key="role_type" :width="100" />
        <a-table-column title="状态" key="enabled" :width="140">
          <template #default="{ record }">
            <div class="status-cell">
              <a-switch
                size="small"
                :checked="record.enabled"
                :disabled="record.is_system && record.role_code === 'superadmin'"
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
        <a-table-column title="操作" key="actions" :width="140">
          <template #default="{ record }">
            <div class="row-actions">
              <a-button type="link" @click="openAuthorize(record)">授权</a-button>
              <a-button v-if="!record.is_system" type="link" @click="removeRole(record)">删除</a-button>
            </div>
          </template>
        </a-table-column>
      </a-table>
    </div>

    <a-modal
      v-model:open="modalOpen"
      title="新增角色"
      :mask-closable="false"
      :footer="null"
      width="480px"
      class="role-form-modal"
      @cancel="closeCreate"
    >
      <a-form
        class="role-form"
        :model="form"
        :label-col="{ style: { width: '92px' } }"
        :wrapper-col="{ style: { flex: 1 } }"
      >
        <a-form-item label="角色名称" required>
          <a-input v-model:value="form.name" placeholder="请输入角色名称" allow-clear />
        </a-form-item>
        <a-form-item label="状态" required>
          <a-radio-group v-model:value="form.enabled">
            <a-radio :value="true">启用</a-radio>
            <a-radio :value="false">禁用</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
      <div class="role-form-footer">
        <a-button type="primary" @click="closeCreate">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveRole">确定</a-button>
      </div>
    </a-modal>

    <a-modal
      v-model:open="membersOpen"
      title="关联人员"
      :mask-closable="false"
      :footer="null"
      width="640px"
      class="members-modal"
      @cancel="closeMembers"
    >
      <div class="members-search">
        <a-input
          v-model:value="memberKeywordInput"
          placeholder="请输入姓名或编码"
          allow-clear
          @pressEnter="searchMembers"
          @clear="searchMembers"
        >
          <template #suffix>
            <button type="button" class="members-search-btn" aria-label="查询" @click="searchMembers">
              <Search :size="16" />
            </button>
          </template>
        </a-input>
      </div>
      <a-table
        class="members-table"
        :data-source="members"
        :loading="membersLoading"
        :pagination="false"
        row-key="id"
      >
        <a-table-column title="序号" key="index" :width="72" align="center">
          <template #default="{ index }">{{ index + 1 }}</template>
        </a-table-column>
        <a-table-column title="姓名" data-index="name" key="name" align="center" />
        <a-table-column title="员工编码" data-index="employee_code" key="employee_code" align="center" />
        <a-table-column title="角色" data-index="role" key="role" align="center" />
        <a-table-column title="状态" key="enabled" :width="88" align="center">
          <template #default="{ record }">{{ record.enabled ? '启用' : '禁用' }}</template>
        </a-table-column>
      </a-table>
      <div class="members-footer">
        <a-button type="primary" @click="closeMembers">取消</a-button>
        <a-button type="primary" @click="closeMembers">确定</a-button>
      </div>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.permission-config-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.permission-config-content {
  padding: 16px var(--page-padding) 32px;
}

.permission-toolbar {
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

.permission-table {
  :deep(.ant-table) {
    background: var(--gray-0);
  }

  :deep(.ant-table-thead > tr > th) {
    background: var(--gray-10);
    color: var(--gray-700);
    font-weight: 600;
  }
}

.member-link {
  padding: 0;
  height: auto;
  color: var(--main-color);
  text-decoration: underline;
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

.role-form {
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
    border-radius: 8px;
  }
}

.role-form-footer {
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

.members-search {
  margin-bottom: 16px;

  :deep(.ant-input-affix-wrapper) {
    border-radius: 8px;
  }
}

.members-search-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;

  &:hover,
  &:focus-visible {
    color: var(--main-color);
    outline: none;
  }
}

.members-table {
  :deep(.ant-table) {
    background: var(--gray-0);
  }

  :deep(.ant-table-thead > tr > th) {
    background: var(--gray-10);
    color: var(--gray-700);
    font-weight: 600;
    text-align: center;
  }
}

.members-footer {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 20px 0 4px;

  :deep(.ant-btn) {
    min-width: 88px;
    height: 36px;
    border-radius: 8px;
  }
}
</style>
