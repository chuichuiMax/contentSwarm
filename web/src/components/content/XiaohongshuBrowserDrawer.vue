<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { MousePointer2, RefreshCw, X } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  account: { type: Object, default: null }
})
const emit = defineEmits(['update:open', 'updated'])

const loading = ref(false)
const acting = ref(false)
const claiming = ref(false)
const controlClaimed = ref(false)
const session = ref(null)
const screenshotUrl = ref('')
const keyboardCapture = ref(null)
const errorMessage = ref('')
const lastUpdatedAt = ref('')
let screenshotTimer = null
let heartbeatTimer = null
let wheelTimer = null
let wheelDelta = 0
let pollingEpoch = 0
let actionSequence = 0
let actionQueue = Promise.resolve()

const revokeScreenshot = () => {
  if (screenshotUrl.value) URL.revokeObjectURL(screenshotUrl.value)
  screenshotUrl.value = ''
}

const stopPolling = () => {
  pollingEpoch += 1
  actionSequence += 1
  loading.value = false
  acting.value = false
  claiming.value = false
  if (screenshotTimer) window.clearTimeout(screenshotTimer)
  if (heartbeatTimer) window.clearTimeout(heartbeatTimer)
  if (wheelTimer) window.clearTimeout(wheelTimer)
  screenshotTimer = null
  heartbeatTimer = null
  wheelTimer = null
  wheelDelta = 0
}

const refreshScreenshot = async (epoch = pollingEpoch, accountId = props.account?.id) => {
  if (!props.open || !accountId || epoch !== pollingEpoch) return
  if (screenshotTimer) window.clearTimeout(screenshotTimer)
  screenshotTimer = null
  try {
    const response = await contentApi.getXiaohongshuBrowserScreenshot(accountId)
    const blob = await response.blob()
    if (epoch !== pollingEpoch || !props.open || props.account?.id !== accountId) return
    const nextUrl = URL.createObjectURL(blob)
    const previousUrl = screenshotUrl.value
    screenshotUrl.value = nextUrl
    lastUpdatedAt.value = new Date().toLocaleTimeString()
    if (previousUrl) URL.revokeObjectURL(previousUrl)
  } catch (error) {
    if (epoch === pollingEpoch && props.open && props.account?.id === accountId) {
      errorMessage.value = error.message || '草稿箱画面暂不可用'
    }
  } finally {
    if (epoch === pollingEpoch && props.open && props.account?.id === accountId) {
      screenshotTimer = window.setTimeout(() => refreshScreenshot(epoch, accountId), 1500)
    }
  }
}

const heartbeat = async (epoch = pollingEpoch, accountId = props.account?.id) => {
  if (!props.open || !accountId || epoch !== pollingEpoch) return
  if (heartbeatTimer) window.clearTimeout(heartbeatTimer)
  heartbeatTimer = null
  let nextDelay = 20000
  try {
    const response = await contentApi.heartbeatXiaohongshuBrowserSession(accountId)
    if (epoch !== pollingEpoch || !props.open || props.account?.id !== accountId) return
    session.value = response
    controlClaimed.value = Boolean(response.control_claimed)
    if (!response.browser?.logged_in || response.browser?.view !== 'drafts') nextDelay = 3000
    emit('updated')
  } catch (error) {
    if (epoch === pollingEpoch && props.open && props.account?.id === accountId) {
      errorMessage.value = error.message || '草稿箱会话心跳失败'
      nextDelay = 3000
    }
  } finally {
    if (epoch === pollingEpoch && props.open && props.account?.id === accountId) {
      heartbeatTimer = window.setTimeout(() => heartbeat(epoch, accountId), nextDelay)
    }
  }
}

