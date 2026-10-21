import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { pathToFileURL } from 'node:url'

const root = process.env.MP_SOURCE_DIR || '/review'
const source = readFileSync(resolve(root, 'pages/materials/shared-case.vue'), 'utf8')
const { caseShareViewData } = await import(pathToFileURL(resolve(root, 'utils/case-share-view.mjs')))
const { caseEntryOptions } = await import(pathToFileURL(resolve(root, 'utils/share-entry.mjs')))

function createPage(visitorApi = {}, contentApi = {}) {
  const modal = []
  const actions = []
  const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '').replace('export default', 'const page =')
  const page = vm.runInNewContext(`${script}\npage`, {
    mpVisitorApi: visitorApi,
    mpContentApi: contentApi,
    CaseShareContent: {}, caseShareViewData, caseEntryOptions, saveLastShareId() {}, publicMediaUrl: value => value,
    errorMessage: (error) => error.message || error.errMsg || 'error',
    beginCaseComponentAction: (method) => actions.push(method), finishCaseComponentAction() {},
    clearCaseSystemAction() {}, markCaseSystemReturn() {},
    uni: { login: ({ success }) => success({ code: 'verified-code' }), showModal: (data) => modal.push(data), setNavigationBarTitle() {}, showToast() {} },
    Date, clearTimeout
  })
  const instance = { ...page.data(), shareId: 'snapshot-token', $nextTick: async () => {} }
  for (const [key, method] of Object.entries(page.methods)) instance[key] = method.bind(instance)
  Object.defineProperty(instance, 'bookingReady', { get: () => page.computed.bookingReady.call(instance) })
  return { instance, page, modal, actions }
}

test('booking is a bottom sheet using native components without a new route', () => {
  assert.match(source, /class="booking-mask"/)
  assert.doesNotMatch(source, /chooseAvatar|type="nickname"|<checkbox/)
  assert.match(source, /open-type="getPhoneNumber\|agreePrivacyAuthorization"/)
  assert.doesNotMatch(source, /navigateTo.*booking/)
})

test('QR resolves the original case anonymously and opens booking once only after case and render succeed', async () => {
  let resolveCase
  let opened = 0
  const received = []
  const state = createPage({ resolveEntry: async code => { received.push(code); return { share_id: 'pc-original' } } },
    { getShare: async token => { received.push(token); return new Promise(resolve => { resolveCase = resolve }) } })
  state.instance.openBooking = () => { opened++ }
  state.page.onLoad.call(state.instance, { scene: 'c'.repeat(24) })
  await new Promise(setImmediate)
  assert.equal(opened, 0)
  resolveCase({ share: { status: 'completed', title: '原案例', images: [] } })
  await new Promise(setImmediate)
  assert.deepEqual(received, ['c'.repeat(24), 'pc-original'])
  assert.equal(opened, 1)
  state.page.onShow.call(state.instance)
  state.page.onShow.call(state.instance)
  assert.equal(opened, 1)
  assert.equal(state.actions.length, 0)
})

test('entry resolution failure cannot open booking or save a lead', async () => {
  let opened = 0, submitted = 0
  const state = createPage({ resolveEntry: async () => { throw Error('invalid entry') },
    submitBooking: async () => { submitted++ } })
  state.instance.openBooking = () => { opened++ }
  state.page.onLoad.call(state.instance, { entryCode: 'invalid', booking: '1' })
  await new Promise(setImmediate)
  assert.equal(state.instance.loadError, true)
  assert.equal(opened, 0)
  assert.equal(submitted, 0)
})

test('session preparation is independent of employee login and ignores a closed sheet', async () => {
  let complete
  const state = createPage({ createSession: () => new Promise((resolve) => { complete = resolve }) })
  state.instance.openBooking()
  await new Promise(setImmediate)
  state.instance.closeBooking()
  complete({ session_id: 'stale-session', expires_in: 600 })
  await new Promise(setImmediate)
  assert.equal(state.instance.bookingVisible, false)
  assert.equal(state.instance.bookingSession, '')
})

test('phone-only booking is ready without profile fields and denial cannot submit', async () => {
  let submitted = 0
  const { instance, modal } = createPage({ submitBooking: async () => { submitted++ } })
  instance.bookingSession = 'session'
  assert.equal(instance.bookingReady, true)
  await instance.submitBooking({ detail: { errMsg: 'getPhoneNumber:fail user deny' } })
  assert.equal(submitted, 0)
  assert.equal(modal.length, 0)
  assert.equal(instance.bookingSession, 'session')
})

test('duplicate native callbacks submit once and success waits for server persistence', async () => {
  let complete
  const calls = []
  const { instance, modal } = createPage({ submitBooking: (...args) => {
    calls.push(args)
    return new Promise((resolve) => { complete = resolve })
  } })
  Object.assign(instance, { bookingVisible: true, bookingSession: 'session' })
  const event = { detail: { code: 'native-phone-code', errMsg: 'getPhoneNumber:ok' } }
  const pending = instance.submitBooking(event)
  await instance.submitBooking(event)
  assert.equal(calls.length, 1)
  assert.equal(modal.length, 0)
  assert.equal(calls[0][0], 'snapshot-token')
  assert.equal(calls[0].length, 2)
  assert.deepEqual(Object.keys(calls[0][1]).sort(), ['consent', 'phone_code', 'session_id'])
  assert.equal(calls[0][1].phone_code, 'native-phone-code')
  assert.equal(calls[0][1].consent, 'true')
  complete({ success: true })
  await pending
  assert.equal(instance.bookingVisible, false)
  assert.equal(modal[0].title, '预约申请已提交')
})

