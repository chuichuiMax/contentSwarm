<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, Search } from 'lucide-vue-next'

import { employeeApi } from '@/apis/employee_api'
import { roleApi } from '@/apis/role_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const GENDER_OPTIONS = [
  { value: 'male', label: '男' },
  { value: 'female', label: '女' }
]

const LOGIN_PORT_OPTIONS = [
  { value: 'pc', label: 'PC' },
  { value: 'app', label: 'APP' }
]

const emptyForm = () => ({
  employee_code: '',
  name: '',
  login_account: '',
  current_branch: '',
  current_department: '',
  gender: 'male',
  age: null,
  login_port: ['pc', 'app'],
  role: '',
  enabled: true
})

const loading = ref(false)
const saving = ref(false)
const togglingId = ref('')
const keywordInput = ref('')
const keyword = ref('')
const roleFilter = ref()
const page = ref(1)
const pageSize = ref(20)
const employees = ref([])
const roleOptions = ref([])
const modalOpen = ref(false)
const editingId = ref('')
const form = reactive(emptyForm())

const modalTitle = computed(() => (editingId.value ? '编辑员工' : '新增员工'))
const roleSelectOptions = computed(() => {
  const names = [...roleOptions.value]
  if (form.role && !names.includes(form.role)) names.unshift(form.role)
  return names
})
const roleFilterOptions = computed(() => {
  const names = new Set(roleOptions.value)
  for (const item of employees.value) {
    if (item.role) names.add(item.role)
  }
  return [...names]
})
const displayedEmployees = computed(() => {
  if (!roleFilter.value) return employees.value
  return employees.value.filter((item) => item.role === roleFilter.value)
})
const tablePagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条`,
  pageSizeOptions: ['10', '20', '50']
}))

const optionLabel = (options, value) =>
  options.find((item) => item.value === value)?.label || value || '-'

const isSystemAccount = (employee) => employee?.source === 'user'

const loginPortLabel = (ports) => {
  const selected = Array.isArray(ports) ? ports : []
  return LOGIN_PORT_OPTIONS.filter((item) => selected.includes(item.value))
    .map((item) => item.label)
    .join('&') || '-'
}

const normalizeLoginPorts = (ports) => {
  if (Array.isArray(ports)) return [...ports]
  if (ports === 'pc_app') return ['pc', 'app']
  if (ports === 'app' || ports === 'pc') return [ports]
  return ['pc', 'app']
}

const loadRoles = async ({ force = false } = {}) => {
  if (!force && roleOptions.value.length) return
  try {
    const response = await roleApi.listRoles({ enabled: true, include_member_counts: false })
    roleOptions.value = (response.roles || []).map((item) => item.name)
  } catch (error) {
    message.error(error.message || '加载角色失败')
  }
}

const loadEmployees = async () => {
  loading.value = true
  try {
    const response = await employeeApi.listEmployees({ keyword: keyword.value })
    employees.value = response.employees || []
  } catch (error) {
    message.error(error.message || '加载员工失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  keyword.value = keywordInput.value.trim()
  page.value = 1
  void loadEmployees()
}

const handleRoleFilterChange = () => {
  page.value = 1
}

const handleTableChange = (pagination) => {
  page.value = pagination.current
  pageSize.value = pagination.pageSize
}

const resetForm = (employee) => {
  const next = employee
    ? {
        ...emptyForm(),
        ...employee,
        age: employee.age ?? null,
        login_port: normalizeLoginPorts(employee.login_port)
      }
    : emptyForm()
  Object.assign(form, next)
}

const openCreate = async () => {
  editingId.value = ''
  await loadRoles()
  resetForm()
  form.role = roleOptions.value[0] || ''
  modalOpen.value = true
}

const openEdit = async (employee) => {
  if (isSystemAccount(employee)) return
  editingId.value = employee.id
  await loadRoles()
  resetForm(employee)
  modalOpen.value = true
}

const saveEmployee = async () => {
  const payload = {
    employee_code: form.employee_code.trim(),
    name: form.name.trim(),
    login_account: form.login_account.trim(),
    current_branch: form.current_branch.trim(),
    current_department: form.current_department.trim(),
    gender: form.gender,
    age: form.age == null || form.age === '' ? null : Number(form.age),
    login_port: form.login_port,
    role: form.role,
    enabled: form.enabled
  }
  if (!payload.employee_code) {
    message.warning('请输入员工编码')
    return
  }
  if (!payload.name) {
    message.warning('请输入姓名')
    return
  }
  if (!payload.login_account) {
    message.warning('请输入登录账号')
    return
  }
  if (payload.age != null && (!Number.isInteger(payload.age) || payload.age < 1 || payload.age > 120)) {
    message.warning('年龄请填写 1–120 的整数')
    return
  }
  if (!payload.login_port.length) {
    message.warning('请选择登录端口')
    return
  }
  if (!payload.role) {
    message.warning('请选择角色')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await employeeApi.updateEmployee(editingId.value, payload)
      message.success('员工已更新')
    } else {
      await employeeApi.createEmployee(payload)
      message.success('员工已创建')
    }
    modalOpen.value = false
    await loadEmployees()
  } catch (error) {
    message.error(error.message || '保存员工失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (employee, enabled) => {
  if (isSystemAccount(employee)) return
  togglingId.value = employee.id
  try {
    await employeeApi.updateEmployee(employee.id, { enabled })
    employee.enabled = enabled
  } catch (error) {
    message.error(error.message || '更新状态失败')
  } finally {
    togglingId.value = ''
  }
}

const removeEmployee = (employee) => {
  if (isSystemAccount(employee)) return
  Modal.confirm({
    title: `删除员工「${employee.name}」`,
    content: '删除后不可恢复，对应平台登录账号将同时注销。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await employeeApi.deleteEmployee(employee.id)
      message.success('员工已删除')
      await loadEmployees()
    }
  })
}

watch([displayedEmployees, pageSize], () => {
  const maxPage = Math.max(1, Math.ceil(displayedEmployees.value.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([loadRoles({ force: true }), (async () => {
      const response = await employeeApi.listEmployees({ keyword: keyword.value })
      employees.value = response.employees || []
    })()])
  } catch (error) {
    message.error(error.message || '加载员工失败')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="employee-manage-view">
    <PageHeader title="员工管理" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>{{ displayedEmployees.length }} 名员工</span>
          <span>{{ displayedEmployees.filter((item) => item.enabled).length }} 名启用</span>
        </div>
      </template>
    </PageHeader>

    <div class="employee-manage-content">
      <div class="employee-toolbar">
        <div class="employee-toolbar-left">
          <a-input
            v-model:value="keywordInput"
            class="search-input"
            placeholder="员工编码、姓名、登录账号、分部、部门"
            allow-clear
            @pressEnter="handleSearch"
            @clear="handleSearch"
          >
            <template #prefix><Search :size="14" /></template>
          </a-input>
          <a-select
            v-model:value="roleFilter"
            class="role-filter"
            placeholder="全部角色"
            allow-clear
            @change="handleRoleFilterChange"
          >
            <a-select-option v-for="role in roleFilterOptions" :key="role" :value="role">
              {{ role }}
            </a-select-option>
          </a-select>
          <a-button type="primary" @click="handleSearch">查询</a-button>
        </div>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreate">
          <Plus :size="14" />
          新增员工
        </a-button>
      </div>

      <a-table
        class="employee-table"
        :data-source="displayedEmployees"
        :loading="loading"
        :pagination="tablePagination"
        row-key="id"
        @change="handleTableChange"
      >
        <a-table-column title="序号" key="index" :width="72">
          <template #default="{ index }">{{ (page - 1) * pageSize + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="员工编码" data-index="employee_code" key="employee_code" />
        <a-table-column title="姓名" data-index="name" key="name" />
        <a-table-column title="当前分部" data-index="current_branch" key="current_branch" />
        <a-table-column title="当前部门" data-index="current_department" key="current_department" />
        <a-table-column title="登录账号" data-index="login_account" key="login_account" />
        <a-table-column title="性别" key="gender" :width="80">
          <template #default="{ record }">{{ optionLabel(GENDER_OPTIONS, record.gender) }}</template>
        </a-table-column>
        <a-table-column title="年龄" key="age" :width="80">
          <template #default="{ record }">{{ record.age ?? '-' }}</template>
        </a-table-column>
        <a-table-column title="登录端口" key="login_port" :width="110">
          <template #default="{ record }">{{ loginPortLabel(record.login_port) }}</template>
        </a-table-column>
        <a-table-column title="角色" data-index="role" key="role" />
        <a-table-column title="状态" key="enabled" :width="140">
          <template #default="{ record }">
            <div class="status-cell">
              <a-switch
                size="small"
                :checked="record.enabled"
                :disabled="isSystemAccount(record)"
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
              <template v-if="isSystemAccount(record)">
                <span class="system-account-hint">系统账号</span>
              </template>
              <template v-else>
                <a-button type="link" danger @click="removeEmployee(record)">删除</a-button>
                <a-button type="link" @click="openEdit(record)">编辑</a-button>
              </template>
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
      width="520px"
    >
      <a-form
        class="employee-form"
        :model="form"
        :label-col="{ style: { width: '92px' } }"
        :wrapper-col="{ style: { flex: 1 } }"
      >
        <a-form-item label="员工编码" required>
          <a-input v-model:value="form.employee_code" placeholder="请输入员工编码" allow-clear />
        </a-form-item>
        <a-form-item label="姓名" required>
          <a-input v-model:value="form.name" placeholder="请输入姓名" allow-clear />
        </a-form-item>
        <a-form-item label="当前分部">
          <a-input v-model:value="form.current_branch" placeholder="请输入当前分部" allow-clear />
        </a-form-item>
        <a-form-item label="当前部门">
          <a-input v-model:value="form.current_department" placeholder="请输入当前部门" allow-clear />
        </a-form-item>
        <a-form-item label="登录账号" required>
          <a-input v-model:value="form.login_account" placeholder="请输入手机号码" allow-clear />
        </a-form-item>
        <a-form-item v-if="!editingId" label="初始密码">
          <a-input value="123456" disabled />
        </a-form-item>
        <a-form-item label="性别" required>
          <a-radio-group v-model:value="form.gender">
            <a-radio v-for="option in GENDER_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="年龄">
          <a-input-number
            v-model:value="form.age"
            :min="1"
            :max="120"
            :precision="0"
            placeholder="请输入年龄"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item label="登录端口" required>
          <a-checkbox-group v-model:value="form.login_port">
            <a-checkbox v-for="option in LOGIN_PORT_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </a-checkbox>
          </a-checkbox-group>
        </a-form-item>
        <a-form-item label="角色" required>
          <a-select v-model:value="form.role" placeholder="请选择角色">
            <a-select-option v-for="role in roleSelectOptions" :key="role" :value="role">
              {{ role }}
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
      <div class="employee-form-footer">
        <a-button type="primary" @click="modalOpen = false">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveEmployee">确定</a-button>
      </div>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.employee-manage-view {
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

.employee-manage-content {
  padding: 16px var(--page-padding) 32px;
}

.employee-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 16px;

  .employee-toolbar-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .search-input {
    width: 280px;
  }

  .role-filter {
    width: 160px;
  }

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.employee-table {
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
  gap: 4px;

  :deep(.ant-btn) {
    padding: 0 4px;
    height: auto;
  }
}

.system-account-hint {
  color: var(--gray-500);
  font-size: 12px;
}

.employee-form {
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
}

.employee-form-footer {
  display: flex;
  justify-content: center;
  gap: 16px;
  padding: 8px 0 4px;
}
</style>
