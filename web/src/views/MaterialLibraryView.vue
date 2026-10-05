<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
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
  Share2,
  Trash2,
  Upload,
  X
} from 'lucide-vue-next'

import { contentApi } from '@/apis/content_api'
import { materialLibraryApi } from '@/apis/material_library_api'
import { useUserStore } from '@/stores/user'
import PageHeader from '@/components/shared/PageHeader.vue'
import PosterOcrReviewModal from '@/components/content/PosterOcrReviewModal.vue'
import VisualWorkspaceHeader from '@/components/content/VisualWorkspaceHeader.vue'

const route = useRoute()
const router = useRouter()
const targetEmployeeId = computed(() => (
  route.query.gallery === 'rough' && typeof route.query.employee_id === 'string'
    ? route.query.employee_id
    : ''
))
const isEmployeeRoughMode = computed(() => Boolean(targetEmployeeId.value))
const targetEmployee = ref(null)

const tabs = [
  { key: 'image', label: '素材图片', path: '/materials/images' }
]
const materialType = ref('image')
const userStore = useUserStore()
const materialScope = ref('private')
const personalFolder = ref('')
const personalFolders = ref([])
const editingFixedFolder = ref(null)
const fixedFolderName = ref('')
const fixedFolderSaving = ref(false)
const personalItems = ref([])
const personalTotal = ref(0)
const personalPage = ref(1)
const personalFolderName = computed(() => personalFolders.value.find((folder) => folder.id === personalFolder.value)?.name || '')
const personalFolderOrder = ['generated', 'rough', 'works', 'uploads']
const filteredPersonalFolders = computed(() => personalFolders.value
  .filter((folder) => folder.name.toLowerCase().includes(queryInput.value.trim().toLowerCase()))
  .sort((left, right) => personalFolderOrder.indexOf(left.id) - personalFolderOrder.indexOf(right.id)))
const canCreateShared = ref(false)
const isGalleryRoot = computed(() => materialType.value === 'image' && !activeGallery.value)
const loading = ref(false)
const remoteSyncing = ref(false)
const remoteSyncJob = ref(null)
const remoteConfigOpen = ref(false)
const remoteConfigSaving = ref(false)
const remoteConfigState = ref(null)
const resumeRemoteSync = ref(false)
let remoteSyncPollTimer = 0
const remoteConfigForm = reactive({ baseUrl: '', username: '', password: '' })
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
const designStyleFilter = ref('')
const categoryFilter = ref('')
const sort = ref('newest')
const uploadOpen = ref(false)
const selectedFiles = ref([])
const uploadCategory = ref('')
const uploadDesignStyle = ref('')
const fileInput = ref(null)
const folderInput = ref(null)
const uploadDragging = ref(false)
const uploadProgress = reactive({ percent: 0, loaded: 0, total: 0, phase: 'idle' })
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
const categoryForm = reactive({
  name: '',
  description: '',
  industry_slug: '',
  visibility: 'private',
  design_style: '',
  building_name: '',
  area: ''
})
const categoryManagerOpen = ref(false)
const deleteCategoryOpen = ref(false)
const categoryDeleting = ref(false)
const deletingCategory = ref(null)
const deleteTargetCategory = ref('')
const selectedShareItemIds = ref([])
const shareOpen = ref(false)
const shareCreating = ref(false)
const shareUrlForManualCopy = ref('')
const previewUrls = new Map()
const maxUploadBytes = 100 * 1024 * 1024
const supportedImageTypes = new Set(['image/png', 'image/jpeg', 'image/webp'])

const categoryMap = computed(() => Object.fromEntries(categories.value.map((item) => [item.code, item])))
const currentGallery = computed(() => categoryMap.value[activeGallery.value])
const parentGallery = computed(() => categoryMap.value[currentGallery.value?.parent_id] || null)
const isTopLevelGallery = computed(() => Boolean(currentGallery.value && !currentGallery.value.parent_id))
const isChildGallery = computed(() => Boolean(currentGallery.value?.parent_id))
const orderedCategories = computed(() => {
  const roots = categories.value.filter((item) => !item.parent_id)
  return roots.flatMap((root) => [root, ...categories.value.filter((item) => item.parent_id === root.id)])
})
const uploadCategories = computed(() => orderedCategories.value.filter((item) => {
  if ((item.visibility || 'private') !== (currentGallery.value?.visibility || materialScope.value)) return false
  if (materialType.value !== 'image' || item.visibility === 'enterprise') return true
  return item.is_system || item.can_manage
}))
const canUploadCurrentLocation = computed(() => {
  if (materialType.value !== 'image') return true
  if (!activeGallery.value && !personalFolder.value) return false
  if (materialScope.value === 'enterprise') return true
  if (personalFolder.value) return ['rough', 'uploads'].includes(personalFolder.value)
  return Boolean(currentGallery.value?.is_system || currentGallery.value?.can_manage)
})
const uploadFileLimit = computed(() => materialType.value === 'image' ? 50 : 100)
const selectedFilesTotalBytes = computed(() => selectedFiles.value.reduce((sum, file) => sum + file.size, 0))
const uploadProgressText = computed(() => {
  if (uploadProgress.phase === 'processing') return '文件已送达，正在转码入库…'
  if (uploadProgress.phase === 'sending') {
    return `已传输 ${uploadProgress.percent}% · ${formatSize(uploadProgress.loaded)} / ${formatSize(uploadProgress.total)}`
  }
  return ''
})
const deleteTargetOptions = computed(() => categories.value.filter((item) => item.id !== deletingCategory.value?.id && (deletingCategory.value?.visibility !== 'enterprise' || item.visibility === 'enterprise')))
const decorationGalleryStyles = [
  '复合写意', '写意木构', '江南印象', '轻欧简美', '欧美香颂', '新装饰主义', '北欧之光',
  '意境东方', '雅致现代', '优雅缤纷', '极简侘寂', '复古风潮', '艺术室界'
]
const isDecorationGalleryChild = computed(() =>
  ['create', 'edit'].includes(categoryEditorMode.value) &&
  materialType.value === 'image' &&
  categoryParentId.value &&
  categoryMap.value[categoryParentId.value]?.industry_slug === 'decoration'
)
const isDecorationGalleryPage = computed(() =>
  materialType.value === 'image' &&
  isTopLevelGallery.value &&
  currentGallery.value?.industry_slug === 'decoration'
)
const uploadTargetGallery = computed(() => categoryMap.value[uploadCategory.value] || null)
const uploadTargetParent = computed(() => categoryMap.value[uploadTargetGallery.value?.parent_id] || null)
const isDecorationUpload = computed(() =>
  materialType.value === 'image' && Boolean(
    uploadTargetGallery.value?.industry_slug === 'decoration' ||
    uploadTargetParent.value?.industry_slug === 'decoration'
  )
)
const filteredGalleries = computed(() => {
  const term = queryInput.value.trim().toLowerCase()
  const scoped = isGalleryRoot.value
    ? galleries.value.filter((item) => !item.parent_id && (item.visibility || 'private') === materialScope.value)
    : (isTopLevelGallery.value ? galleries.value.filter((item) => item.parent_id === activeGallery.value) : [])
  const styleScoped = isDecorationGalleryPage.value && designStyleFilter.value
    ? scoped.filter((item) => item.design_style === designStyleFilter.value)
    : scoped
  const industryScoped = isGalleryRoot.value && industryFilter.value
    ? styleScoped.filter((item) => (item.industry_slug || 'uncategorized') === industryFilter.value)
    : styleScoped
  if (!term) return industryScoped
  return industryScoped.filter((item) => `${item.name}${item.description}`.toLowerCase().includes(term))
})
const filteredPersonalGalleries = computed(() => {
  const term = queryInput.value.trim().toLowerCase()
  const personal = galleries.value.filter((item) =>
    !item.parent_id &&
    !item.is_system &&
    item.is_global_personal &&
    (item.visibility || 'private') === 'private'
  )
  if (!term) return personal
  return personal.filter((item) => `${item.name}${item.description}`.toLowerCase().includes(term))
})