const loadStatus = async () => {
  if (!props.account?.id) return
  const accountId = props.account.id
  const epoch = pollingEpoch
  loading.value = true
  try {
    const response = await contentApi.getXiaohongshuBrowserSession(accountId)
    if (epoch !== pollingEpoch || !props.open || props.account?.id !== accountId) return
    session.value = response
    errorMessage.value = ''
    await refreshScreenshot(epoch, accountId)
  } catch (error) {
    if (epoch === pollingEpoch && props.open && props.account?.id === accountId) {
      errorMessage.value = error.message || '获取草稿箱状态失败'
    }
  } finally {
    if (epoch === pollingEpoch) loading.value = false
  }
}

const start = async () => {
  stopPolling()
  revokeScreenshot()
  session.value = null
  controlClaimed.value = false
  errorMessage.value = ''
  if (!props.account?.id) return
  const accountId = props.account.id
  const epoch = pollingEpoch
  loading.value = true
  try {
    const response = await contentApi.openXiaohongshuBrowserSession(accountId, { target: 'drafts' })
    if (epoch !== pollingEpoch || !props.open || props.account?.id !== accountId) return
    session.value = response
    await claimControl({ notify: false })
    if (epoch !== pollingEpoch || !props.open || props.account?.id !== accountId) return
    await refreshScreenshot(epoch, accountId)
    if (epoch === pollingEpoch && props.open && props.account?.id === accountId) {
      const delay = response.browser?.logged_in && response.browser?.view === 'drafts' ? 20000 : 3000
      heartbeatTimer = window.setTimeout(() => heartbeat(epoch, accountId), delay)
    }
    emit('updated')
  } catch (error) {
    if (epoch === pollingEpoch && props.open && props.account?.id === accountId) {
      errorMessage.value = error.message || '草稿箱启动失败'
    }
  } finally {
    if (epoch === pollingEpoch) loading.value = false
  }
}

const performAction = async (payload) => {
  if (!props.open || !props.account?.id || !controlClaimed.value) return
  const accountId = props.account.id
  const epoch = pollingEpoch
  const sequence = ++actionSequence
  acting.value = true
  try {
    const response = await contentApi.actXiaohongshuBrowserSession(accountId, payload)
    if (sequence !== actionSequence || epoch !== pollingEpoch || props.account?.id !== accountId) return
    session.value = response
    errorMessage.value = ''
    await refreshScreenshot(epoch, accountId)
  } catch (error) {
    if (sequence === actionSequence && epoch === pollingEpoch && props.account?.id === accountId) {
      errorMessage.value = error.message || '草稿箱操作失败'
      message.error(errorMessage.value)
    }
  } finally {
    if (sequence === actionSequence) acting.value = false
  }
}

const sendAction = (payload) => {
  actionQueue = actionQueue.catch(() => {}).then(() => performAction(payload))
  return actionQueue
}

const focusKeyboard = () => nextTick(() => keyboardCapture.value?.focus({ preventScroll: true }))

const clickScreen = (event) => {
  if (!controlClaimed.value) {
    message.info('请先点击顶部“人工接管”')
    return
  }
  const image = event.currentTarget
  const rect = image.getBoundingClientRect()
  if (!rect.width || !image.naturalWidth) return
  const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight)
  const renderedWidth = image.naturalWidth * scale
  const renderedHeight = image.naturalHeight * scale
  const renderedLeft = rect.left + (rect.width - renderedWidth) / 2
  const renderedTop = rect.top + (rect.height - renderedHeight) / 2
  const localX = event.clientX - renderedLeft
  const localY = event.clientY - renderedTop
  if (localX < 0 || localY < 0 || localX > renderedWidth || localY > renderedHeight) return
  void sendAction({
    action: 'click',
    x: Math.round(localX / scale),
    y: Math.round(localY / scale)
  })
  focusKeyboard()
}

const captureInput = (event) => {
  if (!controlClaimed.value || event.isComposing) return
  const text = event.target.value
  event.target.value = ''
  if (text) void sendAction({ action: 'type', text })
}

const captureComposition = (event) => {
  if (!controlClaimed.value) return
  const text = event.data || event.target.value
  event.target.value = ''
  if (text) void sendAction({ action: 'type', text })
}

