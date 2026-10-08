import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'
import { runInNewContext } from 'node:vm'

assert.ok(process.env.SHARE_MINIAPP_ROOT, 'Set SHARE_MINIAPP_ROOT to the mini-program source directory')
const page = readFileSync(resolve(process.env.SHARE_MINIAPP_ROOT, 'pages/materials/shared-case.vue'), 'utf8')
const script = page.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/^import .*$/gm, '')
  .replace('export default', 'globalThis.component =')

function loadComponent(share) {
  const calls = []
  const context = {
    mpContentApi: { getShare: async () => ({ share }) },
    buildSharedCaseImages: () => [],
    formatArea: (area) => `${area}㎡`,
    publicMediaUrl: (url) => url,
    saveLastShareId: () => {},
    openCaseSystemPage: (method, options) => context.uni[method](options),
    errorMessage: (error) => error.message,
    uni: {
      setNavigationBarTitle: () => {},
      showToast: () => assert.fail('Share loading failed'),
      makePhoneCall: ({ phoneNumber }) => calls.push(phoneNumber)
    }
  }
  runInNewContext(script, context)
  const component = context.component
  const instance = { ...component.data(), shareId: 'original-share-token' }
  for (const [name, method] of Object.entries(component.methods)) instance[name] = method.bind(instance)
  return { component, instance, calls }
}

test('mini-program loads the original sharer contact and dials that number', async () => {
  const { component, instance, calls } = loadComponent({
    gallery_name: '测试案例', building_name: '洋湖天序', area: '120', design_style: '复古写意',
    sharer_name: '测试分享员工', sharer_phone: '19900000001'
  })

  await instance.loadShare()
  instance.callSharer()

  assert.equal(instance.caseInfo.sharerName, '测试分享员工')
  assert.equal(instance.caseInfo.sharerPhone, '19900000001')
  assert.deepEqual(calls, ['19900000001'])
  const style = component.computed.metaItems.call(instance).find((item) => item.label === '风格')
  assert.equal(style.wide, false)
  assert.equal(component.onShareAppMessage.call(instance).path,
    '/pages/materials/shared-case?shareId=original-share-token')
  assert.match(page, /v-if="caseInfo\.sharerPhone"[^>]*class="project-field share-phone"[^>]*@click="callSharer"/)
  assert.match(page, /v-if="metaItems\.length \|\| caseInfo\.sharerPhone" class="project-card"/)
  assert.match(page, /\.share-phone\s*\{\s*color:\s*#000;/)
})

test('legacy mini-program share keeps style full width with no contact', async () => {
  const { component, instance } = loadComponent({
    gallery_name: '旧案例', building_name: '洋湖天序', area: '120', design_style: '复古写意'
  })

  await instance.loadShare()

  assert.equal(instance.caseInfo.sharerName, '')
  assert.equal(instance.caseInfo.sharerPhone, '')
  assert.equal(component.computed.metaItems.call(instance).find((item) => item.label === '风格').wide, true)
})
