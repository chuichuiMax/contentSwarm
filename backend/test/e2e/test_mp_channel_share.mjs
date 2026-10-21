// Executes page lifecycle/methods with controllable network and time; no Developer Tools.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import vm from 'node:vm'

const root = process.env.MP_SOURCE_DIR || '/review'
const bundleRoot = process.env.MP_WEIXIN_BUNDLE_DIR
const logic = await import(pathToFileURL(resolve(root, 'utils/materials-logic.mjs')))
const { caseShareViewData } = await import(pathToFileURL(resolve(root, 'utils/case-share-view.mjs')))
const { caseEntryOptions } = await import(pathToFileURL(resolve(root, 'utils/share-entry.mjs')))
const source = (page) => readFileSync(resolve(root, `pages/materials/${page}.vue`), 'utf8')
const tick = () => new Promise(setImmediate)

function clock() {
  let time = 0, next = 0
  const tasks = new Map()
  return {
    setTimeout(fn, ms) { const id = ++next; tasks.set(id, { fn, at: time + ms }); return id },
    clearTimeout(id) { tasks.delete(id) },
    async advance(ms) {
      const end = time + ms
      while (true) {
        await tick()
        const [id, task] = [...tasks].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0] || []
        if (!task) break
        tasks.delete(id); time = task.at; task.fn()
      }
      time = end
      await tick()
    },
    tasks
  }
}

function page(name = 'materials', api = {}, host = 'wechat') {
  const timers = clock(), requests = [], toasts = [], clipboards = [], actions = [], storage = new Map()
  let entropy = 1
  const context = {
    ...logic, caseShareViewData, caseEntryOptions, ...timers, TabBar: {}, CaseShareContent: {}, internalPageMixin: {},
    publicMediaUrl: (url) => url ? (/^http/.test(url) ? url : `https://share.test${url}`) : '',
    errorMessage: (error) => error.message || error.errMsg,
    accountToken: 'employee-token',
    profile: { user_uid: 'sender', employee: { name: '登录员工', login_account: '19900000001' } },
    getToken: () => context.accountToken,
    mpMeApi: { get: async () => context.profile },
    clearCaseSystemAction() {}, markCaseSystemReturn() {}, saveLastShareId() {},
    beginCaseComponentAction: (name) => actions.push(name),
    mpContentApi: {
      galleries: async () => ({ galleries: [{ id: 'case', name: '云栖府' }] }),
      galleryItems: async () => ({ items: [] }),
      createShare(ids, token, contact) { requests.push({ ids: [...ids], token, contact }); return api.createShare?.(ids, token, contact)
        || Promise.resolve({ share: { id: token, status: 'pending' } }) },
      getShare: (token) => api.getShare?.(token) || Promise.resolve({ share: { id: token, status: 'pending' } })
    },
    wx: { getRandomValues({ length, success }) {
      assert.equal(length, 32)
      success({ randomValues: new Uint8Array(length).fill(entropy++).buffer })
    } },
    uni: { hideShareMenu() {}, showToast: (v) => toasts.push(v), hideHomeButton() {}, setNavigationBarTitle() {},
      getAppBaseInfo: () => ({ host: { env: host } }), showModal() {},
      getStorageSync: (key) => storage.get(key),
      setStorageSync: (key, value) => storage.set(key, JSON.parse(JSON.stringify(value))),
      setClipboardData({ data, success }) { clipboards.push(data); success() }
    }
  }
  if (bundleRoot) {
    const module = { exports: {} }
    vm.runInNewContext(readFileSync(resolve(bundleRoot, 'utils/materials-logic.js'), 'utf8'), {
      module, exports: module.exports
    })
    context.wx.createPage = (component) => { context.component = component }
    context.require = (id) => ({
      '../../common/vendor.js': { index: context.uni, get wx$1() { return context.wx }, _export_sfc: (component) => component },
      '../../common/assets.js': {},
      '../../utils/share-entry.js': context,
      '../../apis/mp.js': { mpContentApi: context.mpContentApi, mpMeApi: context.mpMeApi },
      '../../utils/request.js': context,
      '../../utils/internal-access.js': { internalPageMixin: {} },
      '../../utils/materials-logic.js': module.exports,
      '../../utils/case-share-view.js': { caseShareViewData }
    })[id]
    vm.runInNewContext(readFileSync(resolve(bundleRoot, `pages/materials/${name}.js`), 'utf8'), context)
  } else {
    const script = source(name).match(/<script>([\s\S]*?)<\/script>/)[1]
      .replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '').replace('export default', 'component =')
    vm.runInNewContext(script, context)
  }
  const component = context.component
  const instance = { ...component.data(), ensureInternalAccess: async () => true }
  for (const [key, method] of Object.entries(component.methods)) instance[key] = method.bind(instance)
  for (const [key, method] of Object.entries(component.computed)) Object.defineProperty(instance, key, { get: () => method.call(instance) })
  for (const hook of ['onLoad', 'onShow', 'onHide', 'onUnload', 'onShareAppMessage']) {
    if (component[hook]) instance[hook] = component[hook].bind(instance)
  }
  if (name === 'materials') {
    instance.galleries = [{ id: 'case', name: '云栖府' }]
    instance.activeGalleryId = 'case'
    instance.items = [{ id: 'one', galleryId: 'case' }, { id: 'two', galleryId: 'case' }]
    instance.selection = { galleryId: 'case', orderedIds: ['one'] }
    // Existing channel tests begin after confirming the default employee contact.
    instance.shareAccountUid = 'sender'
    instance.shareAccountToken = context.accountToken
    instance.confirmedContact = { contact_name: '登录员工', contact_phone: '19900000001' }
    instance.onLoad()
  }
  return { instance, timers, requests, toasts, clipboards, actions, context, storage }
}