test('save failure retains the sheet and allows retry without changing employee token', async () => {
  const { instance, modal } = createPage({ submitBooking: async () => { throw new Error('保存失败') } })
  Object.assign(instance, { bookingVisible: true, bookingSession: 'session' })
  await instance.submitBooking({ detail: { code: 'phone-code' } })
  assert.equal(instance.bookingVisible, true)
  assert.equal(instance.bookingError, '保存失败')
  assert.equal(instance.bookingSubmitting, false)
  assert.equal(instance.bookingReady, true)
  assert.equal(modal.length, 0)
})

test('native avatar return preserves the case while cancellation without hide expires its marker', () => {
  const callbacks = []
  let page = { route: 'pages/materials/shared-case' }
  const helper = readFileSync(resolve(root, 'utils/share-entry.mjs'), 'utf8').replace(/^export /gm, '')
  const context = vm.createContext({
    getCurrentPages: () => [page], setTimeout: (callback) => callbacks.push(callback)
  })
  vm.runInContext(helper, context)
  const owner = {}
  context.beginCaseComponentAction('chooseAvatar', owner)
  context.markCaseSystemHide()
  assert.equal(context.isCaseSystemReturn(page.route), true)
  context.markCaseSystemReturn(owner)
  context.markCaseSystemHide()
  assert.equal(context.isCaseSystemReturn(page.route), false)
  context.beginCaseComponentAction('chooseAvatar', owner)
  callbacks.at(-1)()
  assert.equal(context.isCaseSystemReturn(page.route), false)
})

test('case URL Link displays the case without opening or preparing booking', async () => {
  let opened = 0, prepared = 0
  const state = createPage({ resolveEntry: async () => ({ share_id: 'original' }),
    createSession: async () => { prepared++ } },
  { getShare: async () => ({ share: { status: 'completed', images: [] } }) })
  state.instance.openBooking = () => { opened++ }
  state.page.onLoad.call(state.instance, { entryCode: 'c'.repeat(24), booking: '0' })
  await new Promise(setImmediate)
  assert.equal(opened, 0)
  assert.equal(prepared, 0)
  assert.equal(state.instance.shareId, 'original')
})

test('native phone button stays disabled while preparing or submitting', () => {
  const { instance } = createPage()
  assert.equal(instance.bookingReady, false)
  instance.bookingSession = 'session'
  instance.bookingSessionLoading = true
  assert.equal(instance.bookingReady, false)
  instance.bookingSessionLoading = false
  assert.equal(instance.bookingReady, true)
  instance.bookingSubmitting = true
  assert.equal(instance.bookingReady, false)
})

test('a late successful save after page unload cannot open a modal on another case', async () => {
  let complete
  const { instance, page, modal } = createPage({ submitBooking: () => new Promise((resolve) => { complete = resolve }) })
  Object.assign(instance, { bookingVisible: true, bookingSession: 'session' })
  const pending = instance.submitBooking({ detail: { code: 'phone-code' } })
  page.onUnload.call(instance)
  complete({ success: true })
  await pending
  assert.equal(modal.length, 0)
})

test('phone-only visitor request sends no employee credentials and failure cannot redirect to login', async () => {
  const requests = []
  const changes = []
  const requestCode = readFileSync(resolve(root, 'utils/request.js'), 'utf8')
    .replace(/^import .*$/gm, '').replace(/^export /gm, '')
  const context = vm.createContext({
    BASE_URL: 'https://cases.test', TOKEN_KEY: 'employee-token',
    getCurrentPages: () => [{ route: 'pages/materials/shared-case' }],
    uni: {
      getStorageSync: () => 'existing-employee-token', removeStorageSync: () => changes.push('clear'),
      reLaunch: () => changes.push('navigate'), request: (options) => requests.push(options)
    }
  })
  vm.runInContext(requestCode, context)
  const apiCode = readFileSync(resolve(root, 'apis/mp.js'), 'utf8')
  const visitorApi = apiCode.slice(apiCode.indexOf('export const mpVisitorApi ='), apiCode.indexOf('export const mpLeadApi ='))
    .replace('export const mpVisitorApi', 'globalThis.visitorApi')
  vm.runInContext(visitorApi, context)
  const pending = context.visitorApi.submitBooking('case-a', { session_id: 'session', phone_code: 'code', consent: 'true' })
  assert.equal(requests[0].header.Authorization, undefined)
  assert.equal(requests[0].header['Content-Type'], 'application/x-www-form-urlencoded')
  assert.equal(requests[0].method, 'POST')
  requests[0].success({ statusCode: 401, data: { detail: { error: { message: '会话已过期' } } } })
  await assert.rejects(pending)
  assert.deepEqual(changes, [])
})

test('domain errors identify the failed WeChat API and explain real-device preview debugging', () => {
  const requestCode = readFileSync(resolve(root, 'utils/request.js'), 'utf8')
    .replace(/^import .*$/gm, '').replace(/^export /gm, '')
  const context = vm.createContext({})
  vm.runInContext(requestCode, context)
  for (const api of ['request', 'uploadFile', 'downloadFile']) {
    const message = context.errorMessage({ errMsg: `${api}:fail url not in domain list` })
    assert.equal(message, `域名未配置：请在公众平台配置 ${api} 合法域名；临时预览请在手机开启调试后重试`)
  }
})
