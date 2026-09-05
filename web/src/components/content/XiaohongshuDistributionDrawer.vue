<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  Eye,
  Image as ImageIcon,
  LoaderCircle,
  Send,
  Settings2
} from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  artifact: { type: Object, default: null }
})
const emit = defineEmits(['update:open'])
const router = useRouter()

const loading = ref(false)
const submitting = ref(false)
const accounts = ref([])
const history = ref([])
const currentJob = ref(null)
const form = reactive({ account_ids: [], mode: 'draft', title: '', body: '', topics: [] })
let pollTimer = null

const readyAccounts = computed(() =>
  accounts.value.filter((item) => item.enabled && item.login_status === 'logged_in')
)
const titleCount = computed(() => [...form.title].length)
const bodyCount = computed(() => [...form.body].length)
const canSubmit = computed(
  () =>
    form.account_ids.length > 0 &&
    titleCount.value > 0 &&
    titleCount.value <= 20 &&
    bodyCount.value > 0 &&
    bodyCount.value <= 1000 &&
    !submitting.value
)
const isRunning = computed(() => ['queued', 'running'].includes(currentJob.value?.status))

const statusLabels = {
  queued: '等待执行',
  running: '正在分发',
  completed: '全部完成',
  partial_failed: '部分失败',
  uncertain: '结果待人工核对',
  failed: '分发失败',
  draft_saved: '已存草稿箱',
  published: '已发布'
}

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
    if (['queued', 'running'].includes(currentJob.value.status)) {
      pollTimer = window.setTimeout(pollJob, 1800)
    } else {
      submitting.value = false
      message[currentJob.value.status === 'completed' ? 'success' : 'warning'](
        currentJob.value.status === 'completed'
          ? currentJob.value.mode === 'draft'
            ? '内容已保存到所选账号的远程浏览器草稿箱'
            : '小红书分发已完成'
          : '分发完成，但存在失败账号'
      )
      await loadHistory()
    }
  } catch (error) {
    submitting.value = false
    message.error(error.message || '获取分发进度失败')
  }
}

const loadHistory = async () => {
  if (!props.artifact?.id) return
  history.value = (await contentApi.listDistributions(props.artifact.id)).items || []
}

const openEvidence = async (result) => {
  const preview = window.open('about:blank', '_blank')
  if (preview) preview.opener = null
  try {
    const response = await contentApi.getDistributionScreenshot(result.id)
    const objectUrl = URL.createObjectURL(await response.blob())
    if (preview) preview.location.href = objectUrl
    else window.open(objectUrl, '_blank', 'noopener,noreferrer')
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60000)
  } catch (error) {
    if (preview) preview.close()
    message.error(error.message || '执行截图加载失败')
  }
}

const initialize = async () => {
  stopPolling()
  currentJob.value = null
  form.account_ids = []
  form.mode = 'draft'
  form.title = props.artifact?.title || ''
  form.body = props.artifact?.body || ''
  form.topics = [...(props.artifact?.topics || [])]
  loading.value = true
  try {
    const [accountResponse] = await Promise.all([
      contentApi.listXiaohongshuAccounts(),
      loadHistory()
    ])
    accounts.value = accountResponse.items || []
    if (readyAccounts.value.length === 1) form.account_ids = [readyAccounts.value[0].id]
  } catch (error) {
    message.error(error.message || '分发配置加载失败')
  } finally {
    loading.value = false
  }
}

const execute = async () => {
  submitting.value = true
  try {
    const response = await contentApi.createDistribution(props.artifact.id, {
      request_id: createClientRequestId(),
      account_ids: form.account_ids,
      mode: form.mode,
      title: form.title,
      body: form.body,
      topics: form.topics,
      confirm_publish: form.mode === 'publish'
    })
    currentJob.value = response.job
    await pollJob()
  } catch (error) {
    submitting.value = false
    message.error(error.message || '创建分发任务失败')
  }
}

