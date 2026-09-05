<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { CheckCircle2, FolderOpen, Image as ImageIcon, Search } from 'lucide-vue-next'

import { materialLibraryApi } from '@/apis/material_library_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  selectedItemId: { type: String, default: '' },
  selectedGalleryId: { type: String, default: '' },
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['update:open', 'select'])

const router = useRouter()
const galleries = ref([])
const activeGalleryId = ref('')
const items = ref([])
const itemUrls = ref({})
const loadingGalleries = ref(false)
const loadingItems = ref(false)
const queryInput = ref('')
const query = ref('')
const page = ref(1)
const pageSize = 24
const total = ref(0)
const pendingItem = ref(null)
let loadVersion = 0

const activeGallery = computed(() => galleries.value.find((item) => item.id === activeGalleryId.value))
const rootGalleries = computed(() => galleries.value.filter((item) => !item.parent_id))
const orderedGalleries = computed(() => {
  const roots = rootGalleries.value
  return roots.flatMap((root) => [root, ...galleryChildren(root.id)])
})
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

function galleryChildren(parentId) {
  return galleries.value.filter((item) => item.parent_id === parentId)
}

function releaseItemUrls(urls = itemUrls.value) {
  Object.values(urls).forEach((url) => {
    if (url) URL.revokeObjectURL(url)
  })
}

async function loadItems() {
  const version = ++loadVersion
  releaseItemUrls()
  itemUrls.value = {}
  items.value = []
  if (!activeGalleryId.value) return
  loadingItems.value = true
  try {
    const response = await materialLibraryApi.listItems({
      material_type: 'image',
      category: activeGalleryId.value,
      status: 'enabled',
      query: query.value || null,
      page: page.value,
      page_size: pageSize,
      sort: 'newest'
    })
    if (version !== loadVersion) return
    items.value = response.items || []
    total.value = response.total || 0
    const nextUrls = {}
    await Promise.all(items.value.map(async (item) => {
      try {
        const response = await materialLibraryApi.getItemFile(item.id)
        nextUrls[item.id] = URL.createObjectURL(await response.blob())
      } catch {
        nextUrls[item.id] = ''
      }
    }))
    if (version !== loadVersion) {
      releaseItemUrls(nextUrls)
      return
    }
    itemUrls.value = nextUrls
    const selected = items.value.find((item) => item.id === props.selectedItemId)
    if (selected && !pendingItem.value) pendingItem.value = selected
  } catch (error) {
    if (version === loadVersion) message.error(error.message || '素材库图片加载失败')
  } finally {
    if (version === loadVersion) loadingItems.value = false
  }
}

async function loadGalleries() {
  loadingGalleries.value = true
  try {
    const response = await materialLibraryApi.listGalleries()
    galleries.value = response.galleries || []
    activeGalleryId.value = (
      props.selectedGalleryId
      && galleries.value.some((item) => item.id === props.selectedGalleryId)
        ? props.selectedGalleryId
        : orderedGalleries.value.find((item) => (item.parent_id ? item.count : item.direct_count) > 0)?.id
          || orderedGalleries.value[0]?.id
          || ''
    )
    await loadItems()
  } catch (error) {
    message.error(error.message || '图片素材库加载失败')
  } finally {
    loadingGalleries.value = false
  }
}

async function chooseGallery(galleryId) {
  if (galleryId === activeGalleryId.value) return
  activeGalleryId.value = galleryId
  pendingItem.value = null
  queryInput.value = ''
  query.value = ''
  page.value = 1
  await loadItems()
}

async function search() {
  query.value = queryInput.value.trim()
  page.value = 1
  await loadItems()
}

async function changePage(nextPage) {
  page.value = nextPage
  await loadItems()
}

function close() {
  emit('update:open', false)
}

function confirm() {
  if (!pendingItem.value || props.disabled) return
  emit('select', pendingItem.value)
  close()
}

async function manageMaterials() {
  close()
  await router.push('/materials/images')
}

watch(() => props.open, (open) => {
  if (!open) return
  pendingItem.value = null
  queryInput.value = ''
  query.value = ''
  page.value = 1
  void loadGalleries()
})