const captureKey = (event) => {
  if (!controlClaimed.value || event.isComposing) return
  if (event.ctrlKey && event.key.toLowerCase() === 'a') {
    event.preventDefault()
    void sendAction({ action: 'keypress', key: 'Control+A' })
    return
  }
  const supported = new Set([
    'Enter', 'Escape', 'Tab', 'Backspace', 'Delete', 'Home', 'End',
    'PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'
  ])
  if (supported.has(event.key)) {
    event.preventDefault()
    void sendAction({ action: 'keypress', key: event.key })
  }
}

const scrollScreen = (event) => {
  if (!controlClaimed.value) return
  wheelDelta += event.deltaY
  if (wheelTimer) window.clearTimeout(wheelTimer)
  wheelTimer = window.setTimeout(() => {
    const deltaY = Math.max(-2000, Math.min(2000, Math.round(wheelDelta)))
    wheelDelta = 0
    wheelTimer = null
    if (deltaY) void sendAction({ action: 'scroll', delta_y: deltaY })
  }, 80)
}

async function claimControl({ notify = true } = {}) {
  if (!props.account?.id || claiming.value) return
  const accountId = props.account.id
  const epoch = pollingEpoch
  const sequence = ++actionSequence
  claiming.value = true
  try {
    await contentApi.claimXiaohongshuBrowserSession(accountId)
    if (sequence !== actionSequence || epoch !== pollingEpoch || props.account?.id !== accountId) return
    controlClaimed.value = true
    errorMessage.value = ''
    if (notify) message.success('人工接管已启用，可直接点击、键入和滚动')
    focusKeyboard()
  } catch (error) {
    if (sequence === actionSequence && epoch === pollingEpoch && props.account?.id === accountId) {
      errorMessage.value = error.message || '启用人工接管失败'
    }
  } finally {
    if (sequence === actionSequence) claiming.value = false
  }
}

const closeSession = async () => {
  stopPolling()
  const accountId = session.value?.session?.account_id || props.account?.id
  if (accountId && session.value?.session) {
    try {
      await contentApi.closeXiaohongshuBrowserSession(accountId)
    } catch (error) {
      message.error(error.message || '关闭草稿箱会话失败')
    }
  }
  revokeScreenshot()
  emit('update:open', false)
  emit('updated')
}

watch(
  [() => props.open, () => props.account?.id],
  ([open, accountId], [previousOpen, previousAccountId]) => {
    if (open && accountId && (!previousOpen || accountId !== previousAccountId)) void start()
    else {
      stopPolling()
      revokeScreenshot()
    }
  }
)
onBeforeUnmount(() => {
  stopPolling()
  revokeScreenshot()
})
</script>

<template>
  <a-drawer
    :open="open"
    root-class-name="xhs-drafts-workspace"
    width="100vw"
    :closable="false"
    :mask-closable="false"
    :body-style="{ padding: 0, overflow: 'hidden' }"
    @close="closeSession"
  >
    <template #title>
      <div class="browser-title"><span>草稿箱</span><small>{{ account?.display_name }}</small></div>
    </template>

    <a-spin :spinning="loading">
      <div class="browser-workspace">
        <div class="workspace-toolbar">
          <div class="status-group">
            <span :class="['state-dot', session?.browser?.logged_in ? 'ready' : 'login']" />
            <span>{{ session?.browser?.logged_in ? '已登录' : '请在画面中完成登录' }}</span>
            <span v-if="session?.browser?.view === 'drafts'" class="drafts-ready">已进入草稿箱</span>
            <small v-if="lastUpdatedAt">画面更新于 {{ lastUpdatedAt }}</small>
          </div>
          <div class="toolbar-actions">
            <span class="interaction-tip">接管后可直接点击、键入、粘贴和滚动</span>
            <a-button
              :type="controlClaimed ? 'default' : 'primary'"
              :loading="claiming"
              :class="{ claimed: controlClaimed }"
              @click="controlClaimed ? focusKeyboard() : claimControl()"
            >
              <MousePointer2 :size="15" />{{ controlClaimed ? '人工接管中' : '人工接管' }}
            </a-button>
            <a-button :loading="loading" @click="loadStatus"><RefreshCw :size="15" />刷新</a-button>
            <a-button danger @click="closeSession"><X :size="15" />关闭</a-button>
          </div>
        </div>
        <a-alert v-if="errorMessage" class="workspace-alert" type="warning" :message="errorMessage" show-icon />
        <div
          v-if="screenshotUrl"
          class="screen-stage"
          :class="{ claimed: controlClaimed, acting }"
          @wheel.prevent="scrollScreen"
        >
          <img :src="screenshotUrl" alt="小红书草稿箱远程画面" draggable="false" @click="clickScreen" />
          <textarea
            ref="keyboardCapture"
            class="keyboard-capture"
            aria-label="远程浏览器键盘输入"
            autocapitalize="off"
            autocomplete="off"
            spellcheck="false"
            @input="captureInput"
            @compositionend="captureComposition"
            @keydown="captureKey"
          ></textarea>
        </div>
        <div v-else class="screen-empty">{{ errorMessage || '正在打开草稿箱…' }}</div>
      </div>
    </a-spin>
  </a-drawer>
