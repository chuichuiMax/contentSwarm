<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  ArrowLeft,
  Download,
  Eye,
  Folder,
  FolderPlus,
  ImagePlus,
  Pencil,
  Plus,
  RefreshCw,
  ScanText,
  Search,
  Settings2,
  Trash2,
  Upload
} from 'lucide-vue-next'

import { contentApi } from '@/apis/content_api'
import { materialLibraryApi } from '@/apis/material_library_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import PosterOcrReviewModal from '@/components/content/PosterOcrReviewModal.vue'
import VisualWorkspaceHeader from '@/components/content/VisualWorkspaceHeader.vue'

const tabs = [
  { key: 'image', label: '素材图片', path: '/materials/images' }
]
const materialType = ref('image')
const isGalleryRoot = computed(() => materialType.value === 'image' && !activeGallery.value)
const loading = ref(false)
const uploading = ref(false)
const categories = ref([])
const galleries = ref([])
const industries = ref([])
const industryFilter = ref('')
const activeGallery = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const queryInput = ref('')
const query = ref('')
const categoryFilter = ref('')
const sort = ref('newest')
const uploadOpen = ref(false)
const selectedFiles = ref([])
const uploadCategory = ref('')
const fileInput = ref(null)
const uploadDragging = ref(false)
const previewItem = ref(null)
const ocrReviewOpen = ref(false)
const ocrReviewItem = ref(null)
const pendingReviewTemplates = ref([])
const editOpen = ref(false)
const editingItem = ref(null)
const editForm = reactive({ name: '', category: '' })
const categoryEditorOpen = ref(false)
const categorySaving = ref(false)
const categoryEditorMode = ref('create')
const editingCategory = ref(null)
const categoryParentId = ref('')
const categoryForm = reactive({ name: '', description: '', industry_slug: '' })
const categoryManagerOpen = ref(false)
const deleteCategoryOpen = ref(false)
const categoryDeleting = ref(false)
const deletingCategory = ref(null)
const deleteTargetCategory = ref('')
const previewUrls = new Map()
const maxUploadBytes = 20 * 1024 * 1024
const supportedImageTypes = new Set(['image/png', 'image/jpeg', 'image/webp'])

const categoryMap = computed(() => Object.fromEntries(categories.value.map((item) => [item.code, item])))
const currentGallery = computed(() => categoryMap.value[activeGallery.value])
const parentGallery = computed(() => categoryMap.value[currentGallery.value?.parent_id] || null)
const isTopLevelGallery = computed(() => Boolean(currentGallery.value && !currentGallery.value.parent_id))
const orderedCategories = computed(() => {
  const roots = categories.value.filter((item) => !item.parent_id)
  return roots.flatMap((root) => [root, ...categories.value.filter((item) => item.parent_id === root.id)])
})
const uploadCategories = computed(() => orderedCategories.value)
const uploadFileLimit = computed(() => materialType.value === 'image' ? 50 : 100)
const deleteTargetOptions = computed(() => categories.value.filter((item) => item.id !== deletingCategory.value?.id))
const filteredGalleries = computed(() => {
  const term = queryInput.value.trim().toLowerCase()
  const scoped = isGalleryRoot.value
    ? galleries.value.filter((item) => !item.parent_id)
    : (isTopLevelGallery.value ? galleries.value.filter((item) => item.parent_id === activeGallery.value) : [])
  const industryScoped = isGalleryRoot.value && industryFilter.value
    ? scoped.filter((item) => (item.industry_slug || 'uncategorized') === industryFilter.value)
    : scoped
  if (!term) return industryScoped
  return industryScoped.filter((item) => `${item.name}${item.description}`.toLowerCase().includes(term))
})
const galleryGroups = computed(() => {
  if (!isGalleryRoot.value) return [{ slug: 'children', name: '二级图库', galleries: filteredGalleries.value }]
  const options = [...industries.value, { slug: 'uncategorized', name: '未分类行业' }]
  return options
    .map((industry) => ({
      ...industry,
      galleries: filteredGalleries.value.filter(
        (gallery) => (gallery.industry_slug || 'uncategorized') === industry.slug
      )
    }))
    .filter((group) => group.galleries.length)
})
const createCategoryTitle = computed(() => {
  if (categoryEditorMode.value === 'edit') {
    if (materialType.value !== 'image') return '编辑分类'
    return editingCategory.value?.parent_id ? '编辑二级图库' : '编辑图库'
  }
  if (materialType.value !== 'image') return '新增分类'
  return categoryParentId.value ? '新建二级图库' : '新建图库'
})

function releasePreviews() {
  previewUrls.forEach((url) => URL.revokeObjectURL(url))
  previewUrls.clear()
}

async function blobPreview(id, key = id) {
  const response = await materialLibraryApi.getItemFile(id)
  const url = URL.createObjectURL(await response.blob())
  previewUrls.set(key, url)
  return url
}

