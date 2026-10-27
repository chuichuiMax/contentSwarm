import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const sourceRoot = process.env.MP_SOURCE_DIR
assert.ok(sourceRoot, 'Set MP_SOURCE_DIR to the mini-program source directory')
const logic = await import(pathToFileURL(resolve(sourceRoot, 'utils/image-design-logic.mjs')))
const source = readFileSync(resolve(sourceRoot, 'pages/cover/cover.vue'), 'utf8')
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/^import[\s\S]*?from '[^']+'\r?$/gm, '')
  .replace('export default', 'const page =')
const oldPhoto = { id: 'old-gallery', thumbnail_file_url: '/old-thumbnail', source_role: 'source' }
const newPhoto = { id: 'new-upload', thumbnail_file_url: '/new-thumbnail', source_role: 'upload' }
const bundleRoot = process.env.MP_WEIXIN_BUNDLE_DIR

function loadCompiledPage(globals) {
  const modules = new Map()
  let page
  const vendor = {
    index: globals.uni,
    _export_sfc: (component, props) => Object.assign(component, Object.fromEntries(props)),
    e: (...objects) => Object.assign({}, ...objects), t: String, n: value => value,
    f: (items, callback) => items.map(callback), p: props => props, o: handler => handler
  }
  function load(path) {
    const absolute = resolve(bundleRoot, path)
    if (absolute === resolve(bundleRoot, 'common/vendor.js')) return vendor
    if (absolute === resolve(bundleRoot, 'apis/mp.js')) return { mpImageDesignApi: globals.mpImageDesignApi }
    if (absolute === resolve(bundleRoot, 'utils/request.js')) return globals
    if (absolute === resolve(bundleRoot, 'utils/internal-access.js')) return { internalPageMixin: {} }
    if (modules.has(absolute)) return modules.get(absolute).exports
    const module = { exports: {} }
    modules.set(absolute, module)
    runInNewContext(readFileSync(absolute, 'utf8'), {
      module, exports: module.exports, setTimeout, clearTimeout,
      require: id => load(resolve(dirname(absolute), id)),
      wx: { createPage: component => { page = component } }
    }, { filename: absolute })
    return module.exports
  }
  load('pages/cover/cover.js')
  return page
}

function createPage({ drafts = {}, cached = null, api = {} } = {}) {
  let choice
  const calls = { drafts: 0, uploads: 0, tasks: 0, saves: 0 }
  const toasts = []
  const globals = {
    ...logic, toggleTransferElementValue: logic.toggleTransferElement,
    TabBar: {}, ImageSourceSelector: {}, SaveTargetSheet: {}, internalPageMixin: {},
    mediaUrl: value => value,
    galleryThumbUrl: item => item?.thumbnail_file_url || item?.file_url || '',
    errorMessage: error => error.message,
    setTimeout, clearTimeout,
    mpImageDesignApi: {
      drafts: async () => { calls.drafts++; return { drafts } },
      saveDrafts: async () => { calls.saves++ },
      addLibraryItem: async () => ({ item: oldPhoto }),
      uploadInput: async () => { calls.uploads++; return { item: newPhoto } },
      createTask: async () => { calls.tasks++ },
      ...api
    },
    uni: {
      chooseImage: options => { choice = options },
      getStorageSync: () => cached ? JSON.stringify(cached) : '',
      setStorageSync() {}, showLoading() {}, hideLoading() {},
      showToast: ({ title }) => toasts.push(title)
    }
  }
  const page = bundleRoot ? loadCompiledPage(globals) : runInNewContext(`${script}\npage`, globals)
  const vm = page.data()
  for (const [name, method] of Object.entries(page.methods)) vm[name] = method.bind(vm)
  for (const [name, getter] of Object.entries(page.computed)) Object.defineProperty(vm, name, { get: getter.bind(vm) })
  vm.ensureInternalAccess = async () => true
  for (const name of ['loadSourceFolders', 'loadSaveTargets', 'loadTransferProfiles', 'loadDesignLibrary', 'loadResults', 'assertUploadableImage']) vm[name] = async () => {}
  vm.scheduleDraftSave = () => {}
  vm.startTaskPolling = () => {}
  vm.stopTaskPolling = () => {}
  return {
    vm, calls, toasts,
    show: () => page.onShow.call(vm),
    hide: () => page.onHide.call(vm),
    render: () => page.render(vm, [], {}, {}, vm, vm),
    get choice() { return choice }
  }
}

test('fresh entry clears the restored original photo and old polish, preserving user settings', async () => {
  const settings = { style: '现代轻奢', description: '保留描述', ratio: 'square', count: 4, quality: '2k', save_target: { scope: 'private', gallery_id: 'save' } }
  const page = createPage({ drafts: { redesign: { ...settings, source: oldPhoto, polished_prompt: 'old prompt', refinement_id: 'old-refinement' } } })
  await page.show()
  assert.equal(page.vm.slotImage('source'), null)
  for (const key of Object.keys(settings)) assert.equal(JSON.stringify(page.vm.activeDraft[key]), JSON.stringify(settings[key]))
  assert.equal(page.vm.activeDraft.refinement_id, '')
})

test('offline fresh entry clears the cached photo and keeps cached parameters', async () => {
  const page = createPage({ cached: { redesign: { source: oldPhoto, style: '新中式' } }, api: { drafts: async () => { throw new Error('offline') } } })
  await page.show()
  assert.equal(page.vm.slotImage('source'), null)
  assert.equal(page.vm.activeDraft.style, '新中式')
  assert.equal(page.vm.draftSyncIssue, true)
})

