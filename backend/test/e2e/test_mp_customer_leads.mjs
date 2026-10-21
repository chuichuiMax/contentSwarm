import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { pathToFileURL } from 'node:url'

const root = process.env.MP_SOURCE_DIR || '/review'
const { caseShareViewData } = await import(pathToFileURL(resolve(root, 'utils/case-share-view.mjs')))
const { caseShareStatusMessage } = await import(pathToFileURL(resolve(root, 'utils/materials-logic.mjs')))
const helpers = readFileSync(resolve(root, 'utils/customer-leads.mjs'), 'utf8').replace(/^export /gm, '')
const tick = () => new Promise(setImmediate)
const lead = (id = 'one', revision = 1) => ({ id, revision, nickname: '客户', phone: '19912345678',
  phone_masked: '199****5678', unread: true, follow_status: 'pending', avatar_url: '/avatar/' + id,
  source_share_id: 'original-snapshot', acquired_at: '2026-10-16T01:00:00Z' })

function pageState(name, api, nextTick = async () => {}, contentApi = {}) {
  const events = [], routes = [], nativeCalls = [], listeners = {}
  const script = readFileSync(resolve(root, `pages/mine/${name}.vue`), 'utf8')
    .match(/<script>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '').replace('export default', 'const page =')
  const sourceCalls = []
  const context = { TabBar: {}, CaseShareContent: {}, caseShareViewData, caseShareStatusMessage, publicMediaUrl: url => url,
    mpContentApi: { getShare: async id => { sourceCalls.push(id); return { share: { title: '原案例', images: [] } } }, ...contentApi }, internalPageMixin: {}, mpLeadApi: { avatar: async () => 'local-avatar', ...api },
    markLeadRead: (...args) => api.read(...args),
    errorMessage: e => e.message || 'error', clearCaseSystemAction() {}, markCaseSystemReturn() {},
    openCaseSystemPage: (method, options) => nativeCalls.push({ method, options }),
    uni: { $on: (key, fn) => { listeners[key] = fn }, $off() {}, $emit: (key, value) => events.push(value),
      navigateTo: options => routes.push(options.url), stopPullDownRefresh() {}, showToast() {} } }
  const page = vm.runInNewContext(`${helpers}\n${script}\npage`, context)
  const instance = { ...page.data(), internalAccessGranted: true, ensureInternalAccess: async () => true,
    $nextTick: nextTick }
  for (const [name, fn] of Object.entries(page.methods)) instance[name] = fn.bind(instance)
  page.onLoad.call(instance, { id: 'one' })
  return { page, instance, events, routes, nativeCalls, listeners, sourceCalls }
}

test('phone-only customers use placeholders without requesting a missing avatar', async () => {
  let avatarRequests = 0
  const customer = { ...lead(), nickname: null, avatar_url: null }
  const api = { avatar: async () => { avatarRequests++ }, read: async () => {},
    get: async () => customer, list: async () => ({ items: [customer], total: 1 }) }
  const detail = pageState('lead-detail', api)
  detail.instance._visible = true
  await detail.instance.load()
  const list = pageState('leads', api)
  await list.instance.load(true)
  await tick()
  assert.equal(avatarRequests, 0)
  assert.equal(detail.instance.lead.nickname, null)
  assert.equal(list.instance.items[0].nickname, null)
  for (const file of ['leads', 'lead-detail']) {
    const template = readFileSync(resolve(root, `pages/mine/${file}.vue`), 'utf8').split('<script>')[0]
    assert.match(template, /nickname \|\| '预约客户'/)
  }
})

test('detail marks only the successfully displayed revision read; dialing and source preserve identity', async () => {
  let render
  const reads = [], follows = []
  const state = pageState('lead-detail', { get: async () => lead(), read: async (...args) => reads.push(args),
    follow: async (...args) => follows.push(args) }, () => new Promise(resolve => { render = resolve }))
  state.instance._visible = true
  const loading = state.instance.load()
  await tick()
  assert.equal(reads.length, 0)
  assert.equal(state.instance.loading, false)
  render()
  await loading
  assert.deepEqual(reads, [['one', 1]])
  assert.equal(state.events.at(-1).unread, false)
  state.instance.call()
  assert.equal(state.nativeCalls[0].options.phoneNumber, '19912345678')
  assert.deepEqual(state.sourceCalls, ['original-snapshot'])
  assert.equal(state.instance.sourceView.caseInfo.title, '原案例')
  assert.deepEqual(state.routes, [])
  assert.equal(follows.length, 0)
})