async function loadCategories() {
  const requestedType = materialType.value
  const response = await materialLibraryApi.listCategories(requestedType)
  if (materialType.value === requestedType) categories.value = response.categories || []
}

function openCreateCategory(parentId = '') {
  categoryEditorMode.value = 'create'
  editingCategory.value = null
  categoryParentId.value = typeof parentId === 'string' ? parentId : ''
  Object.assign(categoryForm, {
    name: '',
    description: '',
    industry_slug: categoryParentId.value ? (categoryMap.value[categoryParentId.value]?.industry_slug || '') : ''
  })
  categoryEditorOpen.value = true
}

function openEditCategory(category) {
  categoryEditorMode.value = 'edit'
  editingCategory.value = category
  categoryParentId.value = category.parent_id || ''
  Object.assign(categoryForm, {
    name: category.name,
    description: category.description || '',
    industry_slug: category.industry_slug || ''
  })
  categoryEditorOpen.value = true
}

async function saveCategory() {
  if (!categoryForm.name.trim()) return message.warning('请输入名称')
  if (materialType.value === 'image' && !categoryParentId.value && !categoryForm.industry_slug) {
    return message.warning('请选择图库所属行业')
  }
  const payload = {
    name: categoryForm.name.trim(),
    description: categoryForm.description.trim(),
    ...(materialType.value === 'image' && !categoryParentId.value
      ? { industry_slug: categoryForm.industry_slug }
      : {})
  }
  categorySaving.value = true
  try {
    if (categoryEditorMode.value === 'create') {
      await materialLibraryApi.createCategory({
        material_type: materialType.value,
        parent_id: categoryParentId.value || null,
        ...payload
      })
      message.success(categoryParentId.value ? '二级图库已创建' : (materialType.value === 'image' ? '图库已创建' : '分类已创建'))
    } else {
      await materialLibraryApi.updateCategory(materialType.value, editingCategory.value.id, payload)
      message.success(materialType.value === 'image' ? '图库信息已更新' : '分类已更新')
    }
    categoryEditorOpen.value = false
    await loadCategories()
    if (materialType.value === 'image') await loadGalleries()
  } catch (error) {
    message.error(error.message || '保存失败，请稍后重试')
  } finally {
    categorySaving.value = false
  }
}

function askDeleteCategory(category) {
  if (materialType.value === 'image' && category.child_count > 0) {
    return message.warning('该一级图库仍有二级图库，请先移动或删除二级图库')
  }
  deletingCategory.value = category
  deleteTargetCategory.value = category.parent_id || categories.value.find((item) => item.is_system)?.id || ''
  deleteCategoryOpen.value = true
}

async function confirmDeleteCategory() {
  if (deletingCategory.value.count > 0 && !deleteTargetCategory.value) {
    return message.warning('请选择素材迁移目标')
  }
  categoryDeleting.value = true
  try {
    await materialLibraryApi.deleteCategory(
      materialType.value,
      deletingCategory.value.id,
      deleteTargetCategory.value || null
    )
    if (activeGallery.value === deletingCategory.value.id) activeGallery.value = ''
    if (categoryFilter.value === deletingCategory.value.id) categoryFilter.value = ''
    deleteCategoryOpen.value = false
    message.success(`${materialType.value === 'image' ? '图库' : '分类'}已删除，原有素材已安全迁移`)
    await loadCategories()
    await loadItems()
  } catch (error) {
    message.error(error.message || '删除失败，请稍后重试')
  } finally {
    categoryDeleting.value = false
  }
}

async function loadGalleries() {
  loading.value = true
  try {
    const response = await materialLibraryApi.listGalleries()
    releasePreviews()
    industries.value = response.industries || []
    galleries.value = await Promise.all((response.galleries || []).map(async (gallery) => ({
      ...gallery,
      coverUrl: gallery.cover_item_id ? await blobPreview(gallery.cover_item_id, `gallery-${gallery.code}`) : ''
    })))
  } catch (error) {
    message.error(error.message || '图库加载失败')
  } finally {
    loading.value = false
  }
}

async function loadItems() {
  if (isGalleryRoot.value) return loadGalleries()
  loading.value = true
  try {
    const response = await materialLibraryApi.listItems({
      material_type: materialType.value,
      category: materialType.value === 'image' ? activeGallery.value : categoryFilter.value,
      query: query.value,
      sort: sort.value,
      page: page.value,
      page_size: 24
    })
    const next = response.items || []
    releasePreviews()
    items.value = await Promise.all(next.map(async (item) => ({
      ...item,
      previewUrl: await blobPreview(item.id)
    })))
    total.value = response.total || 0
  } catch (error) {
    message.error(error.message || '素材加载失败')
  } finally {
    loading.value = false
  }
}

function search() {
  if (isGalleryRoot.value) return
  query.value = queryInput.value.trim()
  page.value = 1
  void loadItems()
}

function enterGallery(gallery) {
  activeGallery.value = gallery.code
  query.value = ''
  queryInput.value = ''
  page.value = 1
  void loadItems()
}

