<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  ArrowLeft,
  CircleOff,
  Link2,
  MonitorUp,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Smartphone,
  Trash2,
  UserRoundCog
} from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import XiaohongshuBrowserDrawer from '@/components/content/XiaohongshuBrowserDrawer.vue'

const router = useRouter()
const loading = ref(false)
const accounts = ref([])
const createOpen = ref(false)
const creating = ref(false)
const browserOpen = ref(false)
const editOpen = ref(false)
const activeAccount = ref(null)
const checkingAccountId = ref('')
const form = reactive({ display_name: '' })
const editForm = reactive({ display_name: '' })

const statusMap = {
  logged_in: { label: '已连接', className: 'success' },
  pending: { label: '等待扫码', className: 'pending' },
  expired: { label: '登录失效', className: 'warning' },
  error: { label: '状态异常', className: 'error' },
  unbound: { label: '未绑定', className: 'muted' }
}

const load = async () => {
  loading.value = true
  try {
    accounts.value = (await contentApi.listXiaohongshuAccounts()).items || []
  } catch (error) {
    message.error(error.message || '账号列表加载失败')
  } finally {
    loading.value = false
  }
}

const create = async () => {
  if (!form.display_name.trim()) return
  creating.value = true
  try {
    const response = await contentApi.createXiaohongshuAccount({
      display_name: form.display_name.trim()
    })
    createOpen.value = false
    form.display_name = ''
    await load()
    activeAccount.value = response.account
    browserOpen.value = true
  } catch (error) {
    message.error(error.message || '添加账号失败')
  } finally {
    creating.value = false
  }
}

const bind = (account) => {
  activeAccount.value = account
  browserOpen.value = true
}

const openBrowser = (account) => {
  activeAccount.value = account
  browserOpen.value = true
}

const openRename = (account) => {
  activeAccount.value = account
  editForm.display_name = account.display_name
  editOpen.value = true
}

const rename = async () => {
  if (!editForm.display_name.trim() || !activeAccount.value) return
  try {
    await contentApi.updateXiaohongshuAccount(activeAccount.value.id, {
      display_name: editForm.display_name.trim()
    })
    editOpen.value = false
    message.success('账号备注已更新')
    await load()
  } catch (error) {
    message.error(error.message || '账号备注更新失败')
  }
}

const check = async (account) => {
  checkingAccountId.value = account.id
  const previousStatus = account.login_status
  const previousVerifiedAt = account.last_verified_at
  try {
    await contentApi.checkXiaohongshuAccount(account.id)
    message.success('已开始检查账号状态')
    for (let attempt = 0; attempt < 15; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000))
      const items = (await contentApi.listXiaohongshuAccounts()).items || []
      accounts.value = items
      const updated = items.find((item) => item.id === account.id)
      if (
        !updated ||
        updated.login_status !== previousStatus ||
        updated.last_verified_at !== previousVerifiedAt
      ) {
        if (updated?.login_status === 'logged_in') message.success('账号连接正常')
        else if (updated) message.warning(updated.last_error_message || '账号需要重新绑定')
        return
      }
    }
    message.info('状态检查仍在处理中，请稍后刷新')
  } catch (error) {
    message.error(error.message || '状态检查失败')
  } finally {
    checkingAccountId.value = ''
  }
}

const toggle = async (account, enabled) => {
  try {
    await contentApi.updateXiaohongshuAccount(account.id, { enabled })
    await load()
  } catch (error) {
    message.error(error.message || '账号状态更新失败')
  }
}

const remove = (account) => {
  Modal.confirm({
    title: `移除“${account.display_name}”`,
    content: '将删除该账号在本用户下保存的登录凭据，历史分发记录会保留。',
    okText: '确认移除',
    cancelText: '取消',
    okType: 'danger',
    onOk: async () => {
      await contentApi.deleteXiaohongshuAccount(account.id)
      message.success('账号已移除')
      await load()
    }
  })
}

onMounted(load)
</script>