test('hidden or failed detail never clears badge; unread persistence failure remains retryable', async () => {
  const reads = []
  const hidden = pageState('lead-detail', { get: async () => lead(), read: async () => reads.push(1) })
  await hidden.instance.load()
  assert.equal(reads.length, 0)
  const failed = pageState('lead-detail', { get: async () => { throw Error('offline') }, read: async () => reads.push(1) })
  failed.instance._visible = true
  await failed.instance.load()
  assert.equal(failed.instance.loadError, 'offline')
  assert.equal(reads.length, 0)
  const retry = pageState('lead-detail', { get: async () => lead(), read: async () => { throw Error('offline') } })
  retry.instance._visible = true
  await retry.instance.load()
  assert.equal(retry.instance.lead.unread, true)
  assert.equal(retry.instance.readError, '已读状态未保存')
})

test('late read response cannot mark newer loaded appointment read', async () => {
  let completeOld
  const reads = []
  let revision = 1
  const state = pageState('lead-detail', { get: async () => lead('one', revision), read: async (id, version) => {
    reads.push(version)
    if (version === 1) await new Promise(resolve => { completeOld = resolve })
    else throw { statusCode: 409 }
  } })
  state.instance._visible = true
  const old = state.instance.load()
  await tick()
  revision = 2
  await state.instance.load()
  completeOld()
  await old
  assert.deepEqual(reads, [1, 2])
  assert.equal(state.instance.lead.revision, 2)
  assert.equal(state.instance.lead.unread, true)
})

test('list keeps filters and rows on return; follow does not clear unread and late avatars survive pagination', async () => {
  const avatars = {}, calls = [], follows = []
  const state = pageState('leads', { list: async query => {
    calls.push(query)
    return { items: [lead(query.page === 1 ? 'one' : 'two')], total: 2 }
  }, avatar: path => new Promise(resolve => { avatars[path] = resolve }),
  follow: async (...args) => follows.push(args) })
  state.instance.appliedKeyword = '客户'
  state.instance.startDate = '2026-10-16'
  await state.instance.load(true)
  await state.instance.load(false)
  avatars['/avatar/one']('first-avatar')
  await tick()
  assert.equal(state.instance.items[0].avatarSrc, 'first-avatar')
  await state.page.onShow.call(state.instance)
  assert.equal(calls.length, 2)
  assert.equal(calls[1].keyword, '客户')
  await state.instance.follow(state.instance.items[0])
  assert.equal(state.instance.items[0].follow_status, 'followed')
  assert.equal(state.instance.items[0].unread, true)
  state.listeners['customer-lead-changed']({ ...lead(), unread: false, follow_status: 'followed' })
  assert.equal(state.instance.items[0].unread, false)
  assert.equal(state.instance.items.length, 2)
  assert.equal(state.instance.items[0].avatarSrc, 'first-avatar')
  state.listeners['customer-lead-changed']({ ...lead(), unread: false, follow_status: 'pending' })
  assert.equal(state.instance.items[0].follow_status, 'followed')
  state.listeners['customer-lead-changed']({ ...lead('one', 2), avatarSrc: 'new-avatar' })
  assert.equal(state.instance.items[0].follow_status, 'pending')
  assert.equal(state.instance.items[0].avatarSrc, 'new-avatar')
})

test('old search and avatar responses cannot replace a newer search result', async () => {
  const complete = []
  const state = pageState('leads', { list: () => new Promise(resolve => complete.push(resolve)) })
  const old = state.instance.load(true)
  const recent = state.instance.load(true)
  complete[1]({ items: [lead('recent')], total: 1 })
  await recent
  complete[0]({ items: [lead('old')], total: 1 })
  await old
  assert.equal(state.instance.items[0].id, 'recent')
})

