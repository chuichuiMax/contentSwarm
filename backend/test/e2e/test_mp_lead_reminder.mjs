import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'

const root = process.env.MP_SOURCE_DIR || '/review'
const bundle = process.env.MP_WEIXIN_BUNDLE_DIR
const tick = () => new Promise(setImmediate)

function runtime(compiled = false) {
  const listeners = {}, timers = new Map(), toasts = [], summaries = [], reads = []
  let token = 'employee-a', timerId = 0
  const uni = {
    getStorageSync: () => token,
    setStorageSync: (key, value) => { token = value },
    removeStorageSync: () => { token = '' },
    $on: (name, callback) => { listeners[name] = callback },
    $emit: name => listeners[name]?.(),
    showToast: value => toasts.push(value),
    getSystemInfoSync: () => ({}),
    reLaunch() {}
  }
  const mpLeadApi = {
    summary: () => new Promise((resolve, reject) => summaries.push({ resolve, reject })),
    read: (id, revision) => new Promise((resolve, reject) => reads.push({ id, revision, resolve, reject }))
  }
  const context = {
    uni, reactive: value => value, mpLeadApi,
    BASE_URL: 'https://example.test', TOKEN_KEY: 'token',
    setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId },
    clearTimeout: id => timers.delete(id),
    getCurrentPages: () => [{ route: 'pages/generate/generate' }]
  }
  function load(file, names, dependencies = {}) {
    let exports
    if (compiled) {
      const module = { exports: {} }
      vm.runInNewContext(readFileSync(resolve(bundle, file), 'utf8'), {
        ...context, module, exports: module.exports,
        require: name => {
          if (name.endsWith('/common/vendor.js')) return { index: uni, reactive: context.reactive }
          assert.ok(dependencies[name], `Unexpected dependency: ${name}`)
          return dependencies[name]
        }
      })
      exports = module.exports
    } else {
      let source = readFileSync(resolve(root, file), 'utf8')
      if (file.endsWith('.vue')) source = source.match(/<script>([\s\S]*?)<\/script>/)[1]
      source = source.replace(/^import .*\r?\n/gm, '').replace(/^export /gm, '')
      exports = vm.runInNewContext(`(() => { ${source}\nreturn { ${names.join(', ')} } })()`, context)
    }
    Object.assign(context, exports)
    return exports
  }
  const request = load('utils/request.js', ['getToken', 'setToken', 'errorMessage'], {
    '../config.js': { BASE_URL: context.BASE_URL, TOKEN_KEY: 'token' },
    './safe-navigation.js': { safeReLaunch: path => uni.reLaunch({ url: path }) }
  })
  const reminder = load('utils/lead-reminder.js', ['leadReminderState', 'startLeadReminder', 'stopLeadReminder', 'markLeadRead'], {
    '../apis/mp.js': { mpLeadApi }, './request.js': request
  })
  return { ...reminder, ...request, context, load, timers, toasts, summaries, reads,
    poll() {
      assert.equal(timers.size, 1)
      const [id, timer] = [...timers.entries()][0]
      timers.delete(id)
      assert.equal(timer.delay, 15000)
      return timer.callback()
    } }
}

