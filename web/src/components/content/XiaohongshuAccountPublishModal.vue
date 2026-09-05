<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { CheckCircle2, CircleAlert, LoaderCircle, Search, Send } from 'lucide-vue-next'
import { accountApi } from '@/apis/account_api'
import { contentApi } from '@/apis/content_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  artifact: { type: Object, default: null }
})
const emit = defineEmits(['update:open'])
const PAGE_SIZE = 5

const loading = ref(false)
const submitting = ref(false)
const keyword = ref('')
const currentPage = ref(1)
const managedAccounts = ref([])
const distributionAccounts = ref([])
const selectedAccountId = ref('')
const currentJob = ref(null)
let pollTimer = null

const jobStatusLabels = {
  queued: '等待发布',
  running: '正在发布',
  completed: '发布完成',
  partial_failed: '部分账号发布失败',
  uncertain: '发布结果待核对',
  failed: '发布失败'
}

const rows = computed(() => {
  const bindings = new Map(
    distributionAccounts.value
      .filter((item) => item.platform_account_id)
      .map((item) => [item.platform_account_id, item])
  )
  const matchedBindingIds = new Set()
  const configuredRows = managedAccounts.value.map((account) => {
    const binding = bindings.get(account.account_id)
    if (binding) matchedBindingIds.add(binding.id)
    const ready = Boolean(
      account.enabled && binding?.enabled && binding.login_status === 'logged_in'
    )
    return {
      key: `configured-${account.id}`,
      distributionAccountId: binding?.id || '',
      name: binding?.platform_nickname || account.name || '-',
      remarkName: binding?.display_name || '-',
      ready,
      status: !account.enabled
        ? '禁用'
        : !binding
          ? '未绑定'
          : !binding.enabled
            ? '禁用'
            : binding.login_status === 'logged_in'
              ? '启用'
              : '未登录'
    }
  })
  const boundOnlyRows = distributionAccounts.value
    .filter((account) => !matchedBindingIds.has(account.id))
    .map((account) => ({
      key: `bound-${account.id}`,
      distributionAccountId: account.id,
      name: account.platform_nickname || '-',
      remarkName: account.display_name || '-',
      ready: account.enabled && account.login_status === 'logged_in',
      status: !account.enabled
        ? '禁用'
        : account.login_status === 'logged_in'
          ? '启用'
          : '未登录'
    }))
  return [...configuredRows, ...boundOnlyRows]
})

const filteredRows = computed(() => {
  const value = keyword.value.trim().toLowerCase()
  if (!value) return rows.value
  return rows.value.filter((row) =>
    [row.name, row.remarkName].some((item) => String(item || '').toLowerCase().includes(value))
  )
})
const pagination = computed(() => ({
  current: currentPage.value,
  pageSize: PAGE_SIZE,
  total: filteredRows.value.length,
  showSizeChanger: false,
  hideOnSinglePage: true,
  position: ['bottomRight']
}))
const isRunning = computed(() => ['queued', 'running'].includes(currentJob.value?.status))
const canPublish = computed(
  () => Boolean(selectedAccountId.value && props.artifact?.id && !submitting.value && !currentJob.value)
)

