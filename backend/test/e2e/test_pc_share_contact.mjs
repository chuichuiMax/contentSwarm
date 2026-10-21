// Exercises the PC modal methods with deterministic employee, API and clipboard boundaries.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'

const source = readFileSync(process.env.PC_SHARE_VIEW || '/app/src/views/MaterialLibraryView.vue', 'utf8')
const methods = source.slice(source.indexOf('async function openShareContact()'), source.indexOf('function formatSize('))
const urlHelper = source.slice(source.indexOf('function currentPublicShareUrl('), source.indexOf('async function openShareContact()'))
  .replace('import.meta.env.BASE_URL', "'/'")

function modal() {
  const storage = new Map(), posts = [], copies = [], messages = []
  const context = {
    shareContactRequest: 0,
    shareContact: { contact_name: '', contact_phone: '' },
    shareOpen: { value: false }, shareCreating: { value: false }, shareContactLoading: { value: false },
    shareAccountUid: { value: '' }, lastShareContact: { value: null }, shareUrlForManualCopy: { value: '' },
    selectedShareItemIds: { value: ['image'] }, targetEmployeeId: { value: 'viewed-employee' },
    userStore: { uid: 'sender', username: '平台用户名', phoneNumber: '19900000001' },
    employeeApi: { getMyEmployee: async () => ({ employee: { name: '登录员工', login_account: '19900000001' } }) },
    localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    materialLibraryApi: { createShare: async (ids, employeeId, contact) => {
      posts.push({ ids, employeeId, contact }); return { share: {
        page_url: 'https://share.test/case/new',
        mini_program_url: 'https://share.test/api/material-library/shares/new/open-mp?booking=false'
      } }
    } },
    URL, window: { location: { origin: 'https://share.test' } },
    copyShareUrl: async (url) => copies.push(url),
    message: Object.fromEntries(['warning', 'success', 'error'].map((level) => [level, (text) => messages.push(text)])),
    watch: () => {}
  }
  context.shareContactError = { get value() {
    return !context.shareContact.contact_name.trim() ? '请填写联系人'
      : /^1[3-9]\d{9}$/.test(context.shareContact.contact_phone.trim()) ? '' : '请填写正确的11位手机号码'
  } }
  vm.createContext(context)
  vm.runInContext(urlHelper + methods, context)
  return { c: context, storage, posts, copies, messages }
}

test('PC rejects loopback links before copying and does not claim sharing succeeded', async () => {
  for (const host of ['127.0.0.1', '127.0.0.2', 'localhost', 'preview.localhost', '[::1]', '0.0.0.0']) {
    const { c, copies, messages } = modal()
    await c.openShareContact()
    c.materialLibraryApi.createShare = async () => ({ share: {
      mini_program_url: `http://${host}:5173/api/material-library/shares/local/open-mp?booking=false`
    } })
    await c.createShare()
    assert.equal(copies.length, 0)
    assert.equal(c.shareOpen.value, true)
    assert.equal(c.shareUrlForManualCopy.value, '')
    assert.equal(messages.at(-1), '当前分享地址是本机地址，手机微信无法访问。请使用公网 PC 地址，或配置同一测试环境的公网预览入口。')
    assert.equal(messages.some(message => message.includes('链接已复制')), false)
  }
})

test('relative links cannot turn the local PC origin into a mobile share link', () => {
  const { c } = modal()
  c.window.location.origin = 'http://127.0.0.1:5173'
  assert.throws(() => c.currentPublicShareUrl('/api/material-library/shares/local/open-mp?booking=false'), /手机微信无法访问/)
  assert.equal(c.currentPublicShareUrl('https://cases.test/api/share?booking=false'), 'https://cases.test/api/share?booking=false')
})

test('PC defaults to the login employee even while viewing another employee gallery', async () => {
  const { c, posts, copies } = modal()
  await c.openShareContact()
  assert.equal(c.shareContact.contact_name, '登录员工')
  assert.equal(c.shareContact.contact_phone, '19900000001')
  assert.equal(posts.length, 0)
  c.shareOpen.value = false // Cancel/close has no creation side effect.
  assert.equal(posts.length, 0)
  await c.openShareContact()
  c.shareContact.contact_name = ' 新联系人 '
  c.shareContact.contact_phone = ' 13800000003 '
  await c.createShare()
  assert.equal(posts[0].contact.contact_name, '新联系人')
  assert.equal(posts[0].contact.contact_phone, '13800000003')
  assert.equal(posts[0].employeeId, 'viewed-employee')
  assert.deepEqual(copies, ['https://share.test/api/material-library/shares/new/open-mp?booking=false'])
  assert.equal(c.shareOpen.value, false)
  assert.equal(c.userStore.phoneNumber, '19900000001')
})

test('PC last contact is opt-in, defaults stay current, and storage is account-scoped', async () => {
  const { c, storage } = modal()
  storage.set('case-share-contact:sender', JSON.stringify({ contact_name: '代接人', contact_phone: '13800000003' }))
  await c.openShareContact()
  assert.equal(c.shareContact.contact_phone, '19900000001')
  Object.assign(c.shareContact, c.lastShareContact.value)
  assert.equal(c.shareContact.contact_phone, '13800000003')
  await c.createShare()
  await c.openShareContact()
  assert.equal(c.shareContact.contact_phone, '19900000001')
  c.userStore.uid = 'another-user'
  await c.openShareContact()
  assert.equal(c.lastShareContact.value, null)
})

test('PC invalid contact and changed account cannot create or copy links', async () => {
  const { c, posts, copies } = modal()
  await c.openShareContact()
  c.shareContact.contact_phone = '123'
  await c.createShare()
  assert.equal(posts.length, 0)
  c.shareContact.contact_phone = '13800000003'
  c.userStore.uid = 'another-user'
  await c.createShare()
  assert.equal(posts.length, 0)
  assert.equal(copies.length, 0)
})

test('PC clipboard failure retains the link and retries copying without another share', async () => {
  const { c, posts, copies } = modal()
  await c.openShareContact()
  c.copyShareUrl = async () => { throw new Error('复制权限不可用') }
  await c.createShare()
  assert.equal(c.shareOpen.value, true)
  assert.equal(c.shareUrlForManualCopy.value, 'https://share.test/api/material-library/shares/new/open-mp?booking=false')
  c.copyShareUrl = async (url) => copies.push(url)
  await c.createShare()
  assert.equal(posts.length, 1)
  assert.equal(copies.length, 1)
})

test('PC a cancelled profile request cannot overwrite a newly opened form', async () => {
  const { c } = modal()
  let finishOld
  c.employeeApi.getMyEmployee = () => new Promise((resolve) => { finishOld = resolve })
  const oldRequest = c.openShareContact()
  c.shareOpen.value = false
  c.employeeApi.getMyEmployee = async () => ({ employee: { name: '最新资料', login_account: '13900000004' } })
  await c.openShareContact()
  c.shareContact.contact_name = '正在填写的联系人'
  finishOld({ employee: { name: '旧资料', login_account: '19900000001' } })
  await oldRequest
  assert.equal(c.shareContact.contact_name, '正在填写的联系人')
})