for (const compiled of bundle ? [false, true] : [false]) {
  const suffix = compiled ? 'compiled' : 'source'

  test(`${suffix}: other tabs refresh immediately, share one poll, and stop when hidden`, async () => {
    const app = runtime(compiled), production = {}, cases = {}
    const first = app.startLeadReminder(production)
    app.startLeadReminder(production)
    app.stopLeadReminder(production)
    app.startLeadReminder(cases)
    assert.equal(app.summaries.length, 1)
    app.summaries[0].resolve({ unread_count: 3 })
    await first
    assert.equal(app.leadReminderState.unreadCount, 3)
    app.stopLeadReminder(production)
    assert.equal(app.timers.size, 1, 'unloading an old page must not stop the visible page')
    const next = app.poll()
    assert.equal(app.summaries.length, 2)
    app.summaries[1].resolve({ unread_count: 4 })
    await next
    assert.equal(app.leadReminderState.unreadCount, 4)
    app.stopLeadReminder(cases)
    assert.equal(app.timers.size, 0)
    const resumed = app.startLeadReminder(cases)
    assert.equal(app.summaries.length, 3)
    app.summaries[2].resolve({ unread_count: 4 })
    await resumed
    app.stopLeadReminder(cases)
  })

  test(`${suffix}: read result updates remaining unread and rejects an older summary`, async () => {
    const app = runtime(compiled), page = {}
    const summary = app.startLeadReminder(page)
    const read = app.markLeadRead('lead-one', 2)
    assert.equal(app.reads[0].id, 'lead-one')
    app.reads[0].resolve({ unread_count: 1 })
    await read
    assert.equal(app.leadReminderState.unreadCount, 1)
    app.summaries[0].resolve({ unread_count: 2 })
    await summary
    assert.equal(app.leadReminderState.unreadCount, 1)
    const last = app.markLeadRead('lead-two', 1)
    app.reads[1].resolve({ unread_count: 0 })
    await last
    assert.equal(app.leadReminderState.unreadCount, 0)
    app.stopLeadReminder(page)
  })

  test(`${suffix}: failures keep the last unread count and retry on the next poll`, async () => {
    const app = runtime(compiled), page = {}
    const initial = app.startLeadReminder(page)
    app.summaries[0].resolve({ unread_count: 2 })
    await initial
    const failed = app.poll()
    app.summaries[1].reject(Error('offline'))
    await failed
    assert.equal(app.leadReminderState.unreadCount, 2)
    assert.equal(app.toasts.length, 1)
    const failedRead = app.markLeadRead('one', 1)
    app.reads[0].reject({ statusCode: 409 })
    await assert.rejects(failedRead)
    assert.equal(app.leadReminderState.unreadCount, 2)
    const retry = app.poll()
    app.summaries[2].resolve({ unread_count: 3 })
    await retry
    assert.equal(app.leadReminderState.unreadCount, 3)
    assert.equal(app.leadReminderState.error, '')
    app.stopLeadReminder(page)
  })

  test(`${suffix}: logout clears reminders and old responses cannot affect a new account`, async () => {
    const app = runtime(compiled), page = {}
    const initial = app.startLeadReminder(page)
    app.summaries[0].resolve({ unread_count: 5 })
    await initial
    const oldSummary = app.poll()
    const oldRead = app.markLeadRead('old-account-lead', 1)
    app.setToken('')
    assert.equal(app.leadReminderState.unreadCount, 0)
    assert.equal(app.timers.size, 0)
    app.startLeadReminder({})
    assert.equal(app.summaries.length, 2, 'logged-out visitors must not request employee reminders')
    app.setToken('employee-b')
    const newPage = {}, recent = app.startLeadReminder(newPage)
    app.summaries[2].resolve({ unread_count: 1 })
    await recent
    app.reads[0].resolve({ unread_count: 4 })
    app.summaries[1].resolve({ unread_count: 5 })
    await Promise.all([oldSummary, oldRead])
    assert.equal(app.leadReminderState.unreadCount, 1)
    assert.equal(app.timers.size, 1)
    app.stopLeadReminder(newPage)
  })

  test(`${suffix}: hidden in-flight responses do not restart polling`, async () => {
    const app = runtime(compiled), page = {}
    const pending = app.startLeadReminder(page)
    app.stopLeadReminder(page)
    app.summaries[0].resolve({ unread_count: 2 })
    await pending
    assert.equal(app.timers.size, 0)
  })
}

test('internal page lifecycle waits for access, pauses in background and cannot restart after hide', async () => {
  const app = runtime()
  let allow
  Object.assign(app.context, {
    mpMeApi: { get: () => new Promise(resolve => { allow = resolve }) },
    createCachedInternalAccessEvaluator: () => ({ evaluate: async (token, get) => ({ allowed: (await get()).allowed }) }),
    evaluateInternalAccess() {}, WORKSPACE_HOME_PATH: '/pages/generate/generate'
  })
  const { internalPageMixin: mixin } = app.load('utils/internal-access.js', ['internalPageMixin'])
  const page = mixin.data()
  mixin.onShow.call(page)
  const access = mixin.methods.ensureInternalAccess.call(page)
  assert.equal(app.summaries.length, 0)
  mixin.onHide.call(page)
  allow({ allowed: true })
  await access
  assert.equal(app.summaries.length, 0, 'an identity response after hide cannot start polling')
  mixin.onShow.call(page)
  assert.equal(app.summaries.length, 1)
  app.summaries[0].resolve({ unread_count: 2 })
  await tick()
  assert.equal(app.timers.size, 1)
  mixin.onHide.call(page)
  assert.equal(app.timers.size, 0)
  mixin.onShow.call(page)
  assert.equal(app.summaries.length, 2)
  app.summaries[1].resolve({ unread_count: 2 })
  await tick()
  mixin.onUnload.call(page)
  assert.equal(app.timers.size, 0)
})