const submit = () => {
  if (!canSubmit.value) return
  if (form.mode === 'draft') {
    void execute()
    return
  }
  const names = readyAccounts.value
    .filter((item) => form.account_ids.includes(item.id))
    .map((item) => item.display_name)
    .join('、')
  Modal.confirm({
    title: '确认直接发布到小红书？',
    content: `内容将立即公开发布到：${names}。此操作不是保存草稿，请再次确认标题、正文与账号。`,
    okText: '确认直接发布',
    cancelText: '返回检查',
    okType: 'danger',
    onOk: execute
  })
}

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
  <a-drawer
    :open="open"
    width="min(620px, 100vw)"
    :closable="!isRunning"
    :mask-closable="!isRunning"
    @close="emit('update:open', false)"
  >
    <template #title><span class="drawer-title"><Send :size="19" />分发到小红书</span></template>

    <a-spin :spinning="loading">
      <div class="distribution-form">
        <section class="form-section">
          <div class="section-title"><div><strong>1. 选择账号</strong><span>仅显示当前用户已绑定的账号</span></div><a-button type="link" @click="router.push('/content/accounts')"><Settings2 :size="14" />账号管理</a-button></div>
          <a-checkbox-group v-if="readyAccounts.length" v-model:value="form.account_ids" class="account-options">
            <label v-for="account in readyAccounts" :key="account.id" class="account-option">
              <a-checkbox :value="account.id" />
              <span><strong>{{ account.display_name }}</strong><small>{{ account.platform_nickname || '已连接' }}</small></span>
            </label>
          </a-checkbox-group>
          <a-empty v-else description="暂无已连接的小红书账号">
            <a-button type="primary" @click="router.push('/content/accounts')">前往绑定账号</a-button>
          </a-empty>
        </section>

        <section class="form-section">
          <div class="section-title"><div><strong>2. 检查发布内容</strong><span>内容快照将在提交后锁定</span></div></div>
          <label class="field"><span>标题 <em :class="{ over: titleCount > 20 }">{{ titleCount }}/20</em></span><a-input v-model:value="form.title" :maxlength="40" /></label>
          <label class="field"><span>正文 <em :class="{ over: bodyCount > 1000 }">{{ bodyCount }}/1000</em></span><a-textarea v-model:value="form.body" :rows="9" :maxlength="1200" /></label>
          <label class="field"><span>话题</span><a-select v-model:value="form.topics" mode="tags" :max-tag-count="6" :max-count="10" placeholder="输入后按回车添加话题" /></label>
          <div class="cover-tip"><ImageIcon :size="18" /><span><strong>自动生成小红书封面</strong><small>使用当前标题和话题生成 3:4 图文封面，无需额外上传图片。</small></span></div>
        </section>

        <section class="form-section">
          <div class="section-title"><div><strong>3. 选择发送方式</strong><span>建议先保存草稿，在小红书中预览后再发布</span></div></div>
          <a-radio-group v-model:value="form.mode" class="mode-options">
            <label class="mode-option" :class="{ selected: form.mode === 'draft' }"><a-radio value="draft" /><span><strong>保存到草稿箱</strong><small>推荐，更适合首次使用和多账号分发</small></span></label>
            <label class="mode-option danger" :class="{ selected: form.mode === 'publish' }"><a-radio value="publish" /><span><strong>直接发布</strong><small>提交后会立即对外公开，操作前需要二次确认</small></span></label>
          </a-radio-group>
        </section>

        <section v-if="currentJob" class="progress-card">
          <div class="progress-heading">
            <LoaderCircle v-if="isRunning" class="spin" :size="19" />
            <CheckCircle2 v-else-if="currentJob.status === 'completed'" :size="19" />
            <CircleAlert v-else :size="19" />
            <strong>{{ statusLabels[currentJob.status] || currentJob.status }}</strong>
          </div>
          <div v-for="result in currentJob.results" :key="result.id" class="result-row">
            <span>{{ accounts.find((item) => item.id === result.account_id)?.display_name || result.account_id }}</span>
            <strong :class="result.status">{{ statusLabels[result.status] || result.status }}</strong>
            <a-button v-if="result.has_screenshot" type="link" size="small" @click="openEvidence(result)"><Eye :size="13" />查看执行截图</a-button>
            <small v-if="result.error_message">{{ result.error_message }}</small>
            <small v-if="result.uncertain" class="uncertain-tip">禁止自动重试，请先进入该账号后台核对是否已经发布。</small>
          </div>
        </section>

        <section v-if="history.length" class="history-section">
          <div class="section-title"><div><strong>最近分发</strong><span>保留内容版本与每个账号的执行结果</span></div></div>
          <div v-for="job in history.slice(0, 5)" :key="job.id" class="history-card">
            <div class="history-row"><Clock3 :size="15" /><span>{{ job.mode === 'draft' ? '保存草稿' : '直接发布' }} · 版本 {{ job.artifact_version }}</span><strong>{{ statusLabels[job.status] || job.status }}</strong></div>
            <div v-for="result in job.results" :key="result.id" class="history-result">
              <span>{{ accounts.find((item) => item.id === result.account_id)?.display_name || result.account_id }}</span>
              <strong :class="result.status">{{ statusLabels[result.status] || result.status }}</strong>
              <a-button v-if="result.has_screenshot" type="link" size="small" @click="openEvidence(result)"><Eye :size="13" />执行截图</a-button>
            </div>
          </div>
        </section>
      </div>
    </a-spin>

    <template #footer>
      <div class="drawer-footer">
        <span>{{ form.mode === 'draft' ? '默认安全模式：保存草稿' : '高风险操作：将立即公开发布' }}</span>
        <a-button :disabled="isRunning" @click="emit('update:open', false)">取消</a-button>
        <a-button :type="form.mode === 'draft' ? 'primary' : 'default'" :danger="form.mode === 'publish'" :loading="submitting" :disabled="!canSubmit" @click="submit">
          <Send :size="15" />{{ form.mode === 'draft' ? '一键保存到草稿箱' : '直接发布' }}
        </a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped lang="less">