function leaveGallery() {
  const targetGallery = currentGallery.value?.parent_id || ''
  activeGallery.value = targetGallery
  items.value = []
  query.value = ''
  queryInput.value = ''
  if (targetGallery) void loadItems()
  else void loadGalleries()
}

function categoryOptionLabel(category) {
  if (!category.parent_id) return category.name
  return `${categoryMap.value[category.parent_id]?.name || '一级图库'} / ${category.name}`
}

function openUpload() {
  uploadCategory.value = activeGallery.value || ''
  uploadOpen.value = true
}

function resetUpload() {
  selectedFiles.value = []
  uploadCategory.value = ''
  uploadDragging.value = false
  if (fileInput.value) fileInput.value.value = ''
}

const chooseFiles = () => fileInput.value?.click()

function addSelectedFiles(fileList) {
  const files = Array.from(fileList || [])
  if (!files.length) return

  const next = [...selectedFiles.value]
  const knownFiles = new Set(next.map((file) => `${file.name}:${file.size}:${file.lastModified}`))
  let unsupported = 0
  let oversized = 0
  let duplicated = 0
  let overflowed = 0

  files.forEach((file) => {
    const extensionSupported = /\.(png|jpe?g|webp)$/i.test(file.name)
    if (!extensionSupported || (file.type && !supportedImageTypes.has(file.type))) {
      unsupported += 1
      return
    }
    if (file.size > maxUploadBytes) {
      oversized += 1
      return
    }
    const identity = `${file.name}:${file.size}:${file.lastModified}`
    if (knownFiles.has(identity)) {
      duplicated += 1
      return
    }
    if (next.length >= uploadFileLimit.value) {
      overflowed += 1
      return
    }
    knownFiles.add(identity)
    next.push(file)
  })

  selectedFiles.value = next
  const warnings = []
  if (unsupported) warnings.push(`${unsupported} 个文件格式不支持`)
  if (oversized) warnings.push(`${oversized} 个文件超过 20 MB`)
  if (duplicated) warnings.push(`${duplicated} 个重复文件已忽略`)
  if (overflowed) warnings.push(`${overflowed} 个文件超出 ${uploadFileLimit.value} 张上限`)
  if (warnings.length) message.warning(warnings.join('；'))
}

function onFiles(event) {
  addSelectedFiles(event.target.files)
  event.target.value = ''
}

function onUploadDragEnter(event) {
  if (Array.from(event.dataTransfer?.types || []).includes('Files')) uploadDragging.value = true
}

function onUploadDragOver(event) {
  if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return
  uploadDragging.value = true
  event.dataTransfer.dropEffect = 'copy'
}

function onUploadDragLeave(event) {
  if (event.currentTarget.contains(event.relatedTarget)) return
  uploadDragging.value = false
}

function onUploadDrop(event) {
  uploadDragging.value = false
  addSelectedFiles(event.dataTransfer?.files)
}

async function uploadFiles() {
  if (!selectedFiles.value.length) return message.warning('请选择图片文件')
  if (!uploadCategory.value) return message.warning('请选择素材分类')
  uploading.value = true
  try {
    let response
    if (materialType.value === 'image') {
      response = await materialLibraryApi.importImages(selectedFiles.value, uploadCategory.value)
    } else {
      response = await contentApi.importCoverPosterTemplates(selectedFiles.value, uploadCategory.value)
      pendingReviewTemplates.value = (response.items || [])
        .map((result) => result.template)
        .filter((item) => item?.requires_review)
    }
    message.success(materialType.value === 'cover_template' && pendingReviewTemplates.value.length
      ? '模板上传成功，请校对 OCR 识别结果后启用'
      : '素材上传成功')
    uploadOpen.value = false
    const uploadedTo = uploadCategory.value
    resetUpload()
    page.value = 1
    if (materialType.value === 'image' && activeGallery.value !== uploadedTo) {
      activeGallery.value = uploadedTo
    }
    await loadItems()
    if (materialType.value === 'cover_template' && pendingReviewTemplates.value.length) {
      await openNextPendingReview()
    }
  } catch (error) {
    message.error(error.message || '素材上传失败')
  } finally {
    uploading.value = false
  }
}

async function reviewItemForTemplate(posterTemplate) {
  const listed = items.value.find((item) => item.poster_template_id === posterTemplate.id)
  if (listed) return listed
  const response = await contentApi.getCoverAssetFile(posterTemplate.asset_id)
  const previewUrl = URL.createObjectURL(await response.blob())
  previewUrls.set(`ocr-${posterTemplate.id}`, previewUrl)
  return {
    id: posterTemplate.asset_id,
    name: posterTemplate.name,
    poster_template_id: posterTemplate.id,
    previewUrl
  }
}

async function openNextPendingReview() {
  const next = pendingReviewTemplates.value[0]
  if (!next) return
  ocrReviewItem.value = await reviewItemForTemplate(next)
  ocrReviewOpen.value = true
}

