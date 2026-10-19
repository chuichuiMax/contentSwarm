import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const root = process.env.MP_SOURCE_DIR || '/review'
const selections = await import(pathToFileURL(resolve(root, 'utils/mine-library-logic.mjs')))
const enterprise = await import(pathToFileURL(resolve(root, 'utils/enterprise-materials.mjs')))
const gallery = { id: 'shared-real-id', name: '毛坯房图库', count: 65, can_upload: true,
  visibility: 'enterprise', image_design_role: 'rough' }

function mount(name, api = {}, host = {}) {
  const routes = [], previews = [], timers = new Map()
  const uni = { setNavigationBarTitle() {}, getSystemInfoSync: () => ({}), showToast() {},
    navigateTo: value => routes.push(value.url), previewImage: value => previews.push(value), ...host }
  const script = readFileSync(resolve(root, `pages/mine/${name}.vue`), 'utf8')
    .match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '').replace('export default', 'const component =')
  const component = runInNewContext(`${script}\ncomponent`, {
    ...selections, ...enterprise, TabBar: {}, internalPageMixin: {}, uni,
    mpContentApi: { enterpriseRoughFolders: async () => ({ galleries: [gallery] }), ...api },
    mpMeApi: { get: async () => ({ user_uid: 'alice' }) },
    errorMessage: error => error.message, mediaUrl: path => path ? `media:${path}` : '',
    galleryThumbUrl: item => item.thumbnail_file_url, galleryCoverPath: () => '',
    setTimeout: fn => { const id = Symbol(); timers.set(id, fn); return id },
    clearTimeout: id => timers.delete(id)
  })
  const page = { ...component.data(), ensureInternalAccess: async () => true }
  for (const [key, method] of Object.entries(component.methods)) page[key] = method.bind(page)
  for (const [key, getter] of Object.entries(component.computed || {})) Object.defineProperty(page, key, { get: getter.bind(page) })
  return { page, component, routes, previews, timers }
}

test('enterprise entry uses the dedicated page and passes the actual gallery to the folder page', async () => {
  const mine = readFileSync(resolve(root, 'pages/mine/mine.vue'), 'utf8')
  assert.ok(mine.indexOf('我的素材') < mine.indexOf('企业共享'))
  assert.match(mine, /\/pages\/mine\/enterprise/)
  const state = mount('enterprise')
  await state.page.loadGalleries()
  assert.deepEqual(JSON.parse(JSON.stringify(state.page.galleries)), [gallery])
  state.page.openGallery(gallery)
  assert.match(state.routes[0], /\/pages\/mine\/folder\?scope=enterprise&category=shared-real-id/)
})

test('enterprise folder requests only shared images and carries date filters into pagination', async () => {
  const calls = []
  const state = mount('folder', {
    galleryItems: async (...args) => { calls.push(args); return { items: [{ id: 'photo' }], total: 65 } },
    myMaterialItems: () => assert.fail('enterprise must not read private materials')
  })
  state.page.scope = 'enterprise'; state.page.categoryId = gallery.id
  state.page.dateFrom = '2026-10-01'; state.page.dateTo = '2026-10-15'
  await state.page.loadGalleryMeta()
  await state.page.loadItems(true)
  await state.page.loadItems(false)
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), [gallery.id, 'enterprise', {
    page: 2, page_size: 30, date_from: '2026-10-01', date_to: '2026-10-15'
  }])
})

test('all shared images preview while only the uploader can select for deletion', () => {
  const state = mount('folder')
  state.page.scope = 'enterprise'; state.page.currentUid = 'alice'
  const other = { id: 'other', uploaded_by: 'bob', can_manage: true, file_url: '/bob' }
  const mine = { id: 'mine', uploaded_by: 'alice', file_url: '/alice' }
  state.page.items = [other, mine]
  state.page.toggleItem(other)
  assert.deepEqual(JSON.parse(JSON.stringify(state.previews)), [{ current: 'media:/bob', urls: ['media:/bob', 'media:/alice'] }])
  state.page.editing = true
  state.page.toggleItem(other)
  assert.equal(state.page.selectedIds.length, 0)
  state.page.toggleItem(mine)
  assert.deepEqual(Array.from(state.page.selectedIds), ['mine'])
})

test('enterprise upload passes its actual shared category and explicit enterprise scope', async () => {
  const calls = []
  let task
  const state = mount('folder', { uploadCover: async (...args) => calls.push(args) }, {
    chooseImage: ({ success }) => { task = success({ tempFilePaths: ['new.png'] }) }
  })
  state.page.scope = 'enterprise'; state.page.categoryId = gallery.id; state.page.galleryMeta = gallery
  state.page.loadItems = async () => {}
  state.page.chooseImages()
  await task
  assert.deepEqual(calls, [['new.png', gallery.id, 'rough', 'enterprise']])
})

test('pending uploads and failures are visible and their polling stops when the folder hides', async () => {
  const state = mount('folder', { galleryItems: async () => ({ items: [], total: 0,
    pending_upload_count: 2, upload_failures: [{ id: 'failed' }] }) })
  state.page.scope = 'enterprise'; state.page.categoryId = gallery.id; state.page.pageVisible = true
  await state.page.loadItems(true)
  assert.equal(state.page.pendingUploadCount, 2)
  assert.equal(state.page.uploadFailures.length, 1)
  assert.equal(state.timers.size, 1)
  state.component.onHide.call(state.page)
  assert.equal(state.timers.size, 0)
  assert.equal(state.page.pageVisible, false)
})

test('failed enterprise navigation stays an error and does not become an empty library', async () => {
  const state = mount('enterprise', { enterpriseRoughFolders: async () => { throw Error('offline') } })
  await state.page.loadGalleries()
  assert.equal(state.page.loadError, '图库加载失败：offline')
  assert.equal(state.page.loading, false)
  assert.equal(state.page.galleries.length, 0)
})