<template>
  <div class="accounts-page">
    <header class="page-header">
      <div class="header-copy">
        <button type="button" class="back-link" @click="router.push('/content/new')">
          <ArrowLeft :size="16" />返回内容生产
        </button>
        <span>Content Distribution</span>
        <h1><UserRoundCog :size="26" />账号管理</h1>
        <p>绑定您自己的小红书账号，用于把生成内容保存到草稿箱或直接发布。</p>
      </div>
      <a-button type="primary" size="large" @click="createOpen = true">
        <Plus :size="17" />添加账号
      </a-button>
    </header>

    <section class="privacy-banner">
      <ShieldCheck :size="22" />
      <div><strong>账号只属于您</strong><span>登录凭据、扫码会话和发布记录均按 Yuxi 用户隔离，其他用户无法访问。</span></div>
    </section>

    <section class="account-card">
      <div class="section-heading">
        <div><h2>小红书账号</h2><p>建议用容易识别的备注名区分品牌号、门店号或个人号。</p></div>
        <a-button :loading="loading" @click="load"><RefreshCw :size="15" />刷新</a-button>
      </div>

      <a-skeleton v-if="loading && !accounts.length" active :paragraph="{ rows: 4 }" />
      <div v-else-if="accounts.length" class="account-list">
        <article v-for="account in accounts" :key="account.id" class="account-row">
          <div class="platform-avatar"><Smartphone :size="22" /></div>
          <div class="account-main">
            <div class="account-title">
              <strong>{{ account.display_name }}</strong>
              <span class="status" :class="statusMap[account.login_status]?.className">
                {{ statusMap[account.login_status]?.label || account.login_status }}
              </span>
              <span v-if="!account.enabled" class="status muted">已停用</span>
            </div>
            <span>{{ account.platform_nickname || '绑定后显示小红书昵称' }}</span>
            <small v-if="account.last_verified_at">最近验证：{{ account.last_verified_at }}</small>
            <small v-if="account.last_error_message" class="error-text">{{ account.last_error_message }}</small>
          </div>
          <div class="account-actions">
            <a-switch :checked="account.enabled" @change="(value) => toggle(account, value)" />
            <a-button type="primary" :disabled="!account.enabled" @click="openBrowser(account)">
              <MonitorUp :size="15" />{{ account.login_status === 'logged_in' ? '打开草稿箱' : '打开登录界面' }}
            </a-button>
            <a-button v-if="account.login_status === 'logged_in'" :loading="checkingAccountId === account.id" :disabled="Boolean(checkingAccountId)" @click="check(account)"><RefreshCw :size="15" />检查状态</a-button>
            <a-button v-else type="text" @click="bind(account)"><Link2 :size="15" />重新绑定</a-button>
            <a-button type="text" aria-label="修改备注" @click="openRename(account)"><Pencil :size="15" /></a-button>
            <a-button danger type="text" aria-label="移除账号" @click="remove(account)"><Trash2 :size="16" /></a-button>
          </div>
        </article>
      </div>
      <div v-else class="empty-state">
        <CircleOff :size="40" />
        <h3>还没有绑定账号</h3>
        <p>先添加一个备注名，再使用小红书 App 扫码。整个过程不需要填写账号密码。</p>
        <a-button type="primary" @click="createOpen = true"><Plus :size="16" />添加第一个账号</a-button>
      </div>
    </section>

    <a-modal v-model:open="createOpen" title="添加小红书账号" :confirm-loading="creating" ok-text="添加并绑定" cancel-text="取消" @ok="create">
      <label class="form-field"><span>账号备注名</span><a-input v-model:value="form.display_name" :maxlength="120" placeholder="例如：品牌主账号" @press-enter="create" /><small>备注名只在 Yuxi 内显示，不会修改小红书昵称。</small></label>
    </a-modal>

    <a-modal v-model:open="editOpen" title="修改账号备注" ok-text="保存" cancel-text="取消" @ok="rename">
      <label class="form-field"><span>账号备注名</span><a-input v-model:value="editForm.display_name" :maxlength="120" @press-enter="rename" /></label>
    </a-modal>

    <XiaohongshuBrowserDrawer v-model:open="browserOpen" :account="activeAccount" @updated="load" />
  </div>
</template>

<style scoped lang="less">
.accounts-page { min-height: 100vh; padding: 24px var(--page-padding) 56px; background: var(--gray-25); color: var(--color-text); }
.page-header, .privacy-banner, .account-card { max-width: 1080px; margin-left: auto; margin-right: auto; }
.page-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 18px; }
.header-copy > span { color: var(--main-700); font-size: 12px; font-weight: 600; }
.header-copy h1 { display: flex; align-items: center; gap: 9px; margin: 6px 0; font-size: 27px; }
.header-copy p { margin: 0; color: var(--color-text-secondary); }
.back-link { display: flex; align-items: center; gap: 5px; margin: 0 0 18px; padding: 0; border: 0; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.back-link:hover { color: var(--main-color); }
.page-header :deep(.ant-btn), .section-heading :deep(.ant-btn), .account-actions :deep(.ant-btn), .empty-state :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 6px; }
.privacy-banner { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; padding: 14px 16px; border: 1px solid var(--main-100); border-radius: 10px; background: var(--main-30); color: var(--main-800); }
.privacy-banner > div { display: flex; flex-direction: column; gap: 2px; }
.privacy-banner span { font-size: 13px; color: var(--color-text-secondary); }
.account-card { padding: 22px; border: 1px solid var(--gray-150); border-radius: 12px; background: var(--gray-0); }
.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--gray-150); }
.section-heading h2 { margin: 0 0 4px; font-size: 18px; }
.section-heading p { margin: 0; color: var(--color-text-secondary); font-size: 13px; }
.account-list { display: flex; flex-direction: column; }
.account-row { display: flex; align-items: center; gap: 14px; padding: 18px 2px; border-bottom: 1px solid var(--gray-150); }
.account-row:last-child { border-bottom: 0; }
.platform-avatar { width: 44px; height: 44px; display: grid; place-items: center; flex: 0 0 auto; border-radius: 12px; background: #fff1f3; color: #ff2442; }
.account-main { min-width: 0; display: flex; flex: 1; flex-direction: column; gap: 3px; }
.account-main > span, .account-main small { color: var(--color-text-secondary); }
.account-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.status { padding: 2px 8px; border-radius: 999px; font-size: 11px; background: var(--gray-100); color: var(--gray-600); }
.status.success { background: var(--color-success-50); color: var(--color-success-700); }
.status.pending { background: var(--color-info-50); color: var(--color-info-700); }
.status.warning { background: var(--color-warning-50); color: var(--color-warning-900); }
.status.error { background: var(--color-error-50); color: var(--color-error-700); }
.error-text { color: var(--color-error-700) !important; }
.account-actions { display: flex; align-items: center; gap: 8px; }
.empty-state { min-height: 320px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; color: var(--color-text-secondary); }
.empty-state h3 { margin: 14px 0 4px; color: var(--color-text); }
.empty-state p { max-width: 440px; margin: 0 0 18px; }
.form-field { display: flex; flex-direction: column; gap: 7px; padding: 12px 0; }
.form-field > span { font-weight: 600; }
.form-field small { color: var(--color-text-secondary); }
@media (max-width: 760px) {
  .page-header { align-items: flex-start; flex-direction: column; }
  .account-row { align-items: flex-start; flex-wrap: wrap; }
  .account-actions { width: 100%; padding-left: 58px; flex-wrap: wrap; }
}
</style>
