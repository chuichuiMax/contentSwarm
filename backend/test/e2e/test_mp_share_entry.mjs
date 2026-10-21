// Execute App lifecycle from source and compiled output with an unsettled native page stack.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import vm from 'node:vm'
import test from 'node:test'

const root = process.env.MP_SOURCE_DIR || '/review'
const bundle = process.env.MP_WEIXIN_BUNDLE_DIR

function app(compiled, pages, allowed = async () => true) {
  const jumps = []
  let config
  let shareApi
  const uni = { reLaunch: ({ url }) => jumps.push(url), getStorageSync: () => null, showToast() {}, makePhoneCall() {} }
  const context = { getCurrentPages: () => pages, uni, requireInternalAccess: allowed,
    resolveAllowedShowUrl: () => '/pages/generate/generate', getActiveGeneration: () => null,
    errorMessage: (error) => error.message, exports: {} }
  context.safeReLaunch = url => uni.reLaunch({ url })
  if (compiled) {
    const vendor = { index: uni, createSSRApp: (value) => { config = value; return { mount() {} } } }
    const shareModule = { exports: {} }
    vm.runInNewContext(readFileSync(resolve(bundle, 'utils/share-entry.js'), 'utf8'), {
      module: shareModule, exports: shareModule.exports, require: () => vendor, getCurrentPages: () => pages
    })
    shareApi = shareModule.exports
    context.require = (id) => ({
      './common/vendor.js': vendor,
      './utils/internal-access.js': { requireInternalAccess: allowed },
      './utils/request.js': { errorMessage: context.errorMessage },
      './utils/safe-navigation.js': { safeReLaunch: context.safeReLaunch },
      './utils/active-generation.js': context,
      './utils/share-entry.js': shareModule.exports
    })[id]
    vm.runInNewContext(readFileSync(resolve(bundle, 'app.js'), 'utf8'), context)
  } else {
    vm.runInNewContext(readFileSync(resolve(root, 'utils/share-entry.mjs'), 'utf8').replace(/export /g, ''), context)
    shareApi = context
    const script = readFileSync(resolve(root, 'App.vue'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1]
      .replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '').replace('export default', 'globalThis.app =')
    vm.runInNewContext(script, context)
    config = context.app
  }
  const instance = config.data()
  return { jumps, show: (options) => config.onShow.call(instance, options), hideForCall: () => {
    shareApi.openCaseSystemPage('makePhoneCall', {}, pages.at(-1))
    shareApi.markCaseSystemHide()
  } }
}

for (const compiled of bundle ? [false, true] : [false]) {
  const label = compiled ? 'compiled' : 'source'
  test(`${label}: native phone return cannot replay retained booking parameters`, async () => {
    const instance = app(compiled, [{ route: 'pages/materials/shared-case', options: { entryCode: 'a'.repeat(24) } }])
    instance.hideForCall()
    await instance.show({ scene: 1038, query: { entryCode: 'a'.repeat(24), booking: '1' } })
    assert.deepEqual(instance.jumps, [])
  })
  for (const [scene, query] of [[1047, { scene: 'c'.repeat(24) }], [1065, { entryCode: 'd'.repeat(24), booking: '1' }],
    [1167, { entryCode: 'd'.repeat(24), booking: '0' }], [1194, { entryCode: 'd'.repeat(24), booking: '0' }]]) {
    test(`${label}: QR and web booking scene ${scene} bypass employee auth and keep native routing`, async () => {
      const instance = app(compiled, [], async () => { throw Error('must remain anonymous') })
      await instance.show({ scene, path: 'pages/materials/shared-case', query })
      assert.deepEqual(instance.jumps, [])
    })
  }
  test(`${label}: browsing URL Link preserves booking=0 when native routing needs a relaunch`, async () => {
    const instance = app(compiled, [], async () => { throw Error('public case must stay anonymous') })
    await instance.show({ scene: 1167, query: { entryCode: 'a'.repeat(24), booking: '0' } })
    assert.deepEqual(instance.jumps, ['/pages/materials/shared-case?entryCode=' + 'a'.repeat(24) + '&booking=0'])
  })
  for (const query of ['scene=' + 'c'.repeat(24), 'entryCode=' + 'd'.repeat(24) + '&booking=1']) {
    test(`${label}: development preview string query bypasses employee auth`, async () => {
      const instance = app(compiled, [], async () => { throw Error('preview case must remain anonymous') })
      await instance.show({ scene: 1047, path: 'pages/materials/shared-case', query })
      assert.deepEqual(instance.jumps, [])
    })
  }
  test(`${label}: query-only booking entry opens its case and invalidates older employee checks`, async () => {
    let complete
    const instance = app(compiled, [], () => new Promise(resolve => { complete = resolve }))
    const old = instance.show({ scene: 1089, query: {} })
    await instance.show({ scene: 1065, query: { entryCode: 'a'.repeat(24), booking: '1' } })
    complete(false)
    await old
    assert.deepEqual(instance.jumps, ['/pages/materials/shared-case?entryCode=' + 'a'.repeat(24) + '&booking=1'])
  })
  for (const scene of [1007, 1008, 1044]) {
    for (const [state, pages] of [
      ['empty', []],
      ['previous page', [{ route: 'pages/materials/materials', options: {} }]],
      ['target options pending', [{ route: 'pages/materials/shared-case', options: {} }]]
    ]) {
      test(`${label}: native share scene ${scene} with ${state} never replaces the native route`, async () => {
        const instance = app(compiled, pages, async () => { throw Error('public share must not validate employees') })
        const options = { scene, path: '/pages/materials/shared-case', query: { shareId: 'case-B' } }
        await instance.show(options)
        await instance.show(options) // Native route has still not committed its page stack.
        assert.deepEqual(instance.jumps, [])
      })
    }
  }
  test(`${label}: a query-only share entry still opens its case`, async () => {
    const instance = app(compiled, [{ route: 'pages/materials/materials', options: {} }])
    await instance.show({ scene: 1007, query: { shareId: 'case-B' } })
    assert.deepEqual(instance.jumps, ['/pages/materials/shared-case?shareId=case-B'])
  })
  test(`${label}: ordinary icon entry ignores a retained case path`, async () => {
    const instance = app(compiled, [])
    await instance.show({ scene: 1001, path: 'pages/materials/shared-case', query: { shareId: 'case-B' } })
    assert.deepEqual(instance.jumps, ['/pages/generate/generate'])
  })
  test(`${label}: reviewing a customer source preserves the lead detail stack on background return`, async () => {
    const instance = app(compiled, [
      { route: 'pages/mine/leads', options: {} },
      { route: 'pages/mine/lead-detail', options: { id: 'lead-a' } },
      { route: 'pages/materials/shared-case', options: { shareId: 'case-a' } }
    ])
    await instance.show({ scene: 1001, query: {} })
    assert.deepEqual(instance.jumps, [])
  })
  test(`${label}: an older identity result cannot redirect a native share entry`, async () => {
    let finish
    const instance = app(compiled, [{ route: 'pages/materials/materials', options: {} }],
      () => new Promise((resolve) => { finish = resolve }))
    const old = instance.show({ scene: 1089, query: {} })
    await instance.show({ scene: 1007, path: 'pages/materials/shared-case', query: { shareId: 'case-B' } })
    finish(true)
    await old
    assert.deepEqual(instance.jumps, [])
  })
}