onBeforeUnmount(() => {
  loadVersion += 1
  releaseItemUrls()
})
</script>

<template>
  <a-modal
    :open="open"
    title="选择素材库底图"
    width="min(1040px, 94vw)"
    :footer="null"
    :mask-closable="!disabled"
    @cancel="close"
  >
    <div class="material-picker">
      <aside class="gallery-sidebar">
        <div class="sidebar-heading"><strong>图库</strong><span>{{ rootGalleries.length }}</span></div>
        <div v-if="loadingGalleries" class="sidebar-state">正在加载…</div>
        <template v-else>
          <div v-for="gallery in rootGalleries" :key="gallery.id" class="gallery-tree-group">
            <button
              type="button"
              class="gallery-button"
              :class="{ active: activeGalleryId === gallery.id }"
              @click="chooseGallery(gallery.id)"
            >
              <FolderOpen :size="16" />
              <span>{{ gallery.name }}</span>
              <small>{{ gallery.direct_count || 0 }}</small>
            </button>
            <div v-if="galleryChildren(gallery.id).length" class="gallery-tree-children">
              <button
                v-for="child in galleryChildren(gallery.id)"
                :key="child.id"
                type="button"
                class="gallery-button child"
                :class="{ active: activeGalleryId === child.id }"
                @click="chooseGallery(child.id)"
              >
                <FolderOpen :size="14" />
                <span>{{ child.name }}</span>
                <small>{{ child.count || 0 }}</small>
              </button>
            </div>
          </div>
        </template>
      </aside>

      <section class="picker-content">
        <div class="picker-toolbar">
          <div>
            <strong>{{ activeGallery?.name || '素材图片' }}</strong>
            <small>选择一张图片作为完整封面的底图</small>
          </div>
          <label class="picker-search">
            <Search :size="16" />
            <input v-model="queryInput" placeholder="搜索图片名称" @keyup.enter="search" />
            <button type="button" @click="search">搜索</button>
          </label>
        </div>

        <div v-if="loadingItems" class="picker-state">正在加载素材图片…</div>
        <div v-else-if="items.length" class="image-grid">
          <button
            v-for="item in items"
            :key="item.id"
            type="button"
            class="image-card"
            :class="{ selected: pendingItem?.id === item.id }"
            :aria-pressed="pendingItem?.id === item.id"
            @click="pendingItem = item"
          >
            <span class="image-preview">
              <img v-if="itemUrls[item.id]" :src="itemUrls[item.id]" :alt="item.name" />
              <ImageIcon v-else :size="24" />
              <CheckCircle2 v-if="pendingItem?.id === item.id" class="selected-check" :size="21" />
            </span>
            <strong :title="item.name">{{ item.name }}</strong>
            <small>{{ item.width }} × {{ item.height }}</small>
          </button>
        </div>
        <div v-else class="picker-state empty">
          <ImageIcon :size="30" />
          <strong>{{ query ? '没有找到匹配的图片' : '当前图库暂无可用图片' }}</strong>
          <span>{{ query ? '换个关键词继续搜索，或清空搜索条件。' : '可前往素材库上传图片后再选择。' }}</span>
          <a-button v-if="!query" type="link" @click="manageMaterials">前往素材库</a-button>
        </div>

        <div class="picker-footer">
          <div class="pagination">
            <a-button size="small" :disabled="page <= 1 || loadingItems" @click="changePage(page - 1)">上一页</a-button>
            <span>第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 张</span>
            <a-button size="small" :disabled="page >= totalPages || loadingItems" @click="changePage(page + 1)">下一页</a-button>
          </div>
          <div class="footer-actions">
            <a-button @click="close">取消</a-button>
            <a-button type="primary" :disabled="!pendingItem || disabled" @click="confirm">使用这张底图</a-button>
          </div>
        </div>
      </section>
    </div>
  </a-modal>
</template>