</template>

<style scoped lang="less">
.browser-title, .workspace-toolbar, .status-group, .toolbar-actions { display: flex; align-items: center; gap: 8px; }
.browser-title { gap: 10px; font-weight: 600; }
.browser-title small, .status-group small, .interaction-tip { color: var(--color-text-secondary); font-size: 12px; font-weight: 400; }
.browser-workspace { display: flex; flex-direction: column; height: calc(100vh - 56px); min-height: 0; background: #15171a; }
.workspace-toolbar { min-height: 54px; padding: 8px 16px; justify-content: space-between; border-bottom: 1px solid var(--gray-150); background: var(--gray-0); }
.status-group { min-width: 0; font-size: 13px; }
.toolbar-actions { justify-content: flex-end; }
.toolbar-actions :deep(.ant-btn) { display: inline-flex; align-items: center; justify-content: center; gap: 5px; }
.toolbar-actions :deep(.ant-btn.claimed) { color: var(--color-success-700); border-color: var(--color-success-500); background: var(--color-success-50); }
.state-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-warning-500); }
.state-dot.ready { background: var(--color-success-500); }
.drafts-ready { padding: 2px 8px; border-radius: 999px; color: var(--color-success-700); background: var(--color-success-50); font-size: 12px; }
.workspace-alert { margin: 10px 16px 0; }
.screen-stage, .screen-empty { flex: 1; min-height: 0; }
.screen-stage { position: relative; display: flex; align-items: center; justify-content: center; overflow: hidden; background: #15171a; cursor: default; }
.screen-stage.claimed { cursor: crosshair; }
.screen-stage.acting::after { position: absolute; right: 18px; bottom: 18px; padding: 5px 10px; border-radius: 999px; color: #fff; background: rgba(0, 0, 0, .58); content: '操作中…'; font-size: 12px; pointer-events: none; }
.screen-stage img { display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; }
.screen-empty { display: grid; place-items: center; color: var(--gray-400); background: #15171a; }
.keyboard-capture { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; border: 0; opacity: 0; pointer-events: none; }
:global(.xhs-drafts-workspace .ant-drawer-content-wrapper) { width: 100vw !important; max-width: none !important; }
:global(.xhs-drafts-workspace .ant-drawer-header) { min-height: 56px; padding: 0 18px; }
:global(.xhs-drafts-workspace .ant-drawer-body) { padding: 0 !important; overflow: hidden !important; }
:global(.xhs-drafts-workspace .ant-spin-nested-loading), :global(.xhs-drafts-workspace .ant-spin-container) { height: 100%; }
@media (max-width: 760px) {
  .workspace-toolbar { align-items: flex-start; flex-direction: column; }
  .toolbar-actions { width: 100%; }
  .interaction-tip, .status-group small { display: none; }
}
</style>