const createClientRequestId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `xhs-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

const stopPolling = () => {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = null
}

const pollJob = async () => {
  if (!props.open || !currentJob.value?.id) return
  try {
    currentJob.value = (await contentApi.getDistribution(currentJob.value.id)).job
    if (isRunning.value) {
      pollTimer = window.setTimeout(pollJob, 1800)
      return
    }
    submitting.value = false
    if (currentJob.value.status === 'completed') message.success('内容已发布到所选小红书账号')
    else message.warning(jobStatusLabels[currentJob.value.status] || '发布任务已结束，请核对结果')
  } catch (error) {
    submitting.value = false
    message.error(error.message || '获取发布进度失败')
  }
}

const initialize = async () => {
  stopPolling()
  loading.value = true
  submitting.value = false
  keyword.value = ''
  currentPage.value = 1
  selectedAccountId.value = ''
  currentJob.value = null
  try {
    const [accountResponse, bindingResponse] = await Promise.all([
      accountApi.listAccounts(),
      contentApi.listXiaohongshuAccounts()
    ])
    managedAccounts.value = accountResponse.accounts || []
    distributionAccounts.value = bindingResponse.items || []
    const readyRows = rows.value.filter((row) => row.ready)
    if (readyRows.length === 1) selectedAccountId.value = readyRows[0].distributionAccountId
  } catch (error) {
    message.error(error.message || '小红书账号加载失败')
  } finally {
    loading.value = false
  }
}

const publish = async () => {
  if (!canPublish.value) return
  submitting.value = true
  try {
    const response = await contentApi.createDistribution(props.artifact.id, {
      request_id: createClientRequestId(),
      account_ids: [selectedAccountId.value],
      mode: 'publish',
      confirm_publish: true
    })
    currentJob.value = response.job
    await pollJob()
  } catch (error) {
    submitting.value = false
    message.error(error.message || '创建小红书发布任务失败')
  }
}

const close = () => {
  if (isRunning.value) return
  emit('update:open', false)
}

const handleTableChange = (nextPagination) => {
  currentPage.value = nextPagination.current || 1
}

watch(keyword, () => {
  currentPage.value = 1
})

watch(
  () => props.open,
  (open) => {
    if (open) void initialize()
    else stopPolling()
  }
)
onBeforeUnmount(stopPolling)
</script>

<template>
  <a-modal
    :open="open"
    class="xhs-account-publish-modal"
    title="选择要发布的账号"
    :width="640"
    :closable="!isRunning"
    :mask-closable="!isRunning"
    destroy-on-close
    @cancel="close"
  >
    <div class="publish-modal-body">
      <div class="publish-toolbar">
        <a-input v-model:value="keyword" placeholder="输入搜索关键词" allow-clear>
          <template #suffix><Search :size="18" /></template>
        </a-input>
      </div>

      <a-table
        :data-source="filteredRows"
        :loading="loading"
        :pagination="pagination"
        row-key="key"
        size="middle"
        bordered
        @change="handleTableChange"
      >
        <a-table-column title="单选" key="selection" :width="64" align="center">
          <template #default="{ record }">
            <a-radio
              :checked="selectedAccountId === record.distributionAccountId"
              :disabled="!record.ready || isRunning"
              @change="selectedAccountId = record.distributionAccountId"
            />
          </template>
        </a-table-column>
        <a-table-column title="序号" key="index" :width="64" align="center">
          <template #default="{ index }">{{ (currentPage - 1) * PAGE_SIZE + index + 1 }}</template>
        </a-table-column>
        <a-table-column title="账号名称" data-index="name" key="name" :width="170" />
        <a-table-column title="备注名" data-index="remarkName" key="remarkName" :width="210" />
        <a-table-column title="状态" key="status" :width="90" align="center">
          <template #default="{ record }">
            <span class="account-status" :class="{ ready: record.ready }">{{ record.status }}</span>
          </template>
        </a-table-column>
        <template #emptyText>
          <div class="publish-empty">
            <span>暂无已配置的小红书账号</span>
          </div>
        </template>
      </a-table>

      <div v-if="currentJob" class="publish-progress" :class="currentJob.status">
        <LoaderCircle v-if="isRunning" class="spin" :size="18" />
        <CheckCircle2 v-else-if="currentJob.status === 'completed'" :size="18" />
        <CircleAlert v-else :size="18" />
        <div>
          <strong>{{ jobStatusLabels[currentJob.status] || currentJob.status }}</strong>
          <span v-if="currentJob.error_message">{{ currentJob.error_message }}</span>
          <span v-else>当前内容版本已锁定，发布结果会保存在分发记录中。</span>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="publish-modal-footer">
        <a-button :disabled="isRunning" @click="close">{{ currentJob ? '关闭' : '取消' }}</a-button>
        <a-button
          v-if="!currentJob"
          type="primary"
          class="lucide-icon-btn"
          :loading="submitting"
          :disabled="!canPublish"
          @click="publish"
        >
          <Send :size="15" />确定并同步应用
        </a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped lang="less">
.publish-modal-body { display: grid; gap: 18px; }
.publish-toolbar { display: flex; align-items: center; }
.publish-toolbar :deep(.ant-input-affix-wrapper) { width: 300px; height: 42px; }
.xhs-account-publish-modal :deep(.ant-table) { border-radius: 6px; overflow: hidden; }
.xhs-account-publish-modal :deep(.ant-table-thead > tr > th) { background: var(--gray-50); color: var(--color-text); font-size: 12px; font-weight: 600; }
.xhs-account-publish-modal :deep(.ant-table-tbody > tr:nth-child(even) > td) { background: var(--gray-25); }
.xhs-account-publish-modal :deep(.ant-table-cell) { height: 54px; padding: 10px 12px; font-size: 12px; }
.xhs-account-publish-modal :deep(.ant-table-pagination) { margin: 14px 0 0; }
.account-status { color: var(--color-text-tertiary); }
.account-status.ready { color: var(--color-success-700); }
.publish-empty { min-height: 100px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--color-text-secondary); }
.publish-progress { display: flex; align-items: flex-start; gap: 9px; padding: 11px 12px; border-radius: 6px; color: var(--color-info-700); background: var(--color-info-50); }
.publish-progress.completed { color: var(--color-success-700); background: var(--color-success-50); }
.publish-progress.failed, .publish-progress.partial_failed { color: var(--color-error-700); background: var(--color-error-50); }
.publish-progress > div { display: grid; gap: 2px; }
.publish-progress span { color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.publish-modal-footer { display: flex; justify-content: center; gap: 18px; }
.publish-modal-footer :deep(.ant-btn) { min-width: 110px; display: inline-flex; align-items: center; justify-content: center; gap: 5px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 640px) {
  .publish-toolbar { align-items: stretch; flex-direction: column; }
  .publish-toolbar :deep(.ant-input-affix-wrapper), .publish-toolbar :deep(.ant-btn) { width: 100%; }
}
</style>