function groupGalleriesByIndustry(items) {
  const options = [...industries.value, { slug: 'uncategorized', name: '未分类行业' }]
  return options
    .map((industry) => ({
      ...industry,
      galleries: items.filter((gallery) => (gallery.industry_slug || 'uncategorized') === industry.slug)
    }))
    .filter((group) => group.galleries.length)
}

const galleryGroups = computed(() => {
  if (!isGalleryRoot.value) return [{ slug: 'children', name: '二级图库', galleries: filteredGalleries.value }]
  return groupGalleriesByIndustry(filteredGalleries.value)
})
const personalGalleryGroups = computed(() => groupGalleriesByIndustry(filteredPersonalGalleries.value))
const createCategoryTitle = computed(() => {
  if (categoryEditorMode.value === 'edit') {
    if (materialType.value !== 'image') return '编辑分类'
    return editingCategory.value?.parent_id ? '编辑二级图库' : '编辑图库'
  }
  if (materialType.value !== 'image') return '新增分类'
  return '新建图库'
})

function releasePreviews() {
  previewUrls.forEach((url) => URL.revokeObjectURL(url))
  previewUrls.clear()
}

async function blobPreview(id, key = id) {
  const response = await materialLibraryApi.getItemThumbnail(id, targetEmployeeId.value)
  const url = URL.createObjectURL(await response.blob())
  previewUrls.set(key, url)
  return url
}

async function loadCategories() {
  const requestedType = materialType.value
  const response = await materialLibraryApi.listCategories(requestedType, targetEmployeeId.value)
  if (materialType.value === requestedType) {
    categories.value = response.categories || []
    canCreateShared.value = Boolean(response.can_create_shared)
    targetEmployee.value = response.target_employee || null
    if (isEmployeeRoughMode.value && response.personal_rough_category_id) {
      materialScope.value = 'private'
      activeGallery.value = response.personal_rough_category_id
    }
  }
}

function openCreateCategory() {
  categoryEditorMode.value = 'create'
  editingCategory.value = null
  categoryParentId.value = ''
  Object.assign(categoryForm, {
    name: '',
    description: '',
    visibility: categoryParentId.value ? categoryMap.value[categoryParentId.value]?.visibility : materialScope.value,
    industry_slug: categoryParentId.value ? (categoryMap.value[categoryParentId.value]?.industry_slug || '') : '',
    design_style: '',
    building_name: '',
    area: ''
  })
  categoryEditorOpen.value = true
}

function openEditCategory(category) {
  categoryEditorMode.value = 'edit'
  editingCategory.value = category
  categoryParentId.value = category.parent_id || ''
  Object.assign(categoryForm, {
    name: category.name,
    visibility: category.visibility || 'private',
    description: category.description || '',
    industry_slug: category.industry_slug || '',
    design_style: category.design_style || '',
    building_name: category.building_name || '',
    area: category.area || ''
  })
  categoryEditorOpen.value = true
}

