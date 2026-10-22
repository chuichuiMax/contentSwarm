import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'

// 编译产物中的工号登录链路模拟；原生输入与布局仍需在微信环境验收。
const bundleRoot = process.env.MP_WEIXIN_BUNDLE_DIR
assert.ok(bundleRoot, 'Set MP_WEIXIN_BUNDLE_DIR to the compiled mp-weixin directory')
const employee = { id: 'employee-test', login_account: 'test-account' }
const tick = () => new Promise((resolve) => setImmediate(resolve))

function loadBundle(initialToken = '') {
  const modules = new Map()
  const requests = []
  const modals = []
  const jumps = []
  const wechatLogins = []
  let token = initialToken
  let page
  const uni = {
    $emit: () => {},
    $on: () => {},
    getSystemInfoSync: () => ({ platform: 'devtools' }),
    getStorageSync: () => token,
    setStorageSync: (key, value) => { token = value },
    removeStorageSync: () => { token = '' },
    login: (options) => { wechatLogins.push(options); options.success({ code: 'test-js-code' }) },
    request: (options) => requests.push(options),
    showModal: (options) => modals.push(options),
    showToast: () => {},
    reLaunch: ({ url }) => jumps.push(url)
  }
  const vendor = { index: uni, reactive: (state) => state, _export_sfc: (component) => component }
  function load(path) {
    const absolute = resolve(bundleRoot, path)
    if (absolute === resolve(bundleRoot, 'common/vendor.js')) return vendor
    if (modules.has(absolute)) return modules.get(absolute).exports
    const module = { exports: {} }
    modules.set(absolute, module)
    vm.runInNewContext(readFileSync(absolute, 'utf8'), {
      module, exports: module.exports,
      require: (id) => load(resolve(dirname(absolute), id)),
      getCurrentPages: () => [{ route: 'pages/login/login' }],
      wx: { createPage: (component) => { page = component } }
    }, { filename: absolute })
    return module.exports
  }
  const access = load('utils/internal-access.js')
  load('pages/login/login.js')
  const instance = page.data()
  for (const [name, method] of Object.entries(page.methods)) instance[name] = method.bind(instance)
  return { access, instance, page, requests, modals, jumps, wechatLogins, get token() { return token } }
}

test('compiled employee login shares blur lookup, signs in and enters the workspace without another identity request', async () => {
  const bundle = loadBundle()
  bundle.instance.onWorkIdInput({ detail: { value: '001A' } })
  const lookup = bundle.instance.lookupEmployee()
  const login = bundle.instance.onLogin()
  await tick()
  assert.match(bundle.requests[0].url, /\/api\/mp\/auth\/employee\/lookup$/)
  assert.equal(bundle.requests[0].data.employee_code, '001A')
  assert.equal(bundle.requests[0].header.Authorization, undefined)
  bundle.requests[0].success({ statusCode: 200, data: {
    employee: { employee_code: '001A', name: '测试员工', login_account: '13900000001' }
  } })
  await tick()
  assert.match(bundle.requests[1].url, /\/api\/mp\/auth\/employee\/login$/)
  assert.equal(bundle.requests[1].data.employee_code, '001A')
  bundle.requests[1].success({ statusCode: 200, data: { access_token: 'confirmed-token', employee } })
  await Promise.all([lookup, login])
  assert.equal(bundle.token, 'confirmed-token')
  assert.equal(bundle.instance.loginLoading, false)
  assert.deepEqual(bundle.jumps, ['/pages/generate/generate'])
  assert.equal(await bundle.access.requireInternalAccess(), true)
  assert.equal(bundle.requests.length, 2)
})

test('compiled cold-start identity failure preserves the token and recovers after retry', async () => {
  const bundle = loadBundle('saved-token')
  const check = bundle.access.requireInternalAccess({ redirect: false })
  assert.match(bundle.requests[0].url, /\/api\/mp\/me$/)
  assert.equal(bundle.requests[0].timeout, 15000)
  bundle.requests[0].fail({ errMsg: 'request:fail timeout' })
  await tick()
  assert.equal(bundle.token, 'saved-token')
  assert.deepEqual(bundle.jumps, [])
  assert.equal(bundle.modals[0].confirmText, '重试')
  bundle.modals[0].success({ confirm: true })
  await tick()
  bundle.requests[1].success({ statusCode: 200, data: { employee } })
  assert.equal(await check, true)
  assert.equal(bundle.token, 'saved-token')
})

const phoneEvent = { detail: { code: 'verified-phone-code', errMsg: 'getPhoneNumber:ok' } }
function respond(request, data, statusCode = 200) {
  request.success({ statusCode, data })
}