test('selection and channel sheet do not create, native callback creates once without loading', async () => {
  const { instance: p, requests, timers, actions } = page()
  await tick()
  p.toggleItem(p.items[1]); await p.openShareSheet(); p.confirmContact()
  await timers.advance(1000)
  assert.equal(requests.length, 0)
  p.shareToWechat()
  assert.equal(requests.length, 0)
  const first = p.onShareAppMessage(), second = p.onShareAppMessage()
  assert.equal(requests.length, 1)
  assert.deepEqual(requests[0].ids, ['one', 'two'])
  assert.match(requests[0].token, /^[a-f0-9]{64}$/)
  assert.equal(first.path, second.path)
  assert.equal(first.imageUrl, logic.DEFAULT_CASE_SHARE_COVER)
  assert.equal(first.title, '云栖府案例分享')
  assert.equal(actions[0], 'shareAppMessage')
  assert.doesNotMatch(source('materials'), /:loading=|scheduleSharePreparation|350|prepareShareForSheet/)
  assert.match(source('materials'), /:open-type="canStartShare \? 'share' : ''"/)
  p.onUnload(); await timers.advance(2600)
  assert.equal((await first.promise).path, first.path)
})

test('ready within native window uses public JPEG, never fetches original or downloads cover', async () => {
  const { instance: p, timers } = page('materials', {
    createShare: async (_, token) => ({ share: { id: token, status: 'completed', title: '正式图库', card_cover_url: '/cover.jpg' } })
  })
  await tick()
  const payload = p.onShareAppMessage()
  assert.equal(payload.imageUrl, logic.DEFAULT_CASE_SHARE_COVER)
  const ready = await payload.promise
  assert.equal(ready.imageUrl, 'https://share.test/cover.jpg')
  assert.equal(ready.path, payload.path)
  assert.equal(ready.title, '正式图库')
  assert.equal(timers.tasks.size, 0)
  assert.doesNotMatch(source('materials'), /downloadFile|preloadShareCover/)
})

test('native timeout keeps same usable token; late completion does not mutate sent default', async () => {
  let complete
  const { instance: p, timers, requests } = page('materials', {
    createShare: (_, token) => new Promise((resolve) => { complete = () => resolve({ share: { id: token,
      status: 'completed', card_cover_url: '/ready.jpg' } }) })
  })
  await tick()
  const native = p.onShareAppMessage()
  await timers.advance(2500)
  const sent = await native.promise
  assert.equal(sent.imageUrl, logic.DEFAULT_CASE_SHARE_COVER)
  complete(); await tick()
  assert.equal(sent.imageUrl, logic.DEFAULT_CASE_SHARE_COVER)
  assert.equal(p.shareSnapshot.coverUrl, 'https://share.test/ready.jpg')
  assert.equal(requests.length, 1)
  assert.equal(logic.buildWechatSharePayload(p.shareSnapshot).path, sent.path)
})