async function saveCategory() {
  if (!categoryForm.name.trim()) return message.warning('请输入名称')
  if (materialType.value === 'image' && !categoryParentId.value && !categoryForm.industry_slug) {
    return message.warning('请选择图库所属行业')
  }
  if (isDecorationGalleryChild.value && !categoryForm.design_style) {
    return message.warning('请选择设计风格')
  }
  if (isDecorationGalleryChild.value && !categoryForm.building_name.trim()) {
    return message.warning('请输入楼盘名称')
  }
  if (isDecorationGalleryChild.value && !categoryForm.area.trim()) {
    return message.warning('请输入面积')
  }
  const payload = {
    name: categoryForm.name.trim(),
    description: categoryForm.description.trim(),
    ...(!categoryParentId.value ? { visibility: categoryForm.visibility } : {}),
    ...(materialType.value === 'image' && !categoryParentId.value
      ? { industry_slug: categoryForm.industry_slug }
      : {}),
    ...(isDecorationGalleryChild.value
      ? {
          design_style: categoryForm.design_style,
          building_name: categoryForm.building_name.trim(),
          area: categoryForm.area.trim()
        }
      : {})
  }
  categorySaving.value = true
  try {
    if (categoryEditorMode.value === 'create') {
      await materialLibraryApi.createCategory({
        material_type: materialType.value,
        parent_id: categoryParentId.value || null,
        ...payload
      }, targetEmployeeId.value)
      message.success(materialType.value === 'image' ? '图库已创建' : '分类已创建')
    } else {
      const response = await materialLibraryApi.updateCategory(
        materialType.value, editingCategory.value.id, payload, targetEmployeeId.value
      )
      if (activeGallery.value === editingCategory.value.id) activeGallery.value = response.category.id
      materialScope.value = response.category.visibility || 'private'
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
      deleteTargetCategory.value || null,
      targetEmployeeId.value
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
    const response = await materialLibraryApi.listGalleries('', targetEmployeeId.value)
    await hydrateGalleries(response)
  } catch (error) {
    message.error(error.message || '图库加载失败')
  } finally {
    loading.value = false
  }
}

async function hydrateGalleries(response) {
  releasePreviews()
  industries.value = response.industries || []
  galleries.value = await Promise.all((response.galleries || []).map(async (gallery) => ({
    ...gallery,
    coverUrl: gallery.cover_item_id ? await blobPreview(gallery.cover_item_id, `gallery-${gallery.code}`) : ''
  })))
}

async function loadItems() {
  if (materialType.value === 'image' && materialScope.value === 'private' && !activeGallery.value) {
    return personalFolder.value ? loadPersonalItems() : loadPersonalFolders()
  }
  if (isGalleryRoot.value) return loadGalleries()
  loading.value = true
  try {
    const response = await materialLibraryApi.listItems({
      material_type: materialType.value,
      category: materialType.value === 'image' ? activeGallery.value : categoryFilter.value,
      query: query.value,
      sort: sort.value,
      page: page.value,
      page_size: 24,
      employee_id: targetEmployeeId.value
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

async function loadPersonalFolders() {
  loading.value = true
  try {
    const [folderResponse, galleryResponse] = await Promise.all([
      materialLibraryApi.listPersonalFolders(),
      materialLibraryApi.listGalleries('', targetEmployeeId.value),
      loadCategories()
    ])
    personalFolders.value = folderResponse.folders || []
    await hydrateGalleries(galleryResponse)
  } catch (error) {
    message.error(error.message || '我的素材加载失败')
  } finally {
    loading.value = false
  }
}

async function loadPersonalItems() {
  loading.value = true
  try {
    const response = await materialLibraryApi.listPersonalItems(personalFolder.value, personalPage.value)
    releasePreviews()
    personalItems.value = await Promise.all((response.items || []).map(async (item) => {
      const file = personalFolder.value === 'works'
        ? await materialLibraryApi.getWorkFile(item.id)
        : await materialLibraryApi.getItemThumbnail(item.id)
      const previewUrl = URL.createObjectURL(await file.blob())
      previewUrls.set(item.id, previewUrl)
      return { ...item, previewUrl }
    }))
    personalTotal.value = response.total || 0
  } catch (error) {
    message.error(error.message || '图库图片加载失败')
  } finally {
    loading.value = false
  }
}

function enterPersonalFolder(folder) {
  personalFolder.value = folder.id
  personalPage.value = 1
  personalItems.value = []
  void loadPersonalItems()
}

function openFixedFolderEditor(folder) {
  editingFixedFolder.value = folder
  fixedFolderName.value = folder.name
}

async function saveFixedFolderName() {
  const name = fixedFolderName.value.trim()
  if (!name) return message.warning('请输入图库名称')
  fixedFolderSaving.value = true
  try {
    await materialLibraryApi.renamePersonalFolder(editingFixedFolder.value.id, name)
    editingFixedFolder.value = null
    await loadPersonalFolders()
    message.success('图库名称已更新')
  } catch (error) {
    message.error(error.message || '保存失败')
  } finally {
    fixedFolderSaving.value = false
  }
}

function askDeleteFixedFolder(folder) {
  Modal.confirm({
    title: `删除“${folder.name}”入口`,
    content: '删除入口不会删除图库里的素材或文件；删除后该入口不会自动恢复。',
    okText: '删除入口',
    okType: 'danger',
    async onOk() {
      await materialLibraryApi.deletePersonalFolder(folder.id)
      await loadPersonalFolders()
      message.success('入口已删除，素材和文件已保留')
    }
  })
}

function leavePersonalFolder() {
  personalFolder.value = ''
  personalItems.value = []
  void loadPersonalFolders()
}

function search() {
  if (isGalleryRoot.value) return
  query.value = queryInput.value.trim()
  page.value = 1
  void loadItems()
}

function enterGallery(gallery) {
  activeGallery.value = gallery.code
  designStyleFilter.value = ''
  selectedShareItemIds.value = []
  query.value = ''
  queryInput.value = ''
  page.value = 1
  void loadItems()
}

function leaveGallery() {
  if (isEmployeeRoughMode.value) {
    void router.push('/model-manage/employees')
    return
  }
  const targetGallery = currentGallery.value?.parent_id || ''
  activeGallery.value = targetGallery
  designStyleFilter.value = ''
  selectedShareItemIds.value = []
  items.value = []
  query.value = ''
  queryInput.value = ''
  if (targetGallery) void loadItems()
  else if (materialScope.value === 'private') void loadPersonalFolders()
  else void loadGalleries()
}

function categoryOptionLabel(category) {
  if (!category.parent_id) return `${category.visibility === 'enterprise' ? '[企业共享] ' : ''}${category.name}`
  return `${categoryMap.value[category.parent_id]?.name || '一级图库'} / ${category.name}`
}

function openUpload() {
  const personalTarget = personalFolder.value === 'rough' ? '毛坯房图库' : '我的上传'
  const fixedGallery = !activeGallery.value && materialScope.value === 'private' && materialType.value === 'image'
    ? uploadCategories.value.find((item) => item.name.startsWith(personalTarget))
    : null
  uploadCategory.value = activeGallery.value || fixedGallery?.id || uploadCategories.value[0]?.id || ''
  uploadDesignStyle.value = currentGallery.value?.design_style || ''
  uploadOpen.value = true
}

function resetUpload() {
  selectedFiles.value = []
  uploadCategory.value = ''
  uploadDesignStyle.value = ''
  uploadDragging.value = false
  uploadProgress.percent = 0
  uploadProgress.loaded = 0
  uploadProgress.total = 0
  uploadProgress.phase = 'idle'
  if (fileInput.value) fileInput.value.value = ''
  if (folderInput.value) folderInput.value.value = ''
}

function isSupportedImageFile(file) {
  if (!file || !/\.(png|jpe?g|webp)$/i.test(file.name)) return false
  return !file.type || supportedImageTypes.has(file.type)
}

function attachRelativePath(file, relativePath) {
  if (!relativePath || file.webkitRelativePath) return file
  try {
    Object.defineProperty(file, 'webkitRelativePath', { configurable: true, value: relativePath })
  } catch {
    // File 在部分浏览器上不可扩展
  }
  return file
}

async function collectImagesFromDirectoryHandle(directoryHandle, prefix = '') {
  const files = []
  for await (const entry of directoryHandle.values()) {
    const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name
    if (entry.kind === 'file') {
      files.push(attachRelativePath(await entry.getFile(), relativePath))
    } else if (entry.kind === 'directory') {
      files.push(...await collectImagesFromDirectoryHandle(entry, relativePath))
    }
  }
  return files
}

async function collectFilesFromEntry(entry, prefix = '') {
  const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject))
    return [attachRelativePath(file, relativePath)]
  }
  if (!entry.isDirectory) return []
  const reader = entry.createReader()
  const children = []
  while (true) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
    if (!batch.length) break
    children.push(...batch)
  }
  const nested = []
  for (const child of children) {
    nested.push(...await collectFilesFromEntry(child, relativePath))
  }
  return nested
}

async function collectFilesFromDataTransfer(dataTransfer) {
  const entries = Array.from(dataTransfer?.items || [])
    .map((item) => item.webkitGetAsEntry?.())
    .filter(Boolean)
  if (!entries.length) return Array.from(dataTransfer?.files || [])
  const files = []
  for (const entry of entries) {
    files.push(...await collectFilesFromEntry(entry))
  }
  return files
}

function addScannedFolderFiles(fileList) {
  const images = Array.from(fileList || []).filter(isSupportedImageFile)
  if (!images.length) {
    message.warning('文件夹中没有可上传的 PNG、JPG、WebP 图片')
    return
  }
  addSelectedFiles(images)
}

async function chooseFiles() {
  if (typeof window.showOpenFilePicker === 'function') {
    try {
      const handles = await window.showOpenFilePicker({
        multiple: true,
        excludeAcceptAllOption: false,
        types: [
          {
            description: '图片',
            accept: {
              'image/png': ['.png'],
              'image/jpeg': ['.jpg', '.jpeg'],
              'image/webp': ['.webp']
            }
          }
        ]
      })
      addSelectedFiles(await Promise.all(handles.map((handle) => handle.getFile())))
      return
    } catch (error) {
      if (error?.name === 'AbortError') return
    }
  }
  fileInput.value?.click()
}

async function chooseFolder() {
  if (uploading.value) return
  if (typeof window.showDirectoryPicker === 'function') {
    try {
      const directory = await window.showDirectoryPicker({ mode: 'read' })
      addScannedFolderFiles(await collectImagesFromDirectoryHandle(directory, directory.name))
      return
    } catch (error) {
      if (error?.name === 'AbortError') return
    }
  }
  folderInput.value?.click()
}

function addSelectedFiles(fileList) {
  const files = Array.from(fileList || [])
  if (!files.length) return

  const next = [...selectedFiles.value]
  const knownFiles = new Set(next.map((file) => `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`))
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
    const identity = `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`
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
  if (oversized) warnings.push(`${oversized} 个文件超过 100 MB`)
  if (duplicated) warnings.push(`${duplicated} 个重复文件已忽略`)
  if (overflowed) warnings.push(`${overflowed} 个文件超出 ${uploadFileLimit.value} 张上限`)
  if (warnings.length) message.warning(warnings.join('；'))
}

function removeSelectedFile(index) {
  if (uploading.value) return
  selectedFiles.value = selectedFiles.value.filter((_, current) => current !== index)
}

function onFiles(event) {
  addSelectedFiles(event.target.files)
  event.target.value = ''
}

function onFolderFiles(event) {
  addScannedFolderFiles(event.target.files)
  event.target.value = ''
}

function onUploadDragEnter(event) {
  if (uploading.value) return
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

async function onUploadDrop(event) {
  uploadDragging.value = false
  if (uploading.value) return
  const entries = Array.from(event.dataTransfer?.items || [])
    .map((item) => item.webkitGetAsEntry?.())
    .filter(Boolean)
  const files = await collectFilesFromDataTransfer(event.dataTransfer)
  if (entries.some((entry) => entry.isDirectory)) addScannedFolderFiles(files)
  else addSelectedFiles(files)
}

async function uploadFiles() {
  if (!selectedFiles.value.length) return message.warning('请选择图片文件')
  if (!uploadCategory.value) return message.warning('请选择素材分类')
  if (isDecorationUpload.value && !uploadDesignStyle.value) return message.warning('请选择设计风格')
  uploading.value = true
  uploadProgress.percent = 0
  uploadProgress.loaded = 0
  uploadProgress.total = selectedFilesTotalBytes.value
  uploadProgress.phase = 'sending'
  const onProgress = (event) => {
    uploadProgress.percent = event.percent
    uploadProgress.loaded = event.loaded ?? uploadProgress.loaded
    uploadProgress.total = event.total ?? uploadProgress.total
    uploadProgress.phase = event.phase || uploadProgress.phase
  }
  try {
    let response
    if (materialType.value === 'image') {
      response = await materialLibraryApi.importImages(
        selectedFiles.value,
        uploadCategory.value,
        uploadDesignStyle.value,
        onProgress,
        targetEmployeeId.value
      )
    } else {
      response = await contentApi.importCoverPosterTemplates(selectedFiles.value, uploadCategory.value, onProgress)
      pendingReviewTemplates.value = (response.items || [])
        .map((result) => result.template)
        .filter((item) => item?.requires_review)
    }
    message.success(materialType.value === 'cover_template' && pendingReviewTemplates.value.length
      ? '模板上传成功，请校对 OCR 识别结果后启用'
      : (response?.summary?.queued ? '素材已提交入库，正在写入对象存储' : '素材上传成功'))
    uploadOpen.value = false
    const uploadedTo = response?.items?.[0]?.category || uploadCategory.value
    resetUpload()
    page.value = 1
    const shouldEnterUploadedGallery = materialType.value === 'image' &&
      !personalFolder.value &&
      (materialScope.value === 'enterprise' || Boolean(activeGallery.value))
    if (shouldEnterUploadedGallery && activeGallery.value !== uploadedTo) {
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
  }, targetEmployeeId.value)
  editOpen.value = false
  message.success('素材信息已更新')
  await loadItems()
}

async function downloadItem(item) {
  const response = personalFolder.value === 'works'
    ? await materialLibraryApi.getWorkFile(item.id)
    : await materialLibraryApi.getItemFile(item.id, targetEmployeeId.value)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  const workExtension = blob.type === 'image/webp' ? 'webp' : blob.type === 'image/jpeg' ? 'jpg' : 'png'
  anchor.download = item.file_name || `${item.name}.${personalFolder.value === 'works' ? workExtension : 'png'}`
  anchor.click()
  URL.revokeObjectURL(url)
}

function removeItem(item) {
  Modal.confirm({
    title: `删除“${item.name}”`,
    content: item.metadata?.ever_shared
      ? '素材将从图库下架，已使用它的作品和任务仍可正常打开与导出。'
      : '删除后无法从素材库恢复。正在被内容任务或封面任务使用的素材不能删除。',
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await materialLibraryApi.deleteItem(item.id, targetEmployeeId.value)
      selectedShareItemIds.value = selectedShareItemIds.value.filter((itemId) => itemId !== item.id)
      message.success('素材已删除')
      await loadItems()
    }
  })
}

function selectedShareOrder(itemId) {
  const index = selectedShareItemIds.value.indexOf(itemId)
  return index === -1 ? null : index + 1
}

function toggleShareItem(itemId) {
  const index = selectedShareItemIds.value.indexOf(itemId)
  if (index === -1) selectedShareItemIds.value.push(itemId)
  else selectedShareItemIds.value.splice(index, 1)
}

async function copyShareUrl(url) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(url)
    return
  }
  const input = document.createElement('textarea')
  input.value = url
  input.setAttribute('readonly', '')
  input.style.position = 'fixed'
  input.style.opacity = '0'
  document.body.append(input)
  input.select()
  const copied = document.execCommand('copy')
  input.remove()
  if (!copied) throw new Error('浏览器未授予复制权限')
}