async function openOcrReview(item) {
  if (!item.poster_template_id) return message.error('模板识别记录不存在')
  ocrReviewItem.value = item
  ocrReviewOpen.value = true
}

async function onReviewConfirmed(reviewedTemplate) {
  pendingReviewTemplates.value = pendingReviewTemplates.value.filter((item) => item.id !== reviewedTemplate.id)
  await loadItems()
  if (pendingReviewTemplates.value.length) setTimeout(openNextPendingReview, 0)
}

function onReviewSaved(reviewedTemplate) {
  const item = items.value.find((entry) => entry.poster_template_id === reviewedTemplate.id)
  if (item) {
    item.template_status = reviewedTemplate.status
    item.template_version = reviewedTemplate.version
    item.review_status = reviewedTemplate.review_status
  }
}

function templateStatusLabel(item) {
  if (item.template_status === 'needs_review') return '待校对'
  if (item.template_status === 'needs_annotation') return '待标注'
  if (item.template_status === 'ready' && item.status === 'enabled') return '已启用'
  return '已停用'
}

function showEdit(item) {
  editingItem.value = item
  editForm.name = item.name
  editForm.category = item.category
  editOpen.value = true
}

async function saveEdit() {
  if (!editForm.name.trim() || !editForm.category) return message.warning('请填写名称并选择分类')
  await materialLibraryApi.updateItem(editingItem.value.id, {
    name: editForm.name.trim(),
    category: editForm.category
  })
  editOpen.value = false
  message.success('素材信息已更新')
  await loadItems()
}

async function downloadItem(item) {
  const response = await materialLibraryApi.getItemFile(item.id)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = item.file_name || `${item.name}.png`
  anchor.click()
  URL.revokeObjectURL(url)
}

function removeItem(item) {
  Modal.confirm({
    title: `删除“${item.name}”`,
    content: '素材文件会同时从私有 image 桶删除；正在被封面任务使用的素材不能删除。',
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await materialLibraryApi.deleteItem(item.id)
      message.success('素材已删除')
      await loadItems()
    }
  })
}

