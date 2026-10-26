// Render the actual Vue page with native-element defaults and fixture APIs.
// WeChat keyboard behavior and native rendering still require device acceptance.
import assert from 'node:assert/strict'
import { readFileSync, mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import test from 'node:test'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const { parse, compileTemplate } = require('@vue/compiler-sfc')
const root = process.env.MP_SOURCE_DIR
assert.ok(root, 'Set MP_SOURCE_DIR to the mini-program source directory')
const output = process.env.MP_SEARCH_QA_DIR
if (output) mkdirSync(output, { recursive: true })

const source = readFileSync(join(root, 'pages/materials/materials.vue'), 'utf8')
const searchSource = readFileSync(join(root, 'components/case-building-search.vue'), 'utf8')
const stripImports = script => script.replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '')
const descriptors = [source, searchSource].map((content, index) => {
  const { descriptor, errors } = parse(content)
  assert.deepEqual(errors, [])
  const compiled = compileTemplate({ source: descriptor.template.content, filename: `case-search-${index}.vue`, id: `case-search-${index}` })
  assert.deepEqual(compiled.errors, [])
  return descriptor
})
const [materials, search] = descriptors
const inputs = {
  template: materials.template.content,
  script: stripImports(materials.script.content).replace('export default', 'return'),
  searchTemplate: search.template.content,
  searchScript: search.script.content.replace('export default', 'return'),
  materialsLogic: readFileSync(join(root, 'utils/materials-logic.mjs'), 'utf8').replace(/export /g, ''),
  caseLogic: stripImports(readFileSync(join(root, 'utils/case-gallery.mjs'), 'utf8')).replace(/export /g, '')
}
const vueRuntime = readFileSync(require.resolve('vue/dist/vue.global.js'), 'utf8')

for (const [width, height, inset] of [[375, 812, 34], [320, 568, 0], [375, 480, 0]]) {
  test(`case search input, style, return and list layout at ${width}x${height}`, async () => {
    const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL })
    try {
      const page = await browser.newPage({ viewport: { width, height } })
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      const css = materials.styles[0].content.replaceAll('env(safe-area-inset-bottom)', `${inset}px`)
      await page.setContent(`<style>body{margin:0}view,scroll-view,text{display:block}scroll-view{overflow-y:auto}input{border:0;outline:0}button{padding:0}.qa-tab{position:fixed;bottom:0;left:0;right:0;height:${52 + inset}px;background:white}</style><style>${css}\n${search.styles[0].content}</style><div id="app"></div>`)
      await page.addScriptTag({ content: vueRuntime })
      await page.evaluate((input) => {
        // The uni-app native input dispatches detail.value; adapt browser inputs to that contract.
        document.addEventListener('input', event => {
          if (event.target.tagName === 'INPUT') {
            Object.defineProperty(event, 'detail', { value: { value: event.target.value } })
          }
        }, true)
        const logic = new Function(`${input.materialsLogic}; return {MATERIAL_LIBRARY_STYLE_OPTIONS, createSelectionState, galleryStyle, galleryCoverPath, groupGalleryItemsIntoRows}`)()
        const filterCaseGalleryChildren = new Function('galleryStyle', `${input.caseLogic}; return filterCaseGalleryChildren`)(logic.galleryStyle)
        const CaseBuildingSearch = new Function(input.searchScript)()
        CaseBuildingSearch.template = input.searchTemplate
        const bindings = {
          ...logic, filterCaseGalleryChildren, CaseBuildingSearch,
          TabBar: { template: '<div class="qa-tab">案例</div>' },
          internalPageMixin: { data: () => ({ internalAccessGranted: true }) },
          mpContentApi: { galleryItems: async () => ({ items: [] }) },
          uni: { hideShareMenu() {} },
          mediaUrl: value => value || ''
        }
        const options = new Function(...Object.keys(bindings), input.script)(...Object.values(bindings))
        options.template = input.template
        const app = Vue.createApp(options)
        app.config.compilerOptions.isCustomElement = tag => ['view', 'scroll-view', 'text', 'image'].includes(tag)
        window.caseVm = app.mount('#app')
        caseVm.galleries = [
          { id: 'root' }, { id: 'legacy-root', design_style: '雅致现代' },
          { id: 'a', parent_id: 'root', building_name: '万科松湖天地', name: '万科松湖天地', design_style: '雅致现代' },
          { id: 'legacy', parent_id: 'legacy-root', name: '万科森林公园-189㎡' },
          { id: 'b', parent_id: 'root', building_name: '万科森林公园', name: '万科森林公园-写意木构', design_style: '写意木构' },
          ...Array.from({ length: 30 }, (_, index) => ({ id: `other-${index}`, parent_id: 'root', name: `其他楼盘${index}`, design_style: '雅致现代' }))
        ]
      }, inputs)
      const input = page.getByPlaceholder('搜索楼盘地址')
      await input.fill('  万科  ')
      await page.evaluate(() => Vue.nextTick())
      assert.equal(await page.locator('.gallery-card').count(), 3)
      await page.locator('.style-option').filter({ hasText: '雅致现代' }).click()
      await page.evaluate(() => Vue.nextTick())
      assert.equal(await page.evaluate(() => caseVm.selectedStyle), '雅致现代')
      assert.equal(await page.locator('.gallery-card').count(), 2)
      await page.locator('.gallery-card').first().click()
      await page.locator('.detail-view').waitFor()
      await page.locator('.back').click()
      assert.equal(await input.inputValue(), '  万科  ')
      assert.equal(await page.locator('.gallery-card').count(), 2)
      await input.fill('不存在的楼盘')
      await page.getByText('未找到匹配的案例', { exact: true }).waitFor()
      await page.locator('.clear').click()
      assert.equal(await input.inputValue(), '')
      assert.equal(await page.locator('.gallery-card').count(), 32)
      const searchBefore = await page.locator('.search-wrap').boundingBox()
      await page.locator('.gallery-content').evaluate(element => { element.scrollTop = element.scrollHeight })
      const searchAfter = await page.locator('.search-wrap').boundingBox()
      assert.equal(searchAfter.y, searchBefore.y)
      const last = await page.locator('.gallery-card').last().boundingBox()
      const tab = await page.locator('.qa-tab').boundingBox()
      assert.ok(last.y >= searchAfter.y + searchAfter.height && last.y + last.height <= tab.y + 1)
      assert.ok(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1))
      assert.deepEqual(errors, [])
      if (output) await page.screenshot({ path: join(output, `case-search-${width}x${height}.png`) })
    } finally {
      await browser.close()
    }
  })
}