test('empty restored photo metadata does not produce a selected card', async () => {
  const page = createPage({ drafts: { redesign: { source: {} } } })
  await page.show()
  assert.equal(page.vm.slotImage('source'), null)
})

test('gallery then upload survives native-picker return and submits the new image id', async () => {
  const page = createPage({ drafts: { redesign: { source: oldPhoto, style: '现代轻奢' } } })
  await page.show()
  page.vm.pickerSlot = 'source'
  page.vm.pickerSourceRole = 'source'
  page.vm.selectedFolderItems = [{ id: 'material-old' }]
  await page.vm.confirmPicker()
  assert.equal(page.vm.slotImage('source').id, oldPhoto.id)
  page.vm.chooseUpload('source')
  page.hide()
  await page.choice.success({ tempFilePaths: ['wxfile://new-photo'] })
  await page.show()
  assert.equal(page.vm.slotImage('source').id, newPhoto.id)
  assert.equal(page.vm.imageUrl(page.vm.slotImage('source')), newPhoto.thumbnail_file_url)
  assert.equal(logic.buildImageDesignPayload('redesign', page.vm.activeDraft).images[0].library_item_id, newPhoto.id)
  assert.equal(page.vm.activeDraft.style, '现代轻奢')
  assert.equal(page.calls.drafts, 1)
  assert.equal(page.calls.tasks, 0)
})

test('upload then gallery also replaces the current photo', async () => {
  const page = createPage()
  await page.show()
  page.vm.chooseUpload('source')
  await page.choice.success({ tempFilePaths: ['wxfile://new-photo'] })
  page.vm.pickerSlot = 'source'
  page.vm.pickerSourceRole = 'source'
  page.vm.selectedFolderItems = [{ id: 'material-old' }]
  await page.vm.confirmPicker()
  assert.equal(page.vm.slotImage('source').id, oldPhoto.id)
  assert.equal(page.calls.tasks, 0)
})

test('cancelled selection and returning from preview preserve the whole current draft', async () => {
  const page = createPage()
  await page.show()
  page.vm.setImageSlot('source', oldPhoto)
  page.vm.activeDraft.style = '新中式'
  page.vm.activeDraft.description_keywords = ['自定义关键词']
  const before = JSON.stringify(page.vm.drafts)
  page.vm.chooseUpload('source')
  page.hide()
  page.choice.fail?.({ errMsg: 'chooseImage:fail cancel' })
  await page.show()
  assert.equal(JSON.stringify(page.vm.drafts), before)
  assert.equal(page.calls.uploads, 0)
  assert.equal(page.calls.tasks, 0)
})

test('upload failure preserves the original photo and parameters', async () => {
  const page = createPage({ api: { uploadInput: async () => { throw new Error('upload failed') } } })
  await page.show()
  page.vm.setImageSlot('source', oldPhoto)
  const before = JSON.stringify(page.vm.drafts)
  page.vm.chooseUpload('source')
  await page.choice.success({ tempFilePaths: ['wxfile://new-photo'] })
  await page.show()
  assert.equal(JSON.stringify(page.vm.drafts), before)
  assert.equal(page.toasts.at(-1), 'upload failed')
})

test('initialization is shared across shows and cannot accept or persist photos before drafts arrive', async () => {
  let finish
  let requests = 0
  const page = createPage({ api: { drafts: () => { requests++; return new Promise(resolve => { finish = resolve }) } } })
  const first = page.show()
  await new Promise(resolve => setImmediate(resolve))
  const second = page.show()
  await new Promise(resolve => setImmediate(resolve))
  page.vm.chooseUpload('source')
  page.hide()
  assert.equal(page.choice, undefined)
  assert.equal(page.calls.saves, 0)
  assert.equal(requests, 1)
  finish({ drafts: { redesign: { source: oldPhoto, style: '新中式' } } })
  await Promise.all([first, second])
  assert.equal(page.vm.slotImage('source'), null)
  assert.equal(page.vm.activeDraft.style, '新中式')
})

test('a pending upload stays bound to its original workflow when the user switches workflows', async () => {
  let finish
  const page = createPage({ api: { uploadInput: () => new Promise(resolve => { finish = resolve }) } })
  await page.show()
  page.vm.chooseUpload('source')
  const uploading = page.choice.success({ tempFilePaths: ['wxfile://new-photo'] })
  await new Promise(resolve => setImmediate(resolve))
  page.vm.selectWorkflow('adapt')
  finish({ item: newPhoto })
  await uploading
  assert.equal(page.vm.drafts.redesign.source.id, newPhoto.id)
  assert.equal(page.vm.drafts.adapt.source, null)
})

test('the original-photo change action invokes native selection without bubbling to preview', () => {
  const card = source.split(/\r?\n/).find(line => line.includes('已选择原房图'))
  assert.match(card, /@click\.stop="chooseUpload\('source'\)"/)
})

if (bundleRoot) {
  test('compiled change event opens the native picker and replaces the submitted photo', async () => {
    const page = createPage()
    await page.show()
    page.vm.internalAccessGranted = true
    page.vm.setImageSlot('source', oldPhoto)
    const wxml = readFileSync(resolve(bundleRoot, 'pages/cover/cover.wxml'), 'utf8')
    const event = wxml.match(/catchtap="\{\{(\w+)\}\}">更换/)
    assert.ok(event, 'The compiled original-photo change action must stop propagation')
    page.render()[event[1]]({})
    assert.equal(page.choice.count, 1)
    await page.choice.success({ tempFilePaths: ['wxfile://new-photo'] })
    await page.show()
    assert.equal(page.vm.slotImage('source').id, newPhoto.id)
    assert.equal(page.calls.tasks, 0)
  })
}
