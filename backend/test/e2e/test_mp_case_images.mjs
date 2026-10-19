import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import vm from 'node:vm'
import test from 'node:test'

const root = process.env.MP_SOURCE_DIR || '/review'
const source = readFileSync(resolve(root, 'components/case-share-content.vue'), 'utf8')
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/^import .*$/gm, '').replace('export default', 'const component =')
const context = { wx: { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }) } }
const component = vm.runInNewContext(`${script}\ncomponent`, context)

test('a pending case image reserves layout while the first image starts loading immediately', () => {
  assert.match(source, /:lazy-load="index > 0"/)
  assert.match(source, /'image-pending': !imageStates\[image\.displayUrl\]/)
  assert.match(source, /\.case-image\.image-pending\s*\{\s*min-height:\s*220px/)
  assert.match(source, /mode="widthFix"/)
  assert.match(source, /@load="imageLoaded\(image\.displayUrl\)"/)
  assert.match(source, /@error="imageFailed\(image\.displayUrl, \$event\)"/)
  assert.doesNotMatch(source, /class="case-image"[^>]*\slazy-load(?:\s|>)/)
})

test('image errors stay visible and retry only resets the failed image', () => {
  const state = component.data()
  component.methods.imageLoaded.call(state, '/one.webp')
  component.methods.imageFailed.call(state, '/two.webp')
  assert.equal(state.imageStates['/one.webp'], 'loaded')
  assert.equal(state.imageStates['/two.webp'], 'error')
  component.methods.retryImage.call(state, '/two.webp')
  assert.equal(state.imageStates['/one.webp'], 'loaded')
  assert.equal(state.imageStates['/two.webp'], undefined)
  component.methods.imageLoaded.call(state, '/two.webp')
  assert.equal(state.imageStates['/two.webp'], 'loaded')
  assert.match(source, /案例图片加载失败/)
  assert.match(source, /@click="retryImage\(image\.displayUrl\)"/)
})

test('image state updates keep original preview order and contact actions', () => {
  const events = []
  const state = { ...component.data(), $emit: (...args) => events.push(args) }
  component.methods.imageFailed.call(state, '/two.webp')
  component.methods.previewImage.call(state, 1)
  component.methods.callSharer.call(state)
  assert.deepEqual(events, [['preview', 1], ['call']])
})

test('development preview retains the native image error while release hides diagnostic details', () => {
  const state = component.data()
  component.methods.imageFailed.call(state, '/one.webp', { detail: { errMsg: 'download image fail: network error' } })
  assert.equal(state.imageErrors['/one.webp'], 'download image fail: network error')
  assert.equal(component.computed.showImageDiagnostics(), true)
  context.wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'release' } })
  assert.equal(component.computed.showImageDiagnostics(), false)
  context.wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'trial' } })
  assert.equal(component.computed.showImageDiagnostics(), false)
})
