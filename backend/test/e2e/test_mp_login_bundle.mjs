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
  let token = initialToken
  let page
  const uni = {
    $emit: () => {},
    $on: () => {},
    getSystemInfoSync: () => ({ platform: 'devtools' }),
    getStorageSync: () => token,
    setStorageSync: (key, value) => { token = value },
    removeStorageSync: () => { token = '' },
    login: (options) => options.success({ code: 'test-js-code' }),
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
  return { access, instance, requests, modals, jumps, get token() { return token } }
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