function currentPublicShareUrl(path) {
  if (/^https?:\/\//i.test(path)) return path
  const basePath = import.meta.env.BASE_URL.replace(/\/$/, '')
  const routePath = path.startsWith('/') ? path : `/${path}`
  return new URL(`${basePath}${routePath}`, window.location.origin).href
}

async function createShare() {
  if (!selectedShareItemIds.value.length) return message.warning('请先选择要分享的图片')
  shareCreating.value = true
  shareUrlForManualCopy.value = ''
  try {
    const response = await materialLibraryApi.createShare(selectedShareItemIds.value, targetEmployeeId.value)
    const shareUrl = currentPublicShareUrl(response.share.url || response.share.page_url || response.share.page_path)
    shareUrlForManualCopy.value = shareUrl
    await copyShareUrl(shareUrl)
    shareOpen.value = false
    message.success('链接已复制，请粘贴到微信或企业微信')
  } catch (error) {
    message.error(error.message || '链接复制失败，请手动复制下方链接')
  } finally {
    shareCreating.value = false
  }
}

function formatSize(bytes) {
  if (!bytes) return '0 KB'
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`
}

watch(materialType, async () => {
  activeGallery.value = ''
  designStyleFilter.value = ''
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

watch(materialScope, async () => {
  activeGallery.value = ''
  personalFolder.value = ''
  queryInput.value = ''
  personalPage.value = 1
  await loadItems()
})

watch(targetEmployeeId, async () => {
  targetEmployee.value = null
  activeGallery.value = ''
  items.value = []
  await loadCategories()
  await loadItems()
})

watch(uploadCategory, (id) => {
  const gallery = categoryMap.value[id]
  if (gallery?.design_style) uploadDesignStyle.value = gallery.design_style
})

const remoteSyncPhaseLabel = computed(() => {
  const phase = remoteSyncJob.value?.phase
  if (phase === 'listing') return '正在读取远程图库'
  if (phase === 'downloading') return '正在同步图片'
  if (phase === 'saving') return '正在写入本地素材库'
  return remoteSyncing.value ? '远程素材同步中' : ''
})
const remoteSyncCountLabel = computed(() => {
  const job = remoteSyncJob.value
  if (!job) return ''
  const done = job.processed ?? job.summary?.assets ?? 0
  const total = job.total || 0
  return total ? `${done}/${total}` : ''
})

function remoteErrorCode(error) {
  return error?.response?.data?.detail?.error?.code || ''
}

function openRemoteConfig(state, { resumeSync = true } = {}) {
  remoteConfigState.value = state
  remoteConfigForm.baseUrl = state.base_url || ''
  remoteConfigForm.username = ''
  remoteConfigForm.password = ''
  resumeRemoteSync.value = resumeSync
  remoteConfigOpen.value = true
}

function scheduleRemoteSyncPoll(jobId) {
  window.clearTimeout(remoteSyncPollTimer)
  remoteSyncPollTimer = window.setTimeout(() => pollRemoteSync(jobId), 2000)
}

async function performRemoteSync() {
  if (remoteSyncing.value) return
  remoteSyncing.value = true
  try {
    const response = await materialLibraryApi.syncRemote()
    remoteSyncJob.value = response.job
    message.success(response.reused ? '远程素材同步正在后台运行' : '远程素材同步任务已提交')
    pollRemoteSync(response.job.id)
  } catch (error) {
    const code = remoteErrorCode(error)
    if (userStore.isSuperAdmin && ['REMOTE_MATERIAL_CONFIG_REQUIRED', 'REMOTE_MATERIAL_AUTH_FAILED'].includes(code)) {
      openRemoteConfig(await materialLibraryApi.getRemoteConfig())
      message.warning(code === 'REMOTE_MATERIAL_AUTH_FAILED' ? '远程账号或密码已失效，请重新配置' : '请先配置远程素材库账号和密码')
      return
    }
    message.error(error.message || '远程素材同步失败，请稍后重试')
    remoteSyncing.value = false
  }
}

async function pollRemoteSync(jobId) {
  try {
    const response = await materialLibraryApi.getRemoteSyncStatus(jobId)
    const job = response.job
    if (!job) {
      remoteSyncing.value = false
      return
    }
    remoteSyncJob.value = job
    remoteSyncing.value = ['queued', 'running'].includes(job.status)
    if (remoteSyncing.value) {
      scheduleRemoteSyncPoll(job.id)
      return
    }
    if (job.status === 'succeeded') {
      message.success(`远程素材同步完成：${job.summary?.assets || 0} 张图片`)
      materialScope.value = 'enterprise'
      activeGallery.value = ''
      await loadCategories()
      await loadGalleries()
      return
    }
    if (job.status === 'failed') {
      if (userStore.isSuperAdmin && ['REMOTE_MATERIAL_CONFIG_REQUIRED', 'REMOTE_MATERIAL_AUTH_FAILED'].includes(job.error_code)) {
        openRemoteConfig(await materialLibraryApi.getRemoteConfig())
      }
      message.error(job.error_message || '远程素材同步失败，请稍后重试')
    }
  } catch (error) {
    remoteSyncing.value = false
    message.error(error.message || '远程素材同步状态读取失败')
  }
}

async function restoreRemoteSync() {
  try {
    const response = await materialLibraryApi.getRemoteSyncStatus()
    if (response.job && ['queued', 'running'].includes(response.job.status)) {
      remoteSyncJob.value = response.job
      remoteSyncing.value = true
      scheduleRemoteSyncPoll(response.job.id)
    }
  } catch {
    // 页面其他素材功能不依赖同步状态，恢复失败时允许用户重新点击。
  }
}

async function syncRemoteMaterials() {
  if (remoteSyncing.value) return
  remoteSyncing.value = true
  try {
    const state = await materialLibraryApi.getRemoteConfig()
    remoteConfigState.value = state
    if (!state.configured) {
      if (state.can_manage) openRemoteConfig(state)
      else message.error('远程素材库尚未配置，请联系超级管理员')
      return
    }
  } catch (error) {
    message.error(error.message || '远程素材库配置状态读取失败')
    return
  } finally {
    remoteSyncing.value = false
  }
  await performRemoteSync()
}

async function saveRemoteConfig() {
  if ((remoteConfigState.value?.base_url_editable && !remoteConfigForm.baseUrl.trim()) || !remoteConfigForm.username.trim() || !remoteConfigForm.password.trim()) {
    message.warning('请填写远程素材库地址、账号和密码')
    return
  }
  remoteConfigSaving.value = true
  try {
    const payload = {
      username: remoteConfigForm.username.trim(),
      password: remoteConfigForm.password
    }
    if (remoteConfigState.value?.base_url_editable) payload.base_url = remoteConfigForm.baseUrl.trim()
    remoteConfigState.value = await materialLibraryApi.saveRemoteConfig(payload)
    const shouldResume = resumeRemoteSync.value
    remoteConfigForm.password = ''
    remoteConfigOpen.value = false
    resumeRemoteSync.value = false
    message.success('远程素材库账号验证并保存成功')
    if (shouldResume) await performRemoteSync()
  } catch (error) {
    message.error(error.message || '远程素材库账号验证失败')
  } finally {
    remoteConfigSaving.value = false
  }
}

function closeRemoteConfig() {
  remoteConfigForm.baseUrl = ''
  remoteConfigForm.username = ''
  remoteConfigForm.password = ''
  resumeRemoteSync.value = false
}

onMounted(restoreRemoteSync)
onBeforeUnmount(() => {
  window.clearTimeout(remoteSyncPollTimer)
  releasePreviews()
})
</script>

<template>
  <div class="material-library-view layout-container">
    <VisualWorkspaceHeader subtitle="集中管理设计可直接使用的图片和封面模板" />
    <PageHeader :title="isEmployeeRoughMode && targetEmployee ? `${targetEmployee.name}的素材库` : '素材库'" :tabs="tabs" :active-key="materialType" :loading="loading" show-border>
      <template #actions>
        <template v-if="materialType === 'image'">
          <a-button v-if="userStore.isAdmin" class="lucide-icon-btn" :loading="remoteSyncing" @click="syncRemoteMaterials">
            <RefreshCw :size="15" />同步远程素材
          </a-button>
          <a-button v-if="isGalleryRoot && !personalFolder && userStore.isAdmin" class="lucide-icon-btn" @click="openCreateCategory">
            <FolderPlus :size="15" />新建图库
          </a-button>
          <a-button v-if="isChildGallery" class="lucide-icon-btn" :disabled="!selectedShareItemIds.length" @click="shareOpen = true">
            <Share2 :size="15" />分享{{ selectedShareItemIds.length ? ` (${selectedShareItemIds.length})` : '' }}
          </a-button>
        </template>
        <a-button v-else class="lucide-icon-btn" @click="categoryManagerOpen = true">
          <Settings2 :size="15" />分类管理
        </a-button>
        <a-button v-if="canUploadCurrentLocation" type="primary" class="lucide-icon-btn" @click="openUpload">
          <Upload :size="15" />上传{{ materialType === 'image' ? '图片' : '模板' }}
        </a-button>
      </template>
    </PageHeader>

    <main class="material-content">
      <div v-if="remoteSyncing && remoteSyncJob" class="remote-sync-status">
        <div>
          <RefreshCw :size="16" class="remote-sync-spin" />
          <strong>{{ remoteSyncPhaseLabel }}</strong>
          <span>{{ remoteSyncCountLabel }}</span>
        </div>
        <a-progress :percent="remoteSyncJob.progress || 0" :show-info="false" size="small" />
      </div>
      <template v-if="materialType === 'image' && materialScope === 'private' && !activeGallery">
        <div class="context-head">
          <button v-if="personalFolder" type="button" class="back-button" @click="leavePersonalFolder"><ArrowLeft :size="16" />返回我的图库</button>
          <div>
            <a-radio-group v-if="!personalFolder" v-model:value="materialScope" button-style="solid">
              <a-radio-button value="private">我的素材</a-radio-button>
              <a-radio-button value="enterprise">企业共享</a-radio-button>
            </a-radio-group>
            <h2>{{ personalFolder ? personalFolderName : '我的图库' }}</h2>
            <p>按来源汇总我的图片和可使用的共享图片。</p>
          </div>
        </div>
        <div class="toolbar">
          <a-input v-if="!personalFolder" v-model:value="queryInput" allow-clear placeholder="搜索图库名称">
            <template #prefix><Search :size="15" /></template>
          </a-input>
          <a-button class="lucide-icon-btn" :loading="loading" @click="loadItems"><RefreshCw :size="15" />刷新</a-button>
        </div>
        <a-spin :spinning="loading">
          <template v-if="!personalFolder">
          <div class="gallery-section">
            <h3>固定图库</h3>
            <div class="gallery-grid">
              <article v-for="folder in filteredPersonalFolders" :key="folder.id" class="gallery-card">
                <button type="button" class="gallery-open" @click="enterPersonalFolder(folder)">
                  <span class="gallery-cover"><span class="folder-art"><Folder :size="44" /><i></i></span><em>{{ folder.count }} 张</em></span>
                  <span class="gallery-copy"><strong>{{ folder.name }}</strong><small>暂未填写图库说明</small><em>装修与家居</em></span>
                </button>
                <div v-if="userStore.isAdmin" class="gallery-actions">
                  <button type="button" :aria-label="`编辑图库 ${folder.name}`" title="编辑图库" @click="openFixedFolderEditor(folder)"><Pencil :size="15" /></button>
                  <button type="button" class="danger" :aria-label="`删除图库 ${folder.name}`" title="删除图库" @click="askDeleteFixedFolder(folder)"><Trash2 :size="15" /></button>
                </div>
              </article>
            </div>
          </div>
          <div v-for="group in personalGalleryGroups" :key="group.slug" class="gallery-section">
            <h3>个人图库 · {{ group.name }}</h3>
            <div class="gallery-grid">
              <article v-for="gallery in group.galleries" :key="gallery.id" class="gallery-card">
                <button type="button" class="gallery-open" @click="enterGallery(gallery)">
                  <span class="gallery-cover">
                    <img v-if="gallery.coverUrl" :src="gallery.coverUrl" alt="" />
                    <span v-else class="folder-art"><Folder :size="44" /><i></i></span>
                    <em>{{ gallery.count }} 张<span v-if="gallery.child_count"> · {{ gallery.child_count }} 个子图库</span></em>
                  </span>
                  <span class="gallery-copy"><strong>{{ gallery.name }}</strong><small>{{ gallery.description || '暂未填写图库说明' }}</small><em>{{ gallery.industry_name }}</em></span>
                </button>
                <div class="gallery-actions">
                  <button v-if="gallery.can_manage" type="button" :aria-label="`编辑图库 ${gallery.name}`" title="编辑图库" @click="openEditCategory(gallery)"><Pencil :size="15" /></button>
                  <button v-if="gallery.can_manage" type="button" class="danger" :aria-label="`删除图库 ${gallery.name}`" title="删除图库" @click="askDeleteCategory(gallery)"><Trash2 :size="15" /></button>
                </div>
              </article>
            </div>
          </div>
          </template>
          <div v-else class="material-section current-gallery-section">
            <div class="image-grid">
              <article v-for="item in personalItems" :key="item.id" class="material-card">
                <button type="button" class="preview-button" @click="previewItem = item"><img :src="item.previewUrl" :alt="item.name" /></button>
                <div class="material-info"><strong :title="item.name">{{ item.name }}</strong><small>{{ item.uploaded_at || '' }}</small></div>
                <div class="card-actions">
                  <button type="button" title="预览" @click="previewItem = item"><Eye :size="15" /></button>
                  <button type="button" title="下载" @click="downloadItem(item)"><Download :size="15" /></button>
                  <button v-if="item.can_manage" type="button" title="编辑名称和分类" @click="showEdit(item)"><Pencil :size="15" /></button>
                  <button v-if="item.can_manage" type="button" class="danger" title="删除" @click="removeItem(item)"><Trash2 :size="15" /></button>
                </div>
              </article>
            </div>
            <a-empty v-if="!loading && !personalItems.length" :image="false" description="当前图库还没有图片" />
          </div>
          <a-empty v-if="!loading && !personalFolder && !filteredPersonalFolders.length && !filteredPersonalGalleries.length" :image="false" description="没有匹配的图库" />
        </a-spin>
        <a-pagination v-if="personalFolder && personalTotal > 24" v-model:current="personalPage" :total="personalTotal" :page-size="24" show-less-items @change="loadPersonalItems" />
      </template>
      <template v-else>
      <div v-if="materialType === 'image'" class="context-head">
        <button v-if="activeGallery" type="button" class="back-button" @click="leaveGallery"><ArrowLeft :size="16" />{{ isEmployeeRoughMode ? '返回员工管理' : (parentGallery ? `返回${parentGallery.name}` : '返回图库') }}</button>
        <div>
          <a-radio-group v-if="isGalleryRoot && !isEmployeeRoughMode" v-model:value="materialScope" button-style="solid" @change="activeGallery = ''; page = 1">
            <a-radio-button value="private">我的素材</a-radio-button>
            <a-radio-button value="enterprise">企业共享</a-radio-button>
          </a-radio-group>
          <a-tag v-else>{{ isEmployeeRoughMode ? '员工个人素材' : (currentGallery?.visibility === 'enterprise' ? '企业共享' : (currentGallery?.is_global_personal ? '我的素材 · 全员可见' : '仅自己可见')) }}</a-tag>
          <h2>{{ isEmployeeRoughMode && targetEmployee ? `${targetEmployee.name} / 毛坯房图库` : (activeGallery ? currentGallery?.name : (materialScope === 'enterprise' ? '企业共享图库' : '我的图库')) }}</h2>
          <p v-if="parentGallery" class="gallery-path">{{ parentGallery.name }} / {{ currentGallery?.name }}</p>
          <p>{{ isEmployeeRoughMode && targetEmployee ? `员工编码：${targetEmployee.employee_code}；本页仅显示该员工个人毛坯房图库。` : (activeGallery ? (currentGallery?.description || '这个图库还没有填写说明。') : '我的素材包含四个固定入口和管理员发布的只读图库；企业共享图库保持现有使用规则。') }}</p>
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
          <div v-if="isDecorationGalleryPage" class="design-style-filter" role="tablist" aria-label="设计风格筛选">
            <button
              type="button"
              role="tab"
              :aria-selected="!designStyleFilter"
              :class="{ active: !designStyleFilter }"
              @click="designStyleFilter = ''"
            >全部</button>
            <button
              v-for="style in decorationGalleryStyles"
              :key="style"
              type="button"
              role="tab"
              :aria-selected="designStyleFilter === style"
              :class="{ active: designStyleFilter === style }"
              @click="designStyleFilter = style"
            >{{ style }}</button>
          </div>
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
              <button v-if="gallery.can_manage" type="button" :aria-label="`编辑图库 ${gallery.name}`" title="编辑图库" @click="openEditCategory(gallery)"><Pencil :size="15" /></button>
              <button v-if="gallery.can_manage && (!gallery.is_system || ['reference', 'rough', 'generated'].includes(gallery.image_design_role))" type="button" class="danger" :aria-label="`删除图库 ${gallery.name}`" title="删除图库" @click="askDeleteCategory(gallery)"><Trash2 :size="15" /></button>
            </div>
          </article>
          </div>
        </div>

        <div v-if="!isGalleryRoot && (items.length || isTopLevelGallery)" class="material-section current-gallery-section">
          <h3 v-if="isTopLevelGallery">当前图库图片</h3>
          <div :class="materialType === 'image' ? 'image-grid' : 'poster-wall'">
          <article v-for="item in items" :key="item.id" class="material-card" :class="{ poster: materialType === 'cover_template', 'is-share-selected': selectedShareOrder(item.id) }">
            <button
              v-if="materialType === 'image' && isChildGallery"
              type="button"
              class="share-select"
              :class="{ selected: selectedShareOrder(item.id) }"
              :aria-label="selectedShareOrder(item.id) ? `取消选择第 ${selectedShareOrder(item.id)} 张图片` : `选择图片 ${item.name}`"
              :title="selectedShareOrder(item.id) ? `已选第 ${selectedShareOrder(item.id)} 张，点击取消` : '选择图片'"
              @click="toggleShareItem(item.id)"
            >{{ selectedShareOrder(item.id) || '' }}</button>
            <button type="button" class="preview-button" @click="previewItem = item">
              <img :src="item.previewUrl" :alt="item.name" />
              <span v-if="materialType === 'cover_template'" class="poster-overlay"><b>{{ item.name }}</b><small>{{ item.category_name }}</small></span>
              <em v-if="materialType === 'cover_template'" class="template-status" :data-status="item.template_status">{{ templateStatusLabel(item) }}</em>
            </button>
            <div class="material-info">
              <strong v-if="materialType === 'image'" :title="item.name">{{ item.name }}</strong>
              <small>上传者 {{ item.uploaded_by_name }} · {{ item.category_name }}{{ item.design_style ? ` · ${item.design_style}` : '' }} · {{ item.width }}×{{ item.height }} · {{ formatSize(item.file_size) }}{{ item.storage_status === 'pending' ? ' · 入库中' : '' }}</small>
            </div>
            <div class="card-actions">
              <button type="button" title="预览" @click="previewItem = item"><Eye :size="15" /></button>
              <button v-if="materialType === 'cover_template'" type="button" title="校对 OCR 识别结果" @click="openOcrReview(item)"><ScanText :size="15" /></button>
              <button type="button" title="下载" @click="downloadItem(item)"><Download :size="15" /></button>
              <button v-if="item.can_manage" type="button" title="编辑名称和分类" @click="showEdit(item)"><Pencil :size="15" /></button>
              <button v-if="item.can_manage" type="button" class="danger" title="删除" @click="removeItem(item)"><Trash2 :size="15" /></button>
            </div>
          </article>
          </div>
        </div>

        <a-empty v-if="!loading && !filteredGalleries.length && (isGalleryRoot || !items.length)" :image="false" :description="isGalleryRoot ? '没有匹配的图库' : (query ? '未找到匹配素材' : (isTopLevelGallery ? '当前图库还没有图片或二级图库' : '当前图库还没有图片'))">
          <a-button v-if="!query && isGalleryRoot && userStore.isAdmin" type="primary" class="lucide-icon-btn" @click="openCreateCategory"><FolderPlus :size="15" />新建第一个图库</a-button>
          <a-button v-else-if="!query && canUploadCurrentLocation" type="primary" class="lucide-icon-btn" @click="openUpload"><ImagePlus :size="15" />上传第一份素材</a-button>
        </a-empty>
      </a-spin>
      <a-pagination v-if="!isGalleryRoot && total > 24" v-model:current="page" :total="total" :page-size="24" show-less-items @change="loadItems" />
      </template>
    </main>

    <a-modal
      v-model:open="remoteConfigOpen"
      title="配置远程素材库"
      :confirm-loading="remoteConfigSaving"
      ok-text="验证并保存"
      cancel-text="取消"
      @ok="saveRemoteConfig"
      @cancel="closeRemoteConfig"
    >
      <div class="remote-config-form">
        <p>配置全站共享的远程素材库凭据。远程地址仅首次配置时可修改；密码只用于服务端登录验证，不会在页面中回显。</p>
        <label><span>远程地址</span><a-input v-model:value="remoteConfigForm.baseUrl" :disabled="!remoteConfigState?.base_url_editable" :maxlength="500" placeholder="请输入 HTTP 或 HTTPS 地址" /></label>
        <label><span>账号</span><a-input v-model:value="remoteConfigForm.username" :maxlength="255" autocomplete="off" placeholder="请输入远程素材库账号" /></label>
        <label><span>密码</span><a-input-password v-model:value="remoteConfigForm.password" :maxlength="500" autocomplete="new-password" placeholder="请输入远程素材库密码" /></label>
      </div>
    </a-modal>

    <a-modal :open="Boolean(editingFixedFolder)" title="编辑图库名称" :confirm-loading="fixedFolderSaving" ok-text="保存" @ok="saveFixedFolderName" @cancel="editingFixedFolder = null">
      <a-input v-model:value="fixedFolderName" :maxlength="80" placeholder="图库名称" />
    </a-modal>

    <a-modal v-model:open="uploadOpen" :title="`上传${materialType === 'image' ? '素材图片' : '封面模板'}`" :confirm-loading="uploading" ok-text="开始上传" @ok="uploadFiles" @cancel="resetUpload">
      <div class="upload-form">
        <input ref="fileInput" type="file" multiple accept="image/png,image/jpeg,image/webp,image/*" hidden @change="onFiles" />
        <input ref="folderInput" type="file" webkitdirectory multiple accept="image/png,image/jpeg,image/webp,image/*" hidden @change="onFolderFiles" />
        <div
          class="upload-drop"
          :class="{ dragging: uploadDragging, disabled: uploading }"
          role="button"
          tabindex="0"
          @click="uploading ? undefined : chooseFiles()"
          @keydown.enter.prevent="uploading ? undefined : chooseFiles()"
          @dragenter.prevent="onUploadDragEnter"
          @dragover.prevent="onUploadDragOver"
          @dragleave="onUploadDragLeave"
          @drop.prevent="onUploadDrop"
        >
          <Upload :size="22" />
          <span>{{ uploadDragging ? '松开鼠标添加图片' : (selectedFiles.length ? `已选择 ${selectedFiles.length} 个文件，共 ${formatSize(selectedFilesTotalBytes)}，可继续拖入或点此再选` : '点击选择文件，或将文件/文件夹拖到此处') }}</span>
          <small>可一次多选文件，也可选择文件夹扫描其中的 PNG、JPG、WebP；将转为 WebP 后入库；单张不超过 100 MB；素材图片最多 50 张，封面模板最多 100 张</small>
          <button type="button" class="upload-folder-action" :disabled="uploading" @click.stop="chooseFolder">
            <Folder :size="14" />选择文件夹
          </button>
        </div>
        <ul v-if="selectedFiles.length" class="upload-file-list">
          <li v-for="(file, index) in selectedFiles" :key="`${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`">
            <span :title="file.webkitRelativePath || file.name">{{ file.webkitRelativePath || file.name }}</span>
            <small>{{ formatSize(file.size) }}</small>
            <button v-if="!uploading" type="button" title="移除" @click="removeSelectedFile(index)"><X :size="14" /></button>
          </li>
        </ul>
        <div v-if="uploading" class="upload-progress">
          <a-progress :percent="uploadProgress.percent" status="active" :show-info="true" />
          <small>{{ uploadProgressText }}</small>
        </div>
        <label><span>分类 <b>*</b></span><a-select v-model:value="uploadCategory" placeholder="请选择一个明确分类" :disabled="uploading">
          <a-select-option v-for="item in uploadCategories" :key="item.code" :value="item.code"><strong>{{ categoryOptionLabel(item) }}</strong> — {{ item.description }}</a-select-option>
        </a-select></label>
        <label v-if="isDecorationUpload"><span>设计风格 <b>*</b></span>
          <a-select v-model:value="uploadDesignStyle" placeholder="请选择设计风格" :disabled="uploading">
            <a-select-option v-for="style in decorationGalleryStyles" :key="style" :value="style">{{ style }}</a-select-option>
          </a-select>
        </label>
      </div>
    </a-modal>

    <a-modal :open="Boolean(previewItem)" :title="previewItem?.name" :footer="null" width="min(900px, 92vw)" @cancel="previewItem = null">
      <img v-if="previewItem" class="large-preview" :src="previewItem.previewUrl" :alt="previewItem.name" />
    </a-modal>

    <a-modal v-model:open="shareOpen" title="分享图库图片" :confirm-loading="shareCreating" ok-text="复制链接" @ok="createShare">
      <div class="share-form">
        <p>将分享 {{ selectedShareItemIds.length }} 张图片。链接长期有效，原图库图片删除后仍可查看本次分享的快照。</p>
        <small>点击“复制链接”后，请打开普通微信或企业微信，粘贴到个人会话或群聊。平台会自行抓取 H5 页面生成预览卡片。</small>
        <a-input v-if="shareUrlForManualCopy" :value="shareUrlForManualCopy" readonly aria-label="分享链接" />
      </div>
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
        <label v-if="materialType === 'image' && !categoryParentId && !editingCategory?.is_system"><span>可见范围</span>
          <a-radio-group v-model:value="categoryForm.visibility" :disabled="!canCreateShared">
            <a-radio value="private">我的素材（全员可见）</a-radio><a-radio value="enterprise">企业共享</a-radio>
          </a-radio-group>
          <small>放在“我的素材”的图库对所有用户显示，仅创建者可管理；企业共享保持现有使用规则。</small>
        </label>
        <label v-if="materialType === 'image' && !categoryParentId"><span>所属行业 <b>*</b></span><a-select v-model:value="categoryForm.industry_slug" placeholder="请选择一个行业">
          <a-select-option v-for="item in industries" :key="item.slug" :value="item.slug">{{ item.name }}</a-select-option>
        </a-select></label>
        <label><span>{{ categoryParentId ? '二级图库名称' : (materialType === 'image' ? '图库名称' : '分类名称') }} <b>*</b></span><a-input v-model:value="categoryForm.name" maxlength="80" :placeholder="categoryParentId ? '例如：客厅案例' : (materialType === 'image' ? '例如：春季新品素材' : '例如：客户案例')" /></label>
        <label v-if="isDecorationGalleryChild"><span>楼盘名称 <b>*</b></span><a-input v-model:value="categoryForm.building_name" maxlength="80" placeholder="请输入楼盘名称" /></label>
        <label v-if="isDecorationGalleryChild"><span>面积㎡ <b>*</b></span><a-input v-model:value="categoryForm.area" maxlength="32" placeholder="请输入面积" /></label>
        <label v-if="isDecorationGalleryChild"><span>设计风格 <b>*</b></span><a-select v-model:value="categoryForm.design_style" placeholder="请选择设计风格">
          <a-select-option v-for="style in decorationGalleryStyles" :key="style" :value="style">{{ style }}</a-select-option>
        </a-select></label>
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
.remote-sync-status { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(180px, 320px); align-items: center; gap: 18px; margin-bottom: 16px; padding: 11px 14px; border: 1px solid var(--main-100); border-radius: 10px; background: var(--main-20); }
.remote-sync-status > div { display: flex; align-items: center; gap: 8px; color: var(--color-text); }
.remote-sync-status span { color: var(--color-text-secondary); font-size: 12px; }
.remote-sync-spin { color: var(--color-primary); animation: remote-sync-rotate 1s linear infinite; }
@keyframes remote-sync-rotate { to { transform: rotate(360deg); } }
.remote-config-form { display: flex; flex-direction: column; gap: 16px; }
.remote-config-form p { margin: 0; color: var(--color-text-secondary); line-height: 1.6; }
.remote-config-form label { display: flex; flex-direction: column; gap: 7px; color: var(--color-text); font-weight: 500; }
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
.design-style-filter { display: flex; gap: 24px; margin: -2px 0 16px; overflow-x: auto; border-bottom: 1px solid var(--gray-100); white-space: nowrap; }
.design-style-filter button { position: relative; padding: 0 0 10px; border: 0; background: transparent; color: var(--color-text-secondary); cursor: pointer; font-size: 14px; }
.design-style-filter button::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; background: transparent; content: ''; }
.design-style-filter button:hover, .design-style-filter button.active { color: var(--color-primary); }
.design-style-filter button.active { font-weight: 600; }.design-style-filter button.active::after { background: var(--color-primary); }
.gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 18px; }
.gallery-card { position: relative; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 14px; background: var(--gray-0); transition: transform .18s, box-shadow .18s, border-color .18s; }
.gallery-card:hover { transform: translateY(-2px); border-color: var(--color-primary); box-shadow: 0 8px 24px rgb(20 35 70 / 10%); }
.gallery-open { display: block; width: 100%; padding: 0; text-align: left; border: 0; background: transparent; cursor: pointer; }
.gallery-cover { position: relative; display: grid; place-items: center; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; background: radial-gradient(circle at 25% 20%, var(--main-20), transparent 48%), linear-gradient(145deg, var(--gray-25), var(--gray-100)); color: var(--color-primary); }
.gallery-cover::after { position: absolute; inset: 0; background: linear-gradient(180deg, transparent 60%, rgb(15 25 45 / 10%)); content: ''; pointer-events: none; }
.gallery-cover img { width: 100%; height: 100%; object-fit: cover; transition: transform .25s; }.gallery-card:hover .gallery-cover img { transform: scale(1.035); }
.folder-art { position: relative; display: grid; place-items: center; width: 84px; height: 72px; border-radius: 20px; background: var(--gray-0); box-shadow: 0 12px 28px rgb(30 55 95 / 12%); }.folder-art i { position: absolute; right: 13px; bottom: 12px; width: 22px; height: 5px; border-radius: 3px; background: var(--main-100); }
.gallery-cover em { position: absolute; z-index: 1; right: 12px; bottom: 12px; padding: 4px 9px; border-radius: 14px; background: rgb(15 25 45 / 66%); color: white; font-size: 12px; font-style: normal; backdrop-filter: blur(4px); }
.gallery-copy { display: flex; flex-direction: column; gap: 4px; min-height: 78px; padding: 11px 76px 11px 14px; }
.gallery-copy strong { overflow: hidden; font-size: 16px; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text); }.gallery-copy small { overflow: hidden; color: var(--color-text-secondary); line-height: 1.45; text-overflow: ellipsis; white-space: nowrap; }
.gallery-copy em { color: var(--color-primary); font-size: 12px; font-style: normal; }
.gallery-actions { position: absolute; right: 10px; bottom: 10px; display: flex; gap: 3px; }.gallery-actions button, .category-row-actions button { display: grid; place-items: center; width: 30px; height: 30px; border: 0; border-radius: 7px; background: var(--gray-25); color: var(--color-text-secondary); cursor: pointer; }.gallery-actions button:hover, .category-row-actions button:hover { background: var(--main-20); color: var(--color-primary); }.gallery-actions button.danger:hover, .category-row-actions button.danger:hover { color: var(--color-error-700); }
.image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 16px; }
.poster-wall { columns: 260px; column-gap: 18px; }
.material-card { position: relative; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 9px; background: var(--gray-0); }
.material-card.is-share-selected { border-color: var(--main-color); }
.material-card.poster { break-inside: avoid; margin: 0 0 18px; }
.material-card:hover { border-color: var(--gray-300); box-shadow: 0 6px 20px rgb(20 35 70 / 9%); }
.preview-button { position: relative; display: block; width: 100%; height: 190px; overflow: hidden; padding: 0; border: 0; background: var(--gray-25); cursor: zoom-in; }
.preview-button img { width: 100%; height: 100%; object-fit: cover; transition: transform .2s; }
.material-card:hover .preview-button img { transform: scale(1.025); }
.share-select { position: absolute; z-index: 3; top: 10px; right: 10px; display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 1px solid var(--gray-300); border-radius: 999px; background: rgb(255 255 255 / 92%); color: var(--color-text-secondary); cursor: pointer; }
.share-select:hover { border-color: var(--main-color); color: var(--main-color); }.share-select.selected { border-color: var(--main-color); background: var(--main-color); color: var(--gray-0); font-weight: 600; }
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
.upload-drop.disabled { cursor: default; opacity: .72; pointer-events: none; }
.upload-drop small { color: var(--color-text-tertiary); text-align: center; }
.upload-folder-action { display: inline-flex; align-items: center; gap: 6px; margin-top: 6px; padding: 6px 12px; border: 1px solid var(--gray-300); border-radius: 8px; background: var(--gray-0); color: var(--color-text); cursor: pointer; }
.upload-folder-action:hover { border-color: var(--main-color); color: var(--main-color); }
.upload-drop.disabled .upload-folder-action { cursor: default; }
.upload-file-list { display: flex; flex-direction: column; max-height: 180px; margin: 0; padding: 0; overflow: auto; border: 1px solid var(--gray-150); border-radius: 8px; list-style: none; }
.upload-file-list li { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--gray-100); }
.upload-file-list li:last-child { border-bottom: 0; }
.upload-file-list span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--color-text); }
.upload-file-list small { color: var(--color-text-tertiary); }
.upload-file-list button { display: grid; place-items: center; width: 24px; height: 24px; padding: 0; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.upload-file-list button:hover { background: var(--gray-50); color: var(--color-error-700); }
.upload-progress { display: flex; flex-direction: column; gap: 6px; }
.upload-progress small { color: var(--color-text-secondary); }
.share-form { display: flex; flex-direction: column; gap: 14px; }.share-form p, .share-form small { margin: 0; color: var(--color-text-secondary); }.share-form small { font-size: 12px; }
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