async function authorize(bundle) {
  const pending = bundle.instance.onWechatLogin(phoneEvent)
  await tick()
  assert.match(bundle.requests[0].url, /\/api\/mp\/auth\/wechat\/code$/)
  assert.equal(bundle.requests[0].data.code, 'test-js-code')
  respond(bundle.requests[0], { session_id: 'wx-session' })
  await tick()
  assert.match(bundle.requests[1].url, /\/api\/mp\/auth\/wechat\/phone$/)
  assert.equal(JSON.stringify(bundle.requests[1].data), JSON.stringify({ session_id: 'wx-session', code: 'verified-phone-code' }))
  return { pending }
}

test('compiled one-click login uses verified phone and the same confirmed employee session', async () => {
  const bundle = loadBundle()
  bundle.instance.workId = 'a-different-employee'
  const { pending } = await authorize(bundle)
  respond(bundle.requests[1], { session_id: 'wx-session', name: '测试员工', phone_masked: '139****0001' })
  await tick()
  assert.match(bundle.requests[2].url, /\/api\/mp\/auth\/confirm$/)
  assert.equal(JSON.stringify(bundle.requests[2].data), JSON.stringify({ session_id: 'wx-session' }))
  respond(bundle.requests[2], { access_token: 'wechat-token', employee })
  await pending
  assert.equal(bundle.token, 'wechat-token')
  assert.equal(await bundle.access.requireInternalAccess(), true)
  assert.equal(bundle.requests.length, 3)
  assert.deepEqual(bundle.jumps, ['/pages/generate/generate'])
  assert.equal(bundle.instance.wechatLoading, false)
})

test('cancelled or unsuccessful phone authorization never starts login', async () => {
  for (const errMsg of ['getPhoneNumber:fail user deny', 'getPhoneNumber:fail', 'getPhoneNumber:fail privacy permission is not authorized']) {
    const bundle = loadBundle()
    await bundle.instance.onWechatLogin({ detail: { errMsg } })
    assert.equal(bundle.requests.length, 0)
    assert.equal(bundle.wechatLogins.length, 0)
    assert.equal(bundle.token, '')
    assert.ok(bundle.instance.wechatError)
    assert.equal(bundle.instance.employeeError, '')
  }
})

test('one-click and employee-code requests are mutually exclusive and repeated callbacks are ignored', async () => {
  const bundle = loadBundle()
  const pending = bundle.instance.onWechatLogin(phoneEvent)
  await bundle.instance.onWechatLogin(phoneEvent)
  await bundle.instance.onLogin()
  await tick()
  assert.equal(bundle.wechatLogins.length, 1)
  assert.equal(bundle.requests.length, 1)
  respond(bundle.requests[0], { detail: { error: { message: '微信服务暂不可用' } } }, 503)
  await pending
  assert.equal(bundle.instance.wechatLoading, false)
  bundle.instance.loginLoading = true
  await bundle.instance.onWechatLogin(phoneEvent)
  assert.equal(bundle.wechatLogins.length, 1)
})

test('unregistered, disabled and PC-only employees cannot establish a frontend session', async () => {
  for (const [status, message] of [[404, '未找到对应员工账号'], [403, '员工已停用'], [403, '该员工未开通小程序登录']]) {
    const bundle = loadBundle()
    const { pending } = await authorize(bundle)
    respond(bundle.requests[1], { detail: { error: { message } } }, status)
    await pending
    assert.equal(bundle.token, '')
    assert.equal(bundle.instance.wechatError, message)
    assert.equal(bundle.instance.employeeError, '')
    assert.equal(bundle.instance.wechatLoading, false)
    assert.deepEqual(bundle.jumps, [])
  }
})

test('an authorization failure can be retried with a fresh WeChat login', async () => {
  const bundle = loadBundle()
  const first = bundle.instance.onWechatLogin(phoneEvent)
  await tick()
  bundle.requests[0].fail({ errMsg: 'request:fail timeout' })
  await first
  const second = bundle.instance.onWechatLogin(phoneEvent)
  await tick()
  assert.equal(bundle.wechatLogins.length, 2)
  assert.equal(bundle.instance.wechatError, '')
  respond(bundle.requests[1], { detail: '微信服务未配置' }, 503)
  await second
})

test('late authorization responses after unload cannot save tokens or navigate', async () => {
  for (const stage of [0, 1, 2]) {
    const bundle = loadBundle()
    const pending = bundle.instance.onWechatLogin(phoneEvent)
    await tick()
    if (stage > 0) {
      respond(bundle.requests[0], { session_id: 'wx-session' })
      await tick()
    }
    if (stage > 1) {
      respond(bundle.requests[1], { session_id: 'wx-session' })
      await tick()
    }
    bundle.page.onUnload.call(bundle.instance)
    respond(bundle.requests[stage], stage === 2 ? { access_token: 'late-token', employee } : { session_id: 'wx-session' })
    await pending
    assert.equal(bundle.token, '')
    assert.deepEqual(bundle.jumps, [])
  }
})