test('selection change and unload never auto-submit or apply an old result to the new selection', async () => {
  const pending = []
  const { instance: p, timers, requests } = page('materials', {
    createShare: (_, token) => new Promise((resolve) => pending.push(() => resolve({ share: { id: token, status: 'completed' } })))
  })
  await tick()
  const old = p.onShareAppMessage()
  p.toggleItem(p.items[1]); await p.openShareSheet(); p.confirmContact()
  pending[0](); await tick()
  assert.equal(p.shareSnapshot, null)
  assert.equal(requests.length, 1)
  const current = p.onShareAppMessage()
  assert.notEqual(old.path, current.path)
  assert.deepEqual(requests[1].ids, ['one', 'two'])
  p.onUnload(); pending[1](); await tick(); await timers.advance(3000)
  assert.equal(p.shareSnapshot, null)
  assert.equal(requests.length, 2)
})

test('lost POST response retries same token; confirmed failure reports on return and next click gets new token', async () => {
  let mode = 'network'
  const { instance: p, timers, requests, toasts } = page('materials', {
    createShare: async (_, token) => {
      if (mode === 'network') throw new Error('请求超时')
      return { share: { id: token, status: 'failed' } }
    },
    getShare: async (token) => ({ share: { id: token, status: mode === 'network' ? 'unknown' : 'failed' } })
  })
  await tick()
  p.onHide(); const original = p.onShareAppMessage()
  await timers.advance(2500)
  assert.equal(toasts.length, 0)
  p.onShareAppMessage(); await tick()
  assert.equal(requests[0].token, requests[1].token)
  mode = 'failed'; await timers.advance(2000)
  assert.equal(p.shareSnapshot.status, 'failed')
  p._shareHidden = false; p.reportShareFailure()
  assert.match(toasts.at(-1).title, /生成失败/)
  const retry = p.onShareAppMessage(); await tick()
  assert.notEqual(retry.path, original.path)
  p.onUnload(); await timers.advance(3000)
})

test('enterprise option copies accepted pending link in WeChat and uses native callback inside WeCom', async () => {
  const normal = page()
  await tick(); await normal.instance.shareToWorkWechat()
  assert.equal(normal.clipboards.length, 1)
  assert.equal(normal.clipboards[0], `https://share.test/share/case/${normal.requests[0].token}`)
  assert.equal(normal.instance.shareSheetVisible, false)
  const work = page('materials', {}, 'wework')
  await tick(); await work.instance.shareToWorkWechat()
  assert.equal(work.requests.length, 0)
  work.instance.onShareAppMessage()
  assert.equal(work.requests.length, 1)
  assert.equal(work.clipboards.length, 0)
  normal.instance.onUnload(); work.instance.onUnload()
  await normal.timers.advance(3000); await work.timers.advance(3000)
})

test('public page handles pending then completed and forwards pending token with generic cover', async () => {
  let status = 'pending', reads = 0
  const { instance: p, timers } = page('shared-case', { getShare: async (id) => {
    reads++; return { share: { id, status, title: '测试', images: [{ url: '/image.png', webp_url: '/image.webp' }] } }
  } })
  p.onLoad({ shareId: 'a'.repeat(64) }); await tick()
  assert.equal(p.shareStatus, 'pending'); assert.equal(p.images.length, 0)
  assert.equal(p.onShareAppMessage().imageUrl, logic.DEFAULT_CASE_SHARE_COVER)
  assert.match(p.onShareAppMessage().path, /a{64}$/)
  status = 'completed'; await timers.advance(2000)
  assert.equal(p.shareStatus, 'completed'); assert.equal(p.images.length, 1)
  assert.equal(p.images[0].displayUrl, 'https://share.test/image.webp')
  assert.equal(reads, 2); assert.equal(timers.tasks.size, 0)
})

test('unknown polls finitely; failed stops; retry and unload ignore stale requests', async () => {
  let status = 'unknown', reads = 0, resolveLate
  const { instance: p, timers } = page('shared-case', { getShare: async (id) => {
    reads++
    if (status === 'late') return new Promise((resolve) => { resolveLate = resolve })
    return { share: { id, status } }
  } })
  p.onLoad({ shareId: 'a'.repeat(64) }); await tick(); await timers.advance(60000)
  assert.equal(reads, 16); assert.equal(p.statusMessage, '案例暂未就绪，请稍后重试')
  assert.equal(timers.tasks.size, 0)
  status = 'failed'; await p.loadShare(); assert.match(p.statusMessage, /案例生成失败/)
  assert.equal(timers.tasks.size, 0)
  status = 'late'; const request = p.loadShare(); await tick(); p.onUnload()
  resolveLate({ share: { status: 'completed', images: [] } }); await request
  assert.equal(p.shareStatus, 'failed'); assert.equal(timers.tasks.size, 0)
})

