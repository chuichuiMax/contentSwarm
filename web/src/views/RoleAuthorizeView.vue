<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { Search } from 'lucide-vue-next'

import { roleApi } from '@/apis/role_api'
import PageHeader from '@/components/shared/PageHeader.vue'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const keywordInput = ref('')
const role = ref(null)
const catalog = ref([])
const grants = ref([])

const grantSet = computed(() => new Set(grants.value))

const roleLabel = computed(() => {
  if (!role.value) return ''
  const name = role.value.name.endsWith('角色') ? role.value.name : `${role.value.name}角色`
  return `${role.value.role_code}/${name}`
})

const permissionRows = computed(() => {
  const kw = keywordInput.value.trim().toLowerCase()
  const matched = []
  for (const module of catalog.value) {
    for (const item of module.lists || []) {
      const haystack = [module.module, item.list, ...(item.actions || []).map((action) => action.label)]
        .join(' ')
        .toLowerCase()
      if (kw && !haystack.includes(kw)) continue
      matched.push({
        key: `${module.module_key}:${item.list_key}`,
        module: module.module,
        list: item.list,
        actions: item.actions || []
      })
    }
  }
  const counts = {}
  for (const row of matched) counts[row.module] = (counts[row.module] || 0) + 1
  const seen = {}
  return matched.map((row) => {
    const first = !seen[row.module]
    seen[row.module] = true
    return { ...row, moduleRowSpan: first ? counts[row.module] : 0 }
  })
})

const moduleCell = (record) => ({ rowSpan: record.moduleRowSpan })

const isGranted = (key) => grantSet.value.has(key)

const isRowAllChecked = (row) =>
  row.actions.length > 0 && row.actions.every((action) => grantSet.value.has(action.key))

const isRowAllIndeterminate = (row) => {
  const count = row.actions.filter((action) => grantSet.value.has(action.key)).length
  return count > 0 && count < row.actions.length
}

const persist = async (nextGrants) => {
  if (!role.value) return
  saving.value = true
  try {
    const response = await roleApi.updateRolePermissions(role.value.id, { grants: nextGrants })
    grants.value = response.grants || []
  } catch (error) {
    message.error(error.message || '保存权限失败')
    throw error
  } finally {
    saving.value = false
  }
}

const toggleAction = async (key, checked) => {
  const next = new Set(grants.value)
  if (checked) next.add(key)
  else next.delete(key)
  await persist([...next])
}

const toggleRowAll = async (row, checked) => {
  const next = new Set(grants.value)
  for (const action of row.actions) {
    if (checked) next.add(action.key)
    else next.delete(action.key)
  }
  await persist([...next])
}

const loadPermissions = async () => {
  const roleId = route.params.roleId
  loading.value = true
  try {
    const response = await roleApi.getRolePermissions(roleId)
    role.value = response.role
    catalog.value = response.catalog || []
    grants.value = response.grants || []
  } catch (error) {
    message.error(error.message || '加载授权失败')
    router.push({ name: 'PermissionConfigComp' })
  } finally {
    loading.value = false
  }
}

onMounted(loadPermissions)
</script>

<template>
  <div class="role-authorize-view">
    <PageHeader title="授权" :show-border="true" />

    <div class="role-authorize-content">
      <div class="authorize-toolbar">
        <a-input
          v-model:value="keywordInput"
          class="search-input"
          placeholder="输入搜索关键词"
          allow-clear
        >
          <template #prefix><Search :size="14" /></template>
        </a-input>
        <span class="role-label">{{ roleLabel }}</span>
      </div>

      <a-table
        class="authorize-table"
        :data-source="permissionRows"
        :loading="loading"
        :pagination="false"
        row-key="key"
      >
        <a-table-column title="模块" :width="160" :custom-cell="moduleCell">
          <template #default="{ record }">{{ record.module }}</template>
        </a-table-column>
        <a-table-column title="列表" data-index="list" key="list" :width="160" />
        <a-table-column title="权限" key="actions">
          <template #default="{ record }">
            <div class="permission-actions">
              <a-checkbox
                :checked="isRowAllChecked(record)"
                :indeterminate="isRowAllIndeterminate(record)"
                :disabled="saving || !record.actions.length"
                @change="(event) => toggleRowAll(record, event.target.checked)"
              >
                权限全选
              </a-checkbox>
              <a-checkbox
                v-for="action in record.actions"
                :key="action.key"
                :checked="isGranted(action.key)"
                :disabled="saving"
                @change="(event) => toggleAction(action.key, event.target.checked)"
              >
                {{ action.label }}
              </a-checkbox>
            </div>
          </template>
        </a-table-column>
      </a-table>
    </div>
  </div>
</template>

<style scoped lang="less">
.role-authorize-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.role-authorize-content {
  padding: 16px var(--page-padding) 32px;
}

.authorize-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.search-input {
  width: 280px;
}

.role-label {
  color: var(--gray-700);
  font-size: 14px;
  white-space: nowrap;
}

.authorize-table {
  :deep(.ant-table) {
    background: var(--gray-0);
  }

  :deep(.ant-table-thead > tr > th) {
    background: var(--gray-10);
    color: var(--gray-700);
    font-weight: 600;
  }

  :deep(.ant-table-tbody > tr > td) {
    vertical-align: middle;
  }
}

.permission-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 24px;

  :deep(.ant-checkbox-wrapper) {
    margin-inline-start: 0;
    align-items: center;
  }
}
</style>
