<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CheckCircle2, LoaderCircle, QrCode, TriangleAlert } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  account: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'success'])

const loading = ref(false)
const session = ref(null)
const errorMessage = ref('')
let pollTimer = null
let pollErrors = 0

const isTerminal = computed(() =>
  ['completed', 'expired', 'failed'].includes(session.value?.status)
)

const stopPolling = () => {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = null
}

const poll = async () => {
  if (!props.open || !session.value?.id) return
  try {
    const response = await contentApi.getXiaohongshuLoginSession(session.value.id)
    pollErrors = 0
    session.value = response.session
    if (response.session.status === 'completed') {
      stopPolling()
      emit('success')
      window.setTimeout(() => emit('update:open', false), 900)
      return
    }
    if (!isTerminal.value) pollTimer = window.setTimeout(poll, 1600)
  } catch (error) {
    pollErrors += 1
    errorMessage.value = error.message || '获取扫码状态失败'
    if (pollErrors < 5) pollTimer = window.setTimeout(poll, 2500)
  }
}

const start = async () => {
  stopPolling()
  session.value = null
  errorMessage.value = ''
  pollErrors = 0
  if (!props.account?.id) return
  loading.value = true
  try {
    const response = await contentApi.loginXiaohongshuAccount(props.account.id)
    session.value = response.session
    await poll()
  } catch (error) {
    errorMessage.value = error.message || '创建登录二维码失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) void start()
    else stopPolling()
  }
)
onBeforeUnmount(stopPolling)
</script>

<template>
  <a-modal
    :open="open"
    title="绑定小红书账号"
    :footer="null"
    :mask-closable="false"
    width="460px"
    @cancel="emit('update:open', false)"
  >
    <div class="login-panel">
      <div class="account-chip">{{ account?.display_name }}</div>

      <div v-if="loading || (session && !session.qr_code && !isTerminal)" class="login-state">
        <LoaderCircle class="spin" :size="34" />
        <strong>正在准备安全登录二维码</strong>
        <span>二维码仅在本次登录会话中短暂保存</span>
      </div>

      <div v-else-if="session?.status === 'completed'" class="login-state success">
        <CheckCircle2 :size="42" />
        <strong>绑定成功</strong>
        <span>之后可在内容结果页直接选择这个账号分发</span>
      </div>

      <div v-else-if="session?.qr_code && !isTerminal" class="qr-state">
        <div class="qr-frame"><img :src="session.qr_code" alt="小红书登录二维码" /></div>
        <strong>打开小红书 App 扫码</strong>
        <span>请在手机端确认登录，页面会自动更新状态</span>
      </div>

      <div v-else class="login-state error">
        <TriangleAlert :size="38" />
        <strong>{{ errorMessage || session?.error_message || '二维码已失效' }}</strong>
        <span>没有修改您的账号数据，可以重新生成二维码</span>
        <a-button type="primary" @click="start"><QrCode :size="16" />重新生成</a-button>
      </div>

      <p class="privacy-tip">登录凭据按当前 Yuxi 用户和账号隔离保存，其他用户及管理员均不可查看。</p>
    </div>
  </a-modal>
</template>

<style scoped lang="less">
.login-panel { display: flex; flex-direction: column; align-items: center; padding: 10px 8px 2px; }
.account-chip { align-self: flex-start; padding: 4px 10px; border-radius: 999px; background: var(--gray-100); color: var(--color-text-secondary); font-size: 12px; }
.login-state, .qr-state { min-height: 300px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; text-align: center; }
.login-state > span, .qr-state > span { color: var(--color-text-secondary); font-size: 13px; }
.login-state.success { color: var(--color-success-700); }
.login-state.error { color: var(--color-error-700); }
.login-state :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; margin-top: 8px; }
.qr-frame { width: 226px; height: 226px; display: grid; place-items: center; padding: 12px; border: 1px solid var(--gray-200); border-radius: 16px; background: var(--gray-0); box-shadow: 0 8px 28px var(--shadow-1); }
.qr-frame img { width: 100%; height: 100%; object-fit: contain; }
.privacy-tip { width: 100%; margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--main-30); color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.spin { animation: spin 1s linear infinite; color: var(--main-color); }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