test('both UI entries read the shared count and only the mine tab shows a dot', () => {
  const mine = readFileSync(resolve(root, 'pages/mine/mine.vue'), 'utf8')
  const tabs = readFileSync(resolve(root, 'components/tab-bar.vue'), 'utf8')
  assert.match(mine, /leadReminder: leadReminderState/)
  assert.match(mine, /@click="openLeads"/)
  assert.match(mine, /v-if="leadReminder.unreadCount > 0"/)
  assert.match(mine, /class="badge">\{\{ leadReminder.unreadCount \}\}/)
  assert.doesNotMatch(mine, /loadLeadSummary|_leadTimer/)
  assert.match(tabs, /leadReminder: leadReminderState/)
  assert.match(tabs, /item.key === 'mine' && leadReminder.unreadCount > 0" class="unread-dot"/)
  assert.doesNotMatch(tabs, /current === 'mine'.*unread/)
  for (const page of ['generate/generate', 'manage/manage', 'cover/cover', 'materials/materials', 'mine/mine']) {
    const source = readFileSync(resolve(root, `pages/${page}.vue`), 'utf8')
    assert.match(source, /internalPageMixin/)
    assert.match(source, /<tab-bar/)
  }
})

if (bundle) test('compiled render shows both reminders together on every selected tab and clears at zero', async () => {
  const app = runtime(true)
  const vendor = {
    index: app.context.uni,
    _export_sfc: (component, properties) => Object.assign(component, Object.fromEntries(properties)),
    f: (items, render) => items.map(render), e: Object.assign, t: String,
    o: callback => callback, p: value => value, resolveComponent: () => () => {}
  }
  const icons = app.load('utils/tab-bar-icons.js', [])
  function component(file, method) {
    let result
    vm.runInNewContext(readFileSync(resolve(bundle, file), 'utf8'), {
      wx: { [method]: value => { result = value } },
      require: name => {
        if (name.endsWith('/common/vendor.js')) return vendor
        if (name.endsWith('/utils/tab-bar-icons.js')) return icons
        if (name.endsWith('/utils/lead-reminder.js')) return app
        if (name.endsWith('/utils/internal-access.js')) return { internalPageMixin: {} }
        if (name.endsWith('/utils/request.js')) return app
        if (name.endsWith('/utils/safe-navigation.js')) return { safeReLaunch() {} }
        if (name.endsWith('/apis/mp.js')) return {}
        throw Error(`Unexpected component dependency: ${name}`)
      }
    })
    return result
  }
  const tabs = component('components/tab-bar.js', 'createComponent')
  const mine = component('pages/mine/mine.js', 'createPage')
  const tabData = tabs.data(), mineData = mine.data()
  assert.equal(tabData.leadReminder, mineData.leadReminder)
  const dotField = readFileSync(resolve(bundle, 'components/tab-bar.wxml'), 'utf8')
    .match(/wx:if="\{\{item\.(\w+)\}\}" class="unread-dot/)[1]
  const page = {}, summary = app.startLeadReminder(page)
  app.summaries[0].resolve({ unread_count: 3 })
  await summary
  for (const current of ['generate', 'manage', 'cover', 'materials', 'mine']) {
    const rendered = tabs.render({}, null, { current }, null, tabData, tabs.methods)
    assert.deepEqual(Array.from(rendered.a, item => item[dotField]), [false, false, false, false, true])
  }
  let menu = mine.render({ internalAccessGranted: true }, null, {}, null, mineData, {})
  assert.ok(Object.values(menu).includes('3'))
  const visibleMenu = menu
  const read = app.markLeadRead('last-lead', 1)
  app.reads[0].resolve({ unread_count: 0 })
  await read
  const cleared = tabs.render({}, null, { current: 'mine' }, null, tabData, tabs.methods)
  assert.ok(cleared.a.every(item => item[dotField] === false))
  menu = mine.render({ internalAccessGranted: true }, null, {}, null, mineData, {})
  assert.ok(Object.keys(menu).some(key => visibleMenu[key] === true && menu[key] === false))
  app.stopLeadReminder(page)
})