<style scoped lang="less">
.material-picker { min-height: 560px; display: grid; grid-template-columns: 190px minmax(0, 1fr); border: 1px solid var(--gray-150); border-radius: 9px; overflow: hidden; background: var(--gray-0); }
.gallery-sidebar { padding: 14px 10px; border-right: 1px solid var(--gray-150); background: var(--gray-25); }
.sidebar-heading { padding: 0 8px 10px; display: flex; align-items: center; justify-content: space-between; color: var(--color-text); }
.sidebar-heading span { color: var(--color-text-tertiary); font-size: 11px; }
.gallery-tree-group { margin-bottom: 4px; }
.gallery-button { width: 100%; padding: 9px 8px; border: 0; border-radius: 7px; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 7px; color: var(--color-text-secondary); background: transparent; text-align: left; cursor: pointer; }
.gallery-button span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gallery-button small { color: var(--color-text-tertiary); }
.gallery-button:hover { color: var(--main-700); background: var(--main-30); }
.gallery-button.active { color: var(--main-700); background: var(--main-50); font-weight: 650; }
.gallery-tree-children { margin: 2px 0 5px 17px; padding-left: 7px; border-left: 1px solid var(--main-100); }
.gallery-button.child { padding: 7px 7px; font-size: 12px; }
.sidebar-state { padding: 12px 8px; color: var(--color-text-secondary); font-size: 12px; }
.picker-content { min-width: 0; padding: 16px; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: 14px; }
.picker-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.picker-toolbar > div { min-width: 0; display: grid; gap: 2px; }
.picker-toolbar > div small { color: var(--color-text-secondary); font-size: 12px; }
.picker-search { width: min(360px, 52%); position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto; }
.picker-search svg { position: absolute; left: 10px; top: 10px; color: var(--gray-500); }
.picker-search input { min-width: 0; padding: 8px 9px 8px 33px; border: 1px solid var(--gray-200); border-right: 0; border-radius: 7px 0 0 7px; color: var(--color-text); background: var(--gray-0); outline: none; }
.picker-search button { padding: 0 12px; border: 1px solid var(--gray-200); border-radius: 0 7px 7px 0; color: var(--main-700); background: var(--gray-25); cursor: pointer; }
.image-grid { align-content: start; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 11px; }
.image-card { min-width: 0; padding: 7px; border: 1px solid var(--gray-150); border-radius: 8px; display: grid; gap: 5px; color: var(--color-text); background: var(--gray-0); text-align: left; cursor: pointer; }
.image-card:hover { border-color: var(--main-300); }
.image-card.selected { border-color: var(--main-500); box-shadow: 0 0 0 2px var(--main-50); }
.image-preview { position: relative; aspect-ratio: 1 / 1; border-radius: 6px; display: grid; place-items: center; overflow: hidden; color: var(--gray-400); background: var(--gray-50); }
.image-preview img { width: 100%; height: 100%; object-fit: cover; }
.selected-check { position: absolute; top: 7px; right: 7px; color: var(--gray-0); filter: drop-shadow(0 1px 3px var(--dark-70)); }
.image-card strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.image-card small { color: var(--color-text-tertiary); font-size: 10px; }
.picker-state { min-height: 360px; display: grid; place-content: center; justify-items: center; gap: 7px; color: var(--color-text-secondary); text-align: center; }
.picker-state.empty strong { color: var(--color-text); }
.picker-state.empty span { font-size: 12px; }
.picker-footer { padding-top: 13px; border-top: 1px solid var(--gray-150); display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pagination, .footer-actions { display: flex; align-items: center; gap: 8px; }
.pagination span { color: var(--color-text-tertiary); font-size: 11px; }
@media (max-width: 760px) { .material-picker { min-height: 620px; grid-template-columns: 1fr; grid-template-rows: auto 1fr; }.gallery-sidebar { border-right: 0; border-bottom: 1px solid var(--gray-150); display: flex; gap: 6px; overflow-x: auto; }.sidebar-heading { display: none; }.gallery-tree-group { min-width: 150px; margin: 0; }.picker-toolbar, .picker-footer { align-items: stretch; flex-direction: column; }.picker-search { width: 100%; }.image-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.footer-actions { justify-content: flex-end; } }
</style>