function formatSize(bytes) {
  if (!bytes) return '0 KB'
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`
}

watch(materialType, async () => {
  activeGallery.value = ''
  categoryFilter.value = ''
  query.value = ''
  queryInput.value = ''
  page.value = 1
  items.value = []
  try {
    await loadCategories()
    await loadItems()
  } catch (error) {
    message.error(error.message || '素材分类加载失败')
  }
}, { immediate: true })
onBeforeUnmount(releasePreviews)
</script>

<template>
  <div class="material-library-view layout-container">
    <VisualWorkspaceHeader subtitle="集中管理设计可直接使用的图片和封面模板" />
    <PageHeader title="素材库" :tabs="tabs" :active-key="materialType" :loading="loading" show-border>
      <template #actions>
        <template v-if="materialType === 'image'">
          <a-button v-if="isGalleryRoot || (isTopLevelGallery && !currentGallery?.is_system)" class="lucide-icon-btn" @click="openCreateCategory(isTopLevelGallery ? activeGallery : '')">
            <FolderPlus :size="15" />{{ isTopLevelGallery ? '新建二级图库' : '新建图库' }}
          </a-button>
        </template>
        <a-button v-else class="lucide-icon-btn" @click="categoryManagerOpen = true">
          <Settings2 :size="15" />分类管理
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" @click="openUpload">
          <Upload :size="15" />上传{{ materialType === 'image' ? '图片' : '模板' }}
        </a-button>
      </template>
    </PageHeader>

    <main class="material-content">
      <div v-if="materialType === 'image'" class="context-head">
        <button v-if="activeGallery" type="button" class="back-button" @click="leaveGallery"><ArrowLeft :size="16" />{{ parentGallery ? `返回${parentGallery.name}` : '返回图库' }}</button>
        <div>
          <h2>{{ activeGallery ? currentGallery?.name : '我的图库' }}</h2>
          <p v-if="parentGallery" class="gallery-path">{{ parentGallery.name }} / {{ currentGallery?.name }}</p>
          <p>{{ activeGallery ? (currentGallery?.description || '这个图库还没有填写说明。') : '创建专属图库管理图片，也可以随时重命名、移动或整理素材。' }}</p>
        </div>
      </div>
      <div v-else class="context-head">
        <div><h2>封面模板</h2><p>以大字报形式浏览竖版模板，按使用场景快速筛选。</p></div>
      </div>

      <div class="toolbar">
        <a-input v-model:value="queryInput" allow-clear :placeholder="isGalleryRoot ? '搜索图库名称' : '搜索素材名称'" @pressEnter="search" @clear="search">
          <template #prefix><Search :size="15" /></template>
        </a-input>
        <a-select v-if="isGalleryRoot" v-model:value="industryFilter" class="category-filter" placeholder="全部行业" allow-clear>
          <a-select-option v-for="item in industries" :key="item.slug" :value="item.slug">{{ item.name }}</a-select-option>
          <a-select-option value="uncategorized">未分类行业</a-select-option>
        </a-select>
        <a-select v-else-if="materialType === 'cover_template'" v-model:value="categoryFilter" class="category-filter" placeholder="全部分类" allow-clear @change="page = 1; loadItems()">
          <a-select-option v-for="item in categories" :key="item.code" :value="item.code">{{ item.name }}</a-select-option>
        </a-select>
        <a-select v-if="!isGalleryRoot" v-model:value="sort" class="sort-filter" @change="page = 1; loadItems()">
          <a-select-option value="newest">最新上传</a-select-option>
          <a-select-option value="oldest">最早上传</a-select-option>
          <a-select-option value="name">名称排序</a-select-option>
        </a-select>
        <a-button v-if="!isGalleryRoot" @click="search">查询</a-button>
        <a-button class="lucide-icon-btn" :loading="loading" @click="loadItems"><RefreshCw :size="15" />刷新</a-button>
      </div>

      <a-spin :spinning="loading">
        <div v-for="group in galleryGroups" :key="group.slug" class="gallery-section">
          <h3>{{ group.name }}</h3>
          <div class="gallery-grid">
          <article v-for="gallery in group.galleries" :key="gallery.id" class="gallery-card">
            <button type="button" class="gallery-open" @click="enterGallery(gallery)">
              <span class="gallery-cover">
                <img v-if="gallery.coverUrl" :src="gallery.coverUrl" alt="" />
                <span v-else class="folder-art"><Folder :size="44" /><i></i></span>
                <em>{{ gallery.count }} 张<span v-if="gallery.child_count"> · {{ gallery.child_count }} 个子图库</span></em>
              </span>
              <span class="gallery-copy"><strong>{{ gallery.name }}</strong><small>{{ gallery.description || '暂未填写图库说明' }}</small><em v-if="isGalleryRoot">{{ gallery.industry_name }}</em></span>
            </button>
            <div class="gallery-actions">
              <button type="button" :aria-label="`编辑图库 ${gallery.name}`" title="编辑图库" @click="openEditCategory(gallery)"><Pencil :size="15" /></button>
              <button v-if="!gallery.is_system" type="button" class="danger" :aria-label="`删除图库 ${gallery.name}`" title="删除图库" @click="askDeleteCategory(gallery)"><Trash2 :size="15" /></button>
            </div>
          </article>
          </div>
        </div>

        <div v-if="!isGalleryRoot && items.length" class="material-section">
          <h3 v-if="isTopLevelGallery && filteredGalleries.length">当前图库图片</h3>
          <div :class="materialType === 'image' ? 'image-grid' : 'poster-wall'">
          <article v-for="item in items" :key="item.id" class="material-card" :class="{ poster: materialType === 'cover_template' }">
            <button type="button" class="preview-button" @click="previewItem = item">
              <img :src="item.previewUrl" :alt="item.name" />
              <span v-if="materialType === 'cover_template'" class="poster-overlay"><b>{{ item.name }}</b><small>{{ item.category_name }}</small></span>
              <em v-if="materialType === 'cover_template'" class="template-status" :data-status="item.template_status">{{ templateStatusLabel(item) }}</em>
            </button>
            <div class="material-info">
              <strong v-if="materialType === 'image'" :title="item.name">{{ item.name }}</strong>
              <small>{{ item.category_name }} · {{ item.width }}×{{ item.height }} · {{ formatSize(item.file_size) }}</small>
            </div>
            <div class="card-actions">
              <button type="button" title="预览" @click="previewItem = item"><Eye :size="15" /></button>
              <button v-if="materialType === 'cover_template'" type="button" title="校对 OCR 识别结果" @click="openOcrReview(item)"><ScanText :size="15" /></button>
              <button type="button" title="下载" @click="downloadItem(item)"><Download :size="15" /></button>
              <button type="button" title="编辑名称和分类" @click="showEdit(item)"><Pencil :size="15" /></button>
              <button type="button" class="danger" title="删除" @click="removeItem(item)"><Trash2 :size="15" /></button>
            </div>
          </article>
          </div>
        </div>

        <a-empty v-if="!loading && !filteredGalleries.length && (isGalleryRoot || !items.length)" :image="false" :description="isGalleryRoot ? '没有匹配的图库' : (query ? '未找到匹配素材' : (isTopLevelGallery ? '当前图库还没有图片或二级图库' : '当前图库还没有图片'))">
          <a-button v-if="!query && isGalleryRoot" type="primary" class="lucide-icon-btn" @click="openCreateCategory('')"><FolderPlus :size="15" />新建第一个图库</a-button>
          <a-button v-else-if="!query" type="primary" class="lucide-icon-btn" @click="openUpload"><ImagePlus :size="15" />上传第一份素材</a-button>
        </a-empty>
      </a-spin>
      <a-pagination v-if="!isGalleryRoot && total > 24" v-model:current="page" :total="total" :page-size="24" show-less-items @change="loadItems" />
    </main>

    <a-modal v-model:open="uploadOpen" :title="`上传${materialType === 'image' ? '素材图片' : '封面模板'}`" :confirm-loading="uploading" ok-text="开始上传" @ok="uploadFiles" @cancel="resetUpload">
      <div class="upload-form">
        <input ref="fileInput" type="file" multiple accept=".png,.jpg,.jpeg,.webp" hidden @change="onFiles" />
        <button
          type="button"
          class="upload-drop"
          :class="{ dragging: uploadDragging }"
          @click="chooseFiles"
          @dragenter.prevent="onUploadDragEnter"
          @dragover.prevent="onUploadDragOver"
          @dragleave="onUploadDragLeave"
          @drop.prevent="onUploadDrop"
        >
          <Upload :size="22" />
          <span>{{ uploadDragging ? '松开鼠标添加图片' : (selectedFiles.length ? `已选择 ${selectedFiles.length} 个文件，可继续拖入` : '点击选择或拖拽 PNG、JPG、WebP 图片到此处') }}</span>
          <small>单张不超过 20 MB；素材图片最多 50 张，封面模板最多 100 张</small>
        </button>
        <label><span>分类 <b>*</b></span><a-select v-model:value="uploadCategory" placeholder="请选择一个明确分类">
          <a-select-option v-for="item in uploadCategories" :key="item.code" :value="item.code"><strong>{{ categoryOptionLabel(item) }}</strong> — {{ item.description }}</a-select-option>
        </a-select></label>
      </div>
    </a-modal>

    <a-modal :open="Boolean(previewItem)" :title="previewItem?.name" :footer="null" width="min(900px, 92vw)" @cancel="previewItem = null">
      <img v-if="previewItem" class="large-preview" :src="previewItem.previewUrl" :alt="previewItem.name" />
    </a-modal>

    <a-modal v-model:open="editOpen" title="编辑素材信息" ok-text="保存" @ok="saveEdit">
      <div class="upload-form">
        <label><span>名称</span><a-input v-model:value="editForm.name" maxlength="255" /></label>
        <label><span>分类</span><a-select v-model:value="editForm.category" placeholder="请选择分类">
          <a-select-option v-for="item in uploadCategories" :key="item.code" :value="item.code">{{ categoryOptionLabel(item) }} — {{ item.description }}</a-select-option>
        </a-select></label>
      </div>
    </a-modal>

    <a-modal v-model:open="categoryEditorOpen" :title="createCategoryTitle" :confirm-loading="categorySaving" ok-text="保存" @ok="saveCategory">
      <div class="upload-form">
        <label v-if="categoryEditorMode === 'create' && categoryParentId"><span>所属一级图库</span><a-input :value="categoryMap[categoryParentId]?.name" disabled /></label>
        <label v-if="materialType === 'image' && !categoryParentId"><span>所属行业 <b>*</b></span><a-select v-model:value="categoryForm.industry_slug" placeholder="请选择一个行业">
          <a-select-option v-for="item in industries" :key="item.slug" :value="item.slug">{{ item.name }}</a-select-option>
        </a-select></label>
        <label><span>{{ categoryParentId ? '二级图库名称' : (materialType === 'image' ? '图库名称' : '分类名称') }} <b>*</b></span><a-input v-model:value="categoryForm.name" maxlength="80" :placeholder="categoryParentId ? '例如：客厅案例' : (materialType === 'image' ? '例如：春季新品素材' : '例如：客户案例')" /></label>
        <label><span>说明</span><a-textarea v-model:value="categoryForm.description" :rows="3" maxlength="255" show-count :placeholder="materialType === 'image' ? '说明图库收纳的图片范围，方便团队快速判断' : '说明这个分类适用的封面场景'" /></label>
      </div>
    </a-modal>

    <a-modal v-model:open="categoryManagerOpen" title="封面模板分类管理" :footer="null" width="620px">
      <div class="category-manager-head"><p>分类仅对当前账号生效，新增、重命名或删除不会改变模板文件。</p><a-button type="primary" class="lucide-icon-btn" @click="openCreateCategory"><Plus :size="15" />新增分类</a-button></div>
      <div class="category-list">
        <div v-for="item in categories" :key="item.id" class="category-row">
          <div><strong>{{ item.name }}</strong><small>{{ item.description || '暂未填写分类说明' }}</small></div>
          <span>{{ item.count }} 个模板</span>
          <div class="category-row-actions">
            <button type="button" :aria-label="`编辑分类 ${item.name}`" title="编辑分类" @click="openEditCategory(item)"><Pencil :size="15" /></button>
            <button v-if="!item.is_system" type="button" class="danger" :aria-label="`删除分类 ${item.name}`" title="删除分类" @click="askDeleteCategory(item)"><Trash2 :size="15" /></button>
            <em v-else>系统兜底</em>
          </div>
        </div>
      </div>
    </a-modal>

    <a-modal v-model:open="deleteCategoryOpen" :title="`删除${materialType === 'image' ? '图库' : '分类'}“${deletingCategory?.name || ''}”`" :confirm-loading="categoryDeleting" ok-text="确认删除" ok-type="danger" @ok="confirmDeleteCategory">
      <div class="delete-category-content">
        <p>删除只会移除分类信息，不会删除 image 桶中的素材文件。</p>
        <label v-if="deletingCategory?.count > 0"><span>将其中 {{ deletingCategory.count }} 个素材移动到</span><a-select v-model:value="deleteTargetCategory" placeholder="请选择迁移目标">
          <a-select-option v-for="item in deleteTargetOptions" :key="item.id" :value="item.id">{{ categoryOptionLabel(item) }}</a-select-option>
        </a-select></label>
        <a-alert v-else type="info" show-icon message="这是一个空分类，可以直接删除。" />
      </div>
    </a-modal>

    <PosterOcrReviewModal
      v-model:open="ocrReviewOpen"
      :item="ocrReviewItem"
      @confirmed="onReviewConfirmed"
      @saved="onReviewSaved"
    />
  </div>
</template>

<style scoped lang="less">
.material-library-view { height: 100%; display: flex; flex-direction: column; background: var(--gray-0); }
.material-content { flex: 1; overflow: auto; padding: 20px var(--page-padding) 36px; }
.context-head { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 16px; }
.context-head h2 { margin: 0; font-size: 20px; color: var(--color-text); }
.context-head p { margin: 5px 0 0; color: var(--color-text-secondary); }
.context-head .gallery-path { margin-bottom: -2px; color: var(--color-text-tertiary); font-size: 12px; }
.back-button { display: flex; align-items: center; gap: 5px; min-height: 32px; border: 0; background: transparent; color: var(--color-primary); cursor: pointer; }
.toolbar { display: flex; gap: 8px; max-width: 920px; margin-bottom: 20px; }
.toolbar :deep(.ant-input-affix-wrapper) { max-width: 380px; }
.category-filter { width: 170px; }.sort-filter { width: 130px; }
.gallery-section, .material-section { margin-bottom: 22px; }
.gallery-section h3, .material-section h3 { margin: 0 0 12px; color: var(--color-text); font-size: 15px; }
.gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 18px; }
.gallery-card { position: relative; overflow: hidden; min-height: 250px; border: 1px solid var(--gray-150); border-radius: 14px; background: var(--gray-0); transition: transform .18s, box-shadow .18s, border-color .18s; }
.gallery-card:hover { transform: translateY(-2px); border-color: var(--color-primary); box-shadow: 0 8px 24px rgb(20 35 70 / 10%); }
.gallery-open { display: block; width: 100%; padding: 0; text-align: left; border: 0; background: transparent; cursor: pointer; }
.gallery-cover { position: relative; display: grid; place-items: center; height: 166px; overflow: hidden; background: radial-gradient(circle at 25% 20%, var(--main-20), transparent 48%), linear-gradient(145deg, var(--gray-25), var(--gray-100)); color: var(--color-primary); }
.gallery-cover::after { position: absolute; inset: 0; background: linear-gradient(180deg, transparent 60%, rgb(15 25 45 / 10%)); content: ''; pointer-events: none; }
.gallery-cover img { width: 100%; height: 100%; object-fit: cover; transition: transform .25s; }.gallery-card:hover .gallery-cover img { transform: scale(1.035); }
.folder-art { position: relative; display: grid; place-items: center; width: 84px; height: 72px; border-radius: 20px; background: var(--gray-0); box-shadow: 0 12px 28px rgb(30 55 95 / 12%); }.folder-art i { position: absolute; right: 13px; bottom: 12px; width: 22px; height: 5px; border-radius: 3px; background: var(--main-100); }
.gallery-cover em { position: absolute; z-index: 1; right: 12px; bottom: 12px; padding: 4px 9px; border-radius: 14px; background: rgb(15 25 45 / 66%); color: white; font-size: 12px; font-style: normal; backdrop-filter: blur(4px); }
.gallery-copy { display: flex; flex-direction: column; gap: 6px; padding: 15px 76px 18px 16px; }
.gallery-copy strong { overflow: hidden; font-size: 17px; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text); }.gallery-copy small { min-height: 40px; color: var(--color-text-secondary); line-height: 1.55; }
.gallery-copy em { color: var(--color-primary); font-size: 12px; font-style: normal; }
.gallery-actions { position: absolute; right: 12px; bottom: 15px; display: flex; gap: 3px; }.gallery-actions button, .category-row-actions button { display: grid; place-items: center; width: 30px; height: 30px; border: 0; border-radius: 7px; background: var(--gray-25); color: var(--color-text-secondary); cursor: pointer; }.gallery-actions button:hover, .category-row-actions button:hover { background: var(--main-20); color: var(--color-primary); }.gallery-actions button.danger:hover, .category-row-actions button.danger:hover { color: var(--color-error-700); }
.image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 16px; }
.poster-wall { columns: 260px; column-gap: 18px; }
.material-card { position: relative; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 9px; background: var(--gray-0); }
.material-card.poster { break-inside: avoid; margin: 0 0 18px; }
.material-card:hover { border-color: var(--gray-300); box-shadow: 0 6px 20px rgb(20 35 70 / 9%); }
.preview-button { position: relative; display: block; width: 100%; height: 190px; overflow: hidden; padding: 0; border: 0; background: var(--gray-25); cursor: zoom-in; }
.preview-button img { width: 100%; height: 100%; object-fit: cover; transition: transform .2s; }
.material-card:hover .preview-button img { transform: scale(1.025); }
.poster .preview-button { height: auto; min-height: 320px; aspect-ratio: 3 / 4; }
.poster .preview-button img { object-fit: cover; }
.poster-overlay { position: absolute; inset: auto 0 0; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; padding: 54px 16px 16px; text-align: left; background: linear-gradient(transparent, rgb(0 0 0 / 82%)); color: white; }
.poster-overlay b { font-size: 18px; line-height: 1.3; }.poster-overlay small { color: rgb(255 255 255 / 78%); }
.template-status { position: absolute; z-index: 2; top: 10px; right: 10px; padding: 4px 8px; border-radius: 999px; color: var(--gray-700); background: rgb(255 255 255 / 90%); box-shadow: 0 2px 8px rgb(20 35 70 / 10%); font-size: 11px; font-style: normal; backdrop-filter: blur(4px); }
.template-status[data-status='needs_review'] { color: var(--color-warning-900); background: color-mix(in srgb, var(--color-warning-50) 92%, transparent); }.template-status[data-status='ready'] { color: var(--color-success-700); background: color-mix(in srgb, var(--color-success-50) 92%, transparent); }
.material-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 11px 12px 8px; }
.material-info strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text); }
.material-info small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text-secondary); }
.card-actions { display: flex; justify-content: flex-end; gap: 3px; padding: 0 8px 8px; }
.card-actions button { display: grid; place-items: center; width: 30px; height: 30px; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.card-actions button:hover { background: var(--gray-50); color: var(--color-primary); }.card-actions button.danger:hover { color: var(--color-error-700); }
.upload-form { display: flex; flex-direction: column; gap: 16px; }
.upload-form label { display: flex; flex-direction: column; gap: 6px; color: var(--color-text); }.upload-form label b { color: var(--color-error-700); }
.upload-drop { display: flex; flex-direction: column; align-items: center; gap: 7px; padding: 28px; border: 1px dashed var(--gray-300); border-radius: 8px; background: var(--gray-25); color: var(--color-text-secondary); cursor: pointer; }
.upload-drop:hover, .upload-drop.dragging { border-color: var(--main-500); background: var(--main-20); color: var(--main-700); }
.upload-drop.dragging { box-shadow: 0 0 0 3px var(--main-100); }
.upload-drop small { color: var(--color-text-tertiary); }
.large-preview { display: block; max-width: 100%; max-height: 72vh; margin: 0 auto; object-fit: contain; }
.category-manager-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }.category-manager-head p { margin: 0; color: var(--color-text-secondary); }
.category-list { display: flex; flex-direction: column; max-height: 520px; overflow: auto; border: 1px solid var(--gray-150); border-radius: 10px; }
.category-row { display: grid; grid-template-columns: minmax(0, 1fr) 90px 88px; align-items: center; gap: 12px; padding: 13px 14px; border-bottom: 1px solid var(--gray-100); }.category-row:last-child { border-bottom: 0; }.category-row > div:first-child { display: flex; flex-direction: column; gap: 4px; min-width: 0; }.category-row strong, .category-row small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.category-row small, .category-row > span { color: var(--color-text-secondary); }.category-row-actions { display: flex; justify-content: flex-end; gap: 3px; }.category-row-actions em { color: var(--color-text-tertiary); font-size: 12px; font-style: normal; }
.delete-category-content { display: flex; flex-direction: column; gap: 14px; }.delete-category-content p { margin: 0; color: var(--color-text-secondary); }.delete-category-content label { display: flex; flex-direction: column; gap: 7px; }
:deep(.ant-pagination) { margin-top: 22px; text-align: right; }
@media (max-width: 720px) {
  .toolbar { flex-wrap: wrap; }.toolbar :deep(.ant-input-affix-wrapper) { max-width: none; width: 100%; }
  .category-filter, .sort-filter { flex: 1; min-width: 130px; }
  .gallery-grid { grid-template-columns: 1fr; }.image-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .poster-wall { columns: 160px; column-gap: 10px; }.material-card.poster { margin-bottom: 10px; }
  .preview-button { height: 140px; }.poster .preview-button { min-height: 220px; }
}
</style>