test('secure token unavailability cannot emit a native card or POST, no weak random fallback', async () => {
  const { instance: p, context, toasts, requests } = page()
  await tick(); p.nextShareToken = ''; context.wx = {}; p.primeShareToken(); await tick()
  assert.equal(p.canStartShare, false)
  p.shareToWechat()
  assert.match(toasts[0].title, /升级微信/)
  assert.equal(requests.length, 0)
  assert.doesNotMatch(source('materials'), /Math\.random/)
})

test('contact modal defaults to login employee, cancellation and invalid input never create a share', async () => {
  const { instance: p, requests, context } = page()
  await tick()
  await p.openShareSheet()
  assert.equal(p.contactVisible, true)
  assert.equal(p.shareSheetVisible, false)
  assert.equal(p.contact.contact_name, '登录员工')
  assert.equal(p.contact.contact_phone, '19900000001')
  assert.equal(p.onShareAppMessage(), null)
  p.contact.contact_phone = '123'; p.confirmContact()
  assert.equal(p.contactVisible, true)
  assert.equal(p.confirmedContact, null)
  p.closeContact()
  assert.equal(p.contactVisible, false)
  assert.equal(requests.length, 0)
  await p.openShareSheet()
  context.accountToken = 'different-token'
  p.confirmContact()
  assert.equal(p.shareSheetVisible, false)
  assert.equal(p.onShareAppMessage(), null)
  p.onUnload()
})

test('last contact restores only on click for its account and confirmation alone does not POST', async () => {
  const { instance: p, storage, requests, context } = page()
  storage.set('case-share-contact:sender', { contact_name: '代接联系人', contact_phone: '13800000003' })
  await p.openShareSheet()
  assert.equal(p.contact.contact_phone, '19900000001')
  p.restoreLastContact()
  assert.equal(p.contact.contact_phone, '13800000003')
  p.confirmContact()
  assert.equal(p.shareSheetVisible, true)
  assert.equal(requests.length, 0)
  await p.openShareSheet()
  assert.equal(p.contact.contact_name, '登录员工')
  context.accountToken = 'other-token'
  context.profile = { user_uid: 'other', employee: { name: '另一员工', login_account: '13900000004' } }
  await p.openShareSheet()
  assert.equal(p.lastShareContact, null)
  assert.equal(p.contact.contact_phone, '13900000004')
  p.onUnload()
})

test('changing contact on the same images creates a new token and freezes retry data', async () => {
  const { instance: p, requests, timers } = page()
  await tick()
  const original = p.onShareAppMessage()
  await p.openShareSheet()
  p.contact = { contact_name: ' 新联系人 ', contact_phone: ' 13800000003 ' }
  p.confirmContact()
  const current = p.onShareAppMessage()
  assert.notEqual(original.path, current.path)
  assert.equal(requests[0].contact.contact_phone, '19900000001')
  assert.equal(requests[1].contact.contact_name, '新联系人')
  assert.equal(requests[1].contact.contact_phone, '13800000003')
  p.contact.contact_phone = '13900000004'
  const retry = p.onShareAppMessage()
  assert.equal(retry.path, current.path)
  assert.equal(requests.length, 2)
  p.onUnload(); await timers.advance(3000)
})

test('enterprise copy also uses the confirmed contact and account changes cannot reuse old snapshots', async () => {
  const { instance: p, requests, clipboards, context, timers } = page()
  await tick()
  await p.openShareSheet()
  p.contact = { contact_name: '代接人', contact_phone: '13800000003' }
  p.confirmContact()
  await p.shareToWorkWechat()
  assert.equal(requests[0].contact.contact_phone, '13800000003')
  assert.equal(clipboards.length, 1)
  context.accountToken = 'another-token'
  assert.equal(p.onShareAppMessage(), null)
  context.profile = { user_uid: 'another-user', employee: { name: '代接人', login_account: '13800000003' } }
  await p.openShareSheet(); p.confirmContact()
  p.onShareAppMessage()
  assert.notEqual(requests[0].token, requests[1].token)
  p.onUnload(); await timers.advance(3000)
})

test('late employee response cannot overwrite a reopened contact form', async () => {
  const { instance: p, context } = page()
  let finishOld
  context.mpMeApi.get = () => new Promise((resolve) => { finishOld = resolve })
  const oldRequest = p.openShareSheet()
  p.closeContact()
  context.mpMeApi.get = async () => context.profile
  await p.openShareSheet()
  p.contact.contact_name = '正在填写的联系人'
  finishOld({ user_uid: 'sender', employee: { name: '旧资料', login_account: '19900000001' } })
  await oldRequest
  assert.equal(p.contact.contact_name, '正在填写的联系人')
  p.onUnload()
})