test('source shows all original images and contacts inline without a booking or navigation entry', async () => {
  const share = { title: '原始分享案例', building_name: '测试楼盘', area: '120', design_style: '雅致现代',
    sharer_name: '分享员工', sharer_phone: '19900000001', cover_url: '/cover.jpg',
    images: [{ id: 'first', url: '/first.jpg', webp_url: '/first.webp' }, { id: 'second', url: '/second.jpg' }] }
  const state = pageState('lead-detail', { get: async () => lead(), read: async () => {} }, undefined,
    { getShare: async () => ({ share }) })
  state.instance._visible = true
  await state.instance.load()
  await tick()
  const view = state.instance.sourceView
  assert.equal(view.coverUrl, '/cover.jpg')
  assert.equal(view.caseInfo.building, '测试楼盘')
  assert.equal(view.caseInfo.sharerName, '分享员工')
  assert.equal(view.caseInfo.sharerPhone, '19900000001')
  assert.deepEqual(view.images.map(image => image.displayUrl), ['/first.webp', '/second.jpg'])
  state.instance.previewSourceImage(1)
  state.instance.callSourceSharer()
  assert.equal(state.nativeCalls[0].method, 'previewImage')
  assert.equal(state.nativeCalls[0].options.current, '/second.jpg')
  assert.deepEqual(state.nativeCalls[0].options.urls, ['/first.jpg', '/second.jpg'])
  assert.equal(state.nativeCalls[1].options.phoneNumber, '19900000001')
  assert.deepEqual(state.routes, [])
  const detail = readFileSync(resolve(root, 'pages/mine/lead-detail.vue'), 'utf8')
  const content = readFileSync(resolve(root, 'components/case-share-content.vue'), 'utf8')
  const publicPage = readFileSync(resolve(root, 'pages/materials/shared-case.vue'), 'utf8')
  assert.match(detail, /<case-share-content/)
  assert.match(publicPage, /<case-share-content/)
  assert.doesNotMatch(detail + content, /预约免费参观工地|booking-cta|openBooking|openSource/)
  assert.match(publicPage, /@click="openBooking">预约免费参观工地/)
})

test('source failure and retry do not hide a lead or repeat its read operation', async () => {
  let failing = true, reads = 0
  const state = pageState('lead-detail', { get: async () => lead(), read: async () => { reads++ } }, undefined,
    { getShare: async () => { if (failing) throw Error('案例连接失败'); return { share: { title: '原案例' } } } })
  state.instance._visible = true
  await state.instance.load()
  await tick()
  assert.equal(state.instance.sourceError, '案例连接失败')
  assert.equal(state.instance.loadError, '')
  assert.equal(state.instance.lead.unread, false)
  assert.equal(state.instance.sourceLoading, false)
  failing = false
  await state.instance.loadSource()
  assert.equal(state.instance.sourceError, '')
  assert.equal(state.instance.sourceView.caseInfo.title, '原案例')
  assert.equal(reads, 1)
})

test('source loads independently of read and ignores stale responses after retry or unload', async () => {
  const pending = []
  let reads = 0
  const state = pageState('lead-detail', { get: async () => lead(), read: async () => { reads++ } }, undefined,
    { getShare: () => new Promise(resolve => pending.push(resolve)) })
  state.instance._visible = true
  await state.instance.load()
  assert.equal(reads, 1)
  assert.equal(state.instance.sourceLoading, true)
  const retry = state.instance.loadSource()
  pending[1]({ share: { title: '新响应' } })
  await retry
  pending[0]({ share: { title: '旧响应' } })
  await tick()
  assert.equal(state.instance.sourceView.caseInfo.title, '新响应')
  const last = state.instance.loadSource()
  state.page.onUnload.call(state.instance)
  pending[2]({ share: { title: '卸载后返回' } })
  await last
  assert.equal(state.instance.sourceView, null)
})

test('unavailable source is shown explicitly instead of an empty case', async () => {
  const state = pageState('lead-detail', { get: async () => lead(), read: async () => {} }, undefined,
    { getShare: async () => ({ share: { status: 'failed' } }) })
  await state.instance.load()
  await tick()
  assert.equal(state.instance.sourceView, null)
  assert.ok(state.instance.sourceError)
})