.drawer-title, .section-title, .drawer-footer, .progress-heading { display: flex; align-items: center; gap: 8px; }
.distribution-form { display: flex; flex-direction: column; gap: 14px; }
.form-section { padding: 16px; border: 1px solid var(--gray-150); border-radius: 10px; background: var(--gray-0); }
.section-title { justify-content: space-between; margin-bottom: 14px; }
.section-title > div { display: flex; flex-direction: column; gap: 2px; }
.section-title span { color: var(--color-text-secondary); font-size: 12px; }
.section-title :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 4px; }
.account-options, .mode-options { width: 100%; display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
.account-option, .mode-option { display: flex; align-items: center; gap: 9px; padding: 12px; border: 1px solid var(--gray-200); border-radius: 8px; cursor: pointer; }
.account-option > span, .mode-option > span, .cover-tip > span { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.account-option small, .mode-option small, .cover-tip small { color: var(--color-text-secondary); }
.mode-option.selected { border-color: var(--main-500); background: var(--main-30); }
.mode-option.danger.selected { border-color: var(--color-error-500); background: var(--color-error-50); }
.field { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
.field > span { display: flex; justify-content: space-between; font-weight: 600; }
.field em { color: var(--color-text-tertiary); font-size: 12px; font-style: normal; font-weight: 400; }
.field em.over { color: var(--color-error-700); }
.cover-tip { display: flex; align-items: center; gap: 10px; margin-top: 12px; padding: 11px 12px; border-radius: 8px; background: var(--main-30); color: var(--main-800); }
.progress-card { padding: 14px; border-radius: 10px; background: var(--gray-50); }
.progress-heading { color: var(--main-700); }
.result-row { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 3px 10px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--gray-200); font-size: 13px; }
.result-row small { grid-column: 1 / -1; color: var(--color-error-700); }
.result-row :deep(.ant-btn), .history-result :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 4px; padding: 0; }
.result-row .draft_saved, .result-row .published { color: var(--color-success-700); }
.result-row .failed { color: var(--color-error-700); }
.result-row .uncertain, .history-result .uncertain, .uncertain-tip { color: var(--color-warning-900); }
.history-section { padding: 4px 2px; }
.history-card { padding: 9px 0; border-bottom: 1px solid var(--gray-150); }
.history-row { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; color: var(--color-text-secondary); font-size: 12px; }
.history-result { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 8px; margin: 7px 0 0 23px; color: var(--color-text-secondary); font-size: 12px; }
.history-result .draft_saved, .history-result .published { color: var(--color-success-700); }
.history-result .failed { color: var(--color-error-700); }
.drawer-footer { justify-content: flex-end; }
.drawer-footer > span { margin-right: auto; color: var(--color-text-secondary); font-size: 12px; }
.drawer-footer :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 5px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 620px) { .account-options, .mode-options { grid-template-columns: 1fr; } .drawer-footer > span { display: none; } }
</style>
