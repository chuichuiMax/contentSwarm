<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Database,
  FileEdit,
  GitBranch,
  Layers3,
  Pencil,
  Plus,
  Save,
  Search,
  Trash2
} from 'lucide-vue-next'
import ContentRuleEditorDrawer from '@/components/content/ContentRuleEditorDrawer.vue'
import { contentApi } from '@/apis/content_api'
import { useContentStudioStore } from '@/stores/contentStudio'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const store = useContentStudioStore()
const userStore = useUserStore()
const ruleVersions = ref([])
const industries = ref([])
const industryPacks = ref([])
const workflows = ref([])
const activeTab = ref('methods')
const selectedVersionId = ref('')
const ruleBundle = ref(null)
const savedSnapshot = ref('')
const validation = ref(null)
const searchText = ref('')
const loading = ref(false)
const saving = ref(false)
const editorOpen = ref(false)
const editorType = ref('methods')
const editingItem = ref(null)
const editingIndex = ref(-1)
const publishOpen = ref(false)
const publishNote = ref('')

const selectedVersion = computed(() =>
  ruleVersions.value.find((item) => item.id === selectedVersionId.value)
)
const publishedVersion = computed(() =>
  ruleVersions.value.find((item) => item.status === 'published' && item.schema_version === 3)
)
const draftVersion = computed(() =>
  ruleVersions.value.find((item) => item.status === 'draft' && item.schema_version === 3)
)
const canEdit = computed(() => userStore.isSuperAdmin && selectedVersion.value?.status === 'draft')
const coreMethods = computed(() =>
  (ruleBundle.value?.methods || []).filter((item) => item.method_type === 'core' && item.enabled)
)
const enabledTitles = computed(() =>
  (ruleBundle.value?.title_formulas || []).filter((item) => item.enabled)
)
const enabledBodies = computed(() =>
  (ruleBundle.value?.content_formulas || []).filter((item) => item.enabled)
)
const query = computed(() => searchText.value.trim().toLowerCase())

const filteredMethods = computed(() => filterItems(ruleBundle.value?.methods, ['code', 'name', 'principle']))
const filteredTitles = computed(() => filterItems(ruleBundle.value?.title_formulas, ['code', 'name', 'core_goal']))
const filteredBodies = computed(() => filterItems(ruleBundle.value?.content_formulas, ['code', 'name']))
const filteredCombinations = computed(() => filterItems(ruleBundle.value?.combination_rules, [
  'content_type_codes', 'title_formula_candidate_codes', 'body_formula_candidate_codes',
  'scenario_description', 'recommendation_reason'
]))

const withoutId = (item) => {
  const value = { ...item }
  delete value.id
  return value
}

const payloadFromBundle = (bundle) => ({
  changelog: bundle?.version?.changelog || '',
  methods: (bundle?.methods || []).map((item, index) => ({ ...withoutId(item), sort_order: index })),
  title_formulas: (bundle?.title_formulas || []).map((item, index) => ({ ...withoutId(item), sort_order: index })),
  content_formulas: (bundle?.content_formulas || []).map((item, index) => ({ ...withoutId(item), sort_order: index })),
  combination_rules: (bundle?.combination_rules || []).map(withoutId),
  content_types: (bundle?.content_types || []).map(withoutId),
  formula_patterns: (bundle?.formula_patterns || []).map((item) => ({
    ...withoutId(item),
    slots: (item.slots || []).map(withoutId)
  })),
  variables: (bundle?.variables || []).map(withoutId)
})

const currentSnapshot = computed(() => JSON.stringify(payloadFromBundle(ruleBundle.value)))
const isDirty = computed(() => Boolean(savedSnapshot.value) && currentSnapshot.value !== savedSnapshot.value)

function filterItems(items = [], fields = []) {
  if (!query.value) return items || []
  return (items || []).filter((item) => fields.some((field) => {
    const value = item[field]
    return (Array.isArray(value) ? value.join(' ') : String(value || '')).toLowerCase().includes(query.value)
  }))
}

const markSaved = () => {
  savedSnapshot.value = currentSnapshot.value
}

const loadVersion = async (versionId) => {
  loading.value = true
  try {
    const response = await contentApi.getAdminRuleBundle(versionId)
    selectedVersionId.value = versionId
    ruleBundle.value = response.bundle
    validation.value = response.validation || null
    searchText.value = ''
    markSaved()
  } finally {
    loading.value = false
  }
}

const load = async (force = false, preferredVersionId = '') => {
  loading.value = true
  try {
    await store.loadBootstrap(force)
    const [rules, templates, packs, flowList] = await Promise.all([
      contentApi.listRuleVersions(),
      contentApi.listIndustryTemplates(),
      contentApi.listIndustryPacks(),
      contentApi.listWorkflowTemplates()
    ])
    ruleVersions.value = rules.items || []
    industries.value = templates.items || []
    industryPacks.value = packs.items || []
    workflows.value = flowList.items || []
    const targetId = preferredVersionId
      || (userStore.isSuperAdmin ? draftVersion.value?.id : '')
      || publishedVersion.value?.id
      || ruleVersions.value[0]?.id
    if (targetId) await loadVersion(targetId)
  } catch (error) {
    message.error(error.message || '加载创作规则库失败')
  } finally {
    loading.value = false
  }
}

const nextPackStatus = (status) => ({
  draft: 'validated',
  validated: 'canary',
  canary: 'published',
  published: 'deprecated',
  deprecated: 'published'
})[status]

const packStatusText = (status) => ({
  draft: '草稿',
  validated: '已校验',
  canary: '灰度',
  published: '已发布',
  deprecated: '已停用'
})[status] || status

const canTransitionPack = (item) =>
  item.status !== 'canary' || item.evaluation_report?.regression?.passed === true

const packActionText = (item) =>
  item.status === 'canary' && !canTransitionPack(item)
    ? '等待全链路回归'
    : packStatusText(nextPackStatus(item.status))

const validatePack = async (item) => {
  try {
    const report = await contentApi.validateIndustryPack(item.id)
    item.evaluation_report = report
    if (report.validation.valid && report.evaluation.passed) message.success(`${item.name} 校验与离线评测通过`)
    else message.warning(`${item.name} 仍有 ${report.validation.errors.length} 项问题`)
  } catch (error) {
    message.error(error.message || '行业包校验失败')
  }
}

const transitionPack = async (item) => {
  const targetStatus = nextPackStatus(item.status)
  if (!targetStatus) return
  try {
    await contentApi.transitionIndustryPack(item.id, { target_status: targetStatus })
    const response = await contentApi.listIndustryPacks()
    industryPacks.value = response.items || []
    message.success(`${item.name} 已更新为${packStatusText(targetStatus)}`)
  } catch (error) {
    message.error(error.message || '行业包状态更新失败')
  }
}

const publishWorkflow = async (item) => {
  try {
    await contentApi.publishWorkflowVersion(item.id, {})
    const response = await contentApi.listWorkflowTemplates()
    workflows.value = response.items || []
    message.success(`${item.slug} v${item.version} 已发布`)
  } catch (error) {
    message.error(error.message || '工作流发布失败')
  }
}

const changeVersion = async (versionId) => {
  if (versionId === selectedVersionId.value) return
  if (isDirty.value && !window.confirm('当前草稿有未保存修改，切换版本会丢失这些修改。确定继续吗？')) return
  await loadVersion(versionId)
}

const createDraft = async () => {
  if (!publishedVersion.value) return
  loading.value = true
  try {
    const response = await contentApi.createRuleDraft({
      source_version_id: publishedVersion.value.id,
      changelog: `基于 v${publishedVersion.value.version} 调整创作规则`
    })
    await load(false, response.bundle.version.id)
    validation.value = response.validation
    message.success(`已创建 v${response.bundle.version.version} 草稿，可开始编辑`)
  } catch (error) {
    message.error(error.message || '创建规则草稿失败')
  } finally {
    loading.value = false
  }
}

const saveDraft = async () => {
  if (!canEdit.value || !ruleBundle.value) return
  saving.value = true
  try {
    const response = await contentApi.saveRuleDraft(selectedVersionId.value, payloadFromBundle(ruleBundle.value))
    ruleBundle.value = response.bundle
    validation.value = response.validation
    markSaved()
    await refreshVersionList()
    if (response.validation.errors.length) {
      message.warning(`草稿已保存，还有 ${response.validation.errors.length} 项发布校验问题`)
    } else {
      message.success('草稿已保存并通过发布校验')
    }
    return response
  } catch (error) {
    message.error(error.message || '保存规则草稿失败')
    throw error
  } finally {
    saving.value = false
  }
}

const refreshVersionList = async () => {
  const rules = await contentApi.listRuleVersions()
  ruleVersions.value = rules.items || []
}

const openEditor = (type, item = null, index = -1) => {
  editorType.value = type
  editingItem.value = item ? structuredClone(item) : null
  editingIndex.value = index
  editorOpen.value = true
}

const saveEditor = (value) => {
  const items = ruleBundle.value[editorType.value]
  if (editorType.value !== 'combination_rules' && editingIndex.value < 0) {
    const duplicate = items.some((item) => item.code.toUpperCase() === value.code.toUpperCase())
    if (duplicate) {
      message.error(`编码 ${value.code.toUpperCase()} 已存在，请使用其他编码`)
      return
    }
  }
  if (editingIndex.value >= 0) items[editingIndex.value] = { ...items[editingIndex.value], ...value }
  else {
    items.push(editorType.value === 'combination_rules'
      ? { ...value, id: `draft-${Date.now()}-${items.length}` }
      : value)
  }
  editorOpen.value = false
  message.success(editingIndex.value >= 0 ? '修改已加入草稿，请记得保存' : '规则已加入草稿，请记得保存')
}

const impactOfDelete = (type, item) => {
  const bundle = ruleBundle.value
  if (type === 'methods') {
    const titles = bundle.title_formulas.filter((entry) => entry.compatible_methods.includes(item.code)).length
    const bodies = bundle.content_formulas.filter((entry) => entry.compatible_methods.includes(item.code)).length
    const combinations = bundle.combination_rules.filter((entry) =>
      entry.method_members.some((member) => member.method_code === item.code)
    ).length
    return { count: titles + bodies + combinations, text: `将同步清理 ${titles} 个标题公式、${bodies} 个正文公式和 ${combinations} 条组合规则中的引用。` }
  }
  if (type === 'title_formulas') {
    const count = bundle.combination_rules.filter((entry) =>
      entry.title_formula_candidate_codes.includes(item.code)
    ).length
    return { count, text: `将同步清理 ${count} 条组合规则中的标题引用；没有其他标题可用的组合会一并删除。` }
  }
  if (type === 'content_formulas') {
    const count = bundle.combination_rules.filter((entry) =>
      entry.body_formula_candidate_codes.includes(item.code)
    ).length
    return { count, text: `将同步清理 ${count} 个组合组中的正文公式引用；候选池为空的组合会一并删除。` }
  }
  return { count: 0, text: '该组合规则会从当前草稿中删除。' }
}

const deleteItem = (type, item, index) => {
  if (type === 'methods' && item.code === 'S01') {
    message.warning('S01 场景增强是当前工作流依赖项，不能删除或停用')
    return
  }
  const impact = impactOfDelete(type, item)
  Modal.confirm({
    title: `从草稿删除“${item.name || item.scenario_description}”`,
    content: impact.text,
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => {
      const bundle = ruleBundle.value
      bundle[type].splice(index, 1)
      if (type === 'methods') {
        bundle.title_formulas.forEach((entry) => {
          entry.compatible_methods = entry.compatible_methods.filter((code) => code !== item.code)
        })
        bundle.content_formulas.forEach((entry) => {
          entry.compatible_methods = entry.compatible_methods.filter((code) => code !== item.code)
        })
        bundle.combination_rules = bundle.combination_rules
          .map((entry) => {
            const methodMembers = entry.method_members
              .filter((member) => member.method_code !== item.code)
              .map((member, memberIndex) => ({
                ...member,
                role: memberIndex === 0 ? 'primary' : 'supporting',
                order: memberIndex + 1
              }))
            return {
              ...entry,
              method_members: methodMembers,
              combination_type: ['single', 'double', 'triple', 'quadruple'][methodMembers.length - 1]
            }
          })
          .filter((entry) => entry.method_members.length)
      } else if (type === 'title_formulas') {
        bundle.combination_rules = bundle.combination_rules
          .map((entry) => ({
            ...entry,
            title_formula_candidate_codes: entry.title_formula_candidate_codes.filter((code) => code !== item.code)
          }))
          .filter((entry) => entry.title_formula_candidate_codes.length)
      } else if (type === 'content_formulas') {
        bundle.combination_rules = bundle.combination_rules
          .map((entry) => ({
            ...entry,
            body_formula_candidate_codes: entry.body_formula_candidate_codes.filter((code) => code !== item.code)
          }))
          .filter((entry) => entry.body_formula_candidate_codes.length)
      }
      message.success(`已从草稿删除${impact.count ? '并同步处理关联项' : ''}，请记得保存`)
    }
  })
}

const moveItem = (type, index, offset) => {
  const items = ruleBundle.value[type]
  const target = index + offset
  if (target < 0 || target >= items.length) return
  const [item] = items.splice(index, 1)
  items.splice(target, 0, item)
}

const toggleItem = (item) => {
  if (item.code === 'S01') {
    message.warning('S01 场景增强是当前工作流依赖项，必须保持启用')
    return
  }
  item.enabled = !item.enabled
}

const openPublish = () => {
  if (isDirty.value) {
    message.warning('请先保存当前修改，再发布规则版本')
    return
  }
  if (validation.value?.errors?.length) {
    message.warning('当前草稿仍有发布校验问题，请修正并重新保存')
    return
  }
  publishNote.value = ruleBundle.value?.version?.changelog || ''
  publishOpen.value = true
}

const publishDraft = async () => {
  try {
    await contentApi.publishRuleVersion(selectedVersionId.value, { note: publishNote.value.trim() || null })
    publishOpen.value = false
    await load(true)
    message.success('规则版本已发布，新创建的内容任务将使用该版本')
  } catch (error) {
    message.error(error.message || '发布规则版本失败')
  }
}

const discardDraft = () => {
  Modal.confirm({
    title: `放弃规则 v${selectedVersion.value?.version} 草稿`,
    content: '草稿中的所有增删改将被永久删除，已发布版本和历史任务不会受影响。',
    okText: '确认放弃',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await contentApi.discardRuleDraft(selectedVersionId.value)
        await load(false, publishedVersion.value?.id)
        message.success('草稿已放弃')
      } catch (error) {
        message.error(error.message || '放弃草稿失败')
      }
    }
  })
}

const beforeUnload = (event) => {
  if (!isDirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteLeave(() => !isDirty.value || window.confirm('当前草稿有未保存修改，确定离开吗？'))
onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload)
  load()
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <div class="rule-library-page">
    <header class="page-header">
      <div class="header-main">
        <button type="button" class="back-button" @click="router.back()"><ArrowLeft :size="18" /></button>
        <div><span>运营配置后台</span><h1>创作规则库</h1><p>在草稿中维护精确规则，发布后再供新任务使用。</p></div>
      </div>
      <span class="published-badge">线上生效 v{{ store.ruleBundle?.version?.version || '-' }}</span>
    </header>

    <div class="overview-grid">
      <div><Layers3 :size="20" /><strong>{{ ruleBundle?.methods?.filter(item => item.enabled).length || 0 }}</strong><span>启用的创作手法</span></div>
      <div><Database :size="20" /><strong>{{ enabledTitles.length }} / {{ enabledBodies.length }}</strong><span>启用的标题 / 正文公式</span></div>
      <div><GitBranch :size="20" /><strong>{{ ruleBundle?.combination_rules?.length || 0 }}</strong><span>V3 组合组</span></div>
    </div>

    <section class="workspace-bar">
      <div class="version-picker">
        <label>当前查看版本</label>
        <a-select :value="selectedVersionId" :loading="loading" style="width: 250px" @change="changeVersion">
          <a-select-option v-for="item in ruleVersions" :key="item.id" :value="item.id">
            v{{ item.version }} · {{ item.status === 'published' ? '已发布' : item.status === 'draft' ? '编辑草稿' : '历史版本' }}
          </a-select-option>
        </a-select>
        <span v-if="selectedVersion?.status === 'draft'" class="draft-badge"><FileEdit :size="14" />草稿</span>
        <span v-if="isDirty" class="unsaved-badge">有未保存修改</span>
      </div>
      <div class="workspace-actions">
        <a-button v-if="userStore.isSuperAdmin && !draftVersion" @click="createDraft"><FileEdit :size="16" />基于线上版本创建草稿</a-button>
        <template v-if="canEdit">
          <a-button danger @click="discardDraft">放弃草稿</a-button>
          <a-button :loading="saving" :disabled="!isDirty" @click="saveDraft"><Save :size="16" />保存修改</a-button>
          <a-button type="primary" :disabled="isDirty || validation?.errors?.length" @click="openPublish">发布版本</a-button>
        </template>
      </div>
    </section>

    <a-alert
      v-if="selectedVersion?.status !== 'draft'"
      class="status-alert"
      type="info"
      show-icon
      message="当前版本为只读"
      description="已发布和历史版本不会被直接修改。需要调整内容时，请基于线上版本创建编辑草稿。"
    />
    <a-alert
      v-else-if="isDirty"
      class="status-alert"
      type="warning"
      show-icon
      message="修改尚未保存"
      description="页面中的增删改只在浏览器内，点击“保存修改”后才会写入草稿。"
    />
    <div v-else-if="validation?.errors?.length" class="validation-panel validation-error">
      <AlertTriangle :size="18" />
      <div><strong>草稿已保存，但暂时不能发布</strong><p v-for="item in validation.errors" :key="`${item.code}-${item.path}`">{{ item.message }}</p></div>
    </div>
    <div v-else-if="validation" class="validation-panel validation-success">
      <CheckCircle2 :size="18" /><div><strong>草稿已保存并通过校验</strong><p>可以填写变更说明后发布。</p></div>
    </div>

    <section class="rule-card" :class="{ 'is-loading': loading }">
      <a-tabs v-model:activeKey="activeTab" @change="searchText = ''">
        <a-tab-pane key="methods" :tab="`创作手法 ${ruleBundle?.methods?.length || 0}`">
          <div class="tab-toolbar">
            <a-input v-model:value="searchText" allow-clear placeholder="搜索编码、名称或原则"><template #prefix><Search :size="15" /></template></a-input>
            <a-button v-if="canEdit" type="primary" @click="openEditor('methods')"><Plus :size="16" />新增手法</a-button>
          </div>
          <div class="rule-grid">
            <article v-for="item in filteredMethods" :key="item.code" :class="{ disabled: !item.enabled }">
              <div class="rule-heading"><code>{{ item.code }}</code><span>{{ item.method_type === 'core' ? '核心手法' : '增强器' }}</span><i>{{ item.enabled ? '启用' : '停用' }}</i></div>
              <h3>{{ item.name }}</h3><p>{{ item.principle }}</p>
              <div class="tag-list"><span v-for="scene in item.suitable_scenes" :key="scene">{{ scene }}</span></div>
              <div v-if="canEdit" class="item-actions">
                <a-switch :checked="item.enabled" :disabled="item.code === 'S01'" size="small" @change="toggleItem(item)" />
                <button type="button" class="lucide-icon-btn" title="上移" :disabled="ruleBundle.methods.indexOf(item) === 0" @click="moveItem('methods', ruleBundle.methods.indexOf(item), -1)"><ChevronUp :size="16" /></button>
                <button type="button" class="lucide-icon-btn" title="下移" :disabled="ruleBundle.methods.indexOf(item) === ruleBundle.methods.length - 1" @click="moveItem('methods', ruleBundle.methods.indexOf(item), 1)"><ChevronDown :size="16" /></button>
                <button type="button" class="lucide-icon-btn" title="编辑" @click="openEditor('methods', item, ruleBundle.methods.indexOf(item))"><Pencil :size="16" /></button>
                <button type="button" class="lucide-icon-btn danger" title="删除" :disabled="item.code === 'S01'" @click="deleteItem('methods', item, ruleBundle.methods.indexOf(item))"><Trash2 :size="16" /></button>
              </div>
            </article>
          </div>
        </a-tab-pane>

        <a-tab-pane key="titles" :tab="`标题公式 ${ruleBundle?.title_formulas?.length || 0}`">
          <div class="tab-toolbar">
            <a-input v-model:value="searchText" allow-clear placeholder="搜索编码、公式或目标"><template #prefix><Search :size="15" /></template></a-input>
            <a-button v-if="canEdit" type="primary" @click="openEditor('title_formulas')"><Plus :size="16" />新增标题公式</a-button>
          </div>
          <a-table :data-source="filteredTitles" row-key="code" :pagination="false" :scroll="{ x: 900 }">
            <a-table-column title="编码" data-index="code" width="80" />
            <a-table-column title="公式" data-index="name" width="190" />
            <a-table-column title="核心目标" data-index="core_goal" />
            <a-table-column title="兼容手法" width="180"><template #default="{ record }">{{ record.compatible_methods.join('、') || '-' }}</template></a-table-column>
            <a-table-column title="状态" width="74"><template #default="{ record }"><span :class="record.enabled ? 'status-on' : 'status-off'">{{ record.enabled ? '启用' : '停用' }}</span></template></a-table-column>
            <a-table-column v-if="canEdit" title="操作" width="200" fixed="right"><template #default="{ record }"><div class="table-actions"><a-switch :checked="record.enabled" size="small" @change="toggleItem(record)" /><button type="button" class="lucide-icon-btn" title="上移" :disabled="ruleBundle.title_formulas.indexOf(record) === 0" @click="moveItem('title_formulas', ruleBundle.title_formulas.indexOf(record), -1)"><ChevronUp :size="16" /></button><button type="button" class="lucide-icon-btn" title="下移" :disabled="ruleBundle.title_formulas.indexOf(record) === ruleBundle.title_formulas.length - 1" @click="moveItem('title_formulas', ruleBundle.title_formulas.indexOf(record), 1)"><ChevronDown :size="16" /></button><button type="button" class="lucide-icon-btn" title="编辑" @click="openEditor('title_formulas', record, ruleBundle.title_formulas.indexOf(record))"><Pencil :size="16" /></button><button type="button" class="lucide-icon-btn danger" title="删除" @click="deleteItem('title_formulas', record, ruleBundle.title_formulas.indexOf(record))"><Trash2 :size="16" /></button></div></template></a-table-column>
          </a-table>
        </a-tab-pane>

        <a-tab-pane key="bodies" :tab="`正文公式 ${ruleBundle?.content_formulas?.length || 0}`">
          <div class="tab-toolbar">
            <a-input v-model:value="searchText" allow-clear placeholder="搜索编码或公式名称"><template #prefix><Search :size="15" /></template></a-input>
            <a-button v-if="canEdit" type="primary" @click="openEditor('content_formulas')"><Plus :size="16" />新增正文公式</a-button>
          </div>
          <div class="rule-grid body-grid">
            <article v-for="item in filteredBodies" :key="item.code" :class="{ disabled: !item.enabled }">
              <div class="rule-heading"><code>{{ item.code }}</code><i>{{ item.enabled ? '启用' : '停用' }}</i></div>
              <h3>{{ item.name }}</h3><ol><li v-for="section in item.structure_schema" :key="section">{{ section }}</li></ol>
              <div v-if="canEdit" class="item-actions">
                <a-switch :checked="item.enabled" size="small" @change="toggleItem(item)" />
                <button type="button" class="lucide-icon-btn" :disabled="ruleBundle.content_formulas.indexOf(item) === 0" @click="moveItem('content_formulas', ruleBundle.content_formulas.indexOf(item), -1)"><ChevronUp :size="16" /></button>
                <button type="button" class="lucide-icon-btn" :disabled="ruleBundle.content_formulas.indexOf(item) === ruleBundle.content_formulas.length - 1" @click="moveItem('content_formulas', ruleBundle.content_formulas.indexOf(item), 1)"><ChevronDown :size="16" /></button>
                <button type="button" class="lucide-icon-btn" @click="openEditor('content_formulas', item, ruleBundle.content_formulas.indexOf(item))"><Pencil :size="16" /></button>
                <button type="button" class="lucide-icon-btn danger" @click="deleteItem('content_formulas', item, ruleBundle.content_formulas.indexOf(item))"><Trash2 :size="16" /></button>
              </div>
            </article>
          </div>
        </a-tab-pane>

        <a-tab-pane key="combinations" :tab="`组合矩阵 ${ruleBundle?.combination_rules?.length || 0}`">
          <div class="tab-toolbar">
          <a-input v-model:value="searchText" allow-clear placeholder="搜索内容方向、公式编码或适用场景"><template #prefix><Search :size="15" /></template></a-input>
            <a-button v-if="canEdit" type="primary" @click="openEditor('combination_rules')"><Plus :size="16" />新增组合</a-button>
          </div>
          <a-table :data-source="filteredCombinations" row-key="id" :pagination="false" :scroll="{ x: 980 }">
            <a-table-column title="内容方向" width="110"><template #default="{ record }">{{ record.content_type_codes.join('、') }}</template></a-table-column>
            <a-table-column title="组合类型" data-index="combination_type" width="100" />
            <a-table-column title="创作手法" width="160"><template #default="{ record }">{{ record.method_members.map(item => item.method_code).join(' + ') }}</template></a-table-column>
            <a-table-column title="标题候选池" width="190"><template #default="{ record }">{{ record.title_formula_candidate_codes.join('、') }}</template></a-table-column>
            <a-table-column title="正文候选池" width="150"><template #default="{ record }">{{ record.body_formula_candidate_codes.join('、') }}</template></a-table-column>
            <a-table-column title="适用场景" data-index="scenario_description" />
            <a-table-column title="优先级" data-index="priority" width="74" />
            <a-table-column v-if="canEdit" title="操作" width="100" fixed="right"><template #default="{ record }"><div class="table-actions"><button type="button" class="lucide-icon-btn" @click="openEditor('combination_rules', record, ruleBundle.combination_rules.indexOf(record))"><Pencil :size="16" /></button><button type="button" class="lucide-icon-btn danger" @click="deleteItem('combination_rules', record, ruleBundle.combination_rules.indexOf(record))"><Trash2 :size="16" /></button></div></template></a-table-column>
          </a-table>
        </a-tab-pane>

        <a-tab-pane key="industries" :tab="`行业包 ${industryPacks.length}`">
          <div class="readonly-note">行业包采用独立版本管理，并按“草稿 → 校验 → 灰度 → 发布”晋级；规则草稿不会直接修改行业包。</div>
          <div class="workflow-list industry-pack-list">
            <article v-for="item in industryPacks" :key="item.id">
              <div><code>{{ item.id }}</code><h3>{{ item.name }}</h3></div>
              <span>{{ packStatusText(item.status) }}</span>
              <p>{{ Object.keys(item.content_type_aliases || {}).length }} 个方向 · {{ item.combination_overrides?.length || 0 }} 个组合组 · {{ item.golden_samples?.length || 0 }} 个黄金样本</p>
              <small>离线评测：{{ item.evaluation_report?.evaluation?.passed ? '通过' : '待校验' }} · canary 全链路回归：{{ item.evaluation_report?.regression?.passed ? '通过' : '待完成' }} · 最低覆盖率 {{ Math.round((item.minimum_coverage || 0) * 100) }}%</small>
              <div v-if="userStore.isSuperAdmin && item.schema_version === 3" class="pack-actions">
                <a-button size="small" @click="validatePack(item)">校验与评测</a-button>
                <a-button size="small" type="primary" :disabled="!canTransitionPack(item)" @click="transitionPack(item)">{{ packActionText(item) }}</a-button>
              </div>
            </article>
          </div>
          <details class="legacy-templates"><summary>查看行业表单模板（{{ industries.length }}）</summary>
            <div class="rule-grid"><article v-for="item in industries" :key="item.id"><code>{{ item.slug }} · v{{ item.version }}</code><h3>{{ item.name }}</h3><p>{{ item.description }}</p><small>{{ item.quick_form_schema.length }} 个简化字段 · {{ item.pro_form_schema.length }} 个专业字段</small></article></div>
          </details>
        </a-tab-pane>
        <a-tab-pane key="workflows" :tab="`工作流版本 ${workflows.length}`">
          <div class="readonly-note">工作流是独立的可执行版本，需要单独设计和发布。</div>
          <div class="workflow-list"><article v-for="item in workflows" :key="item.id"><div><code>{{ item.id }}</code><h3>{{ item.slug }} · v{{ item.version }}</h3></div><span>{{ item.status }}</span><p>{{ item.definition.nodes?.length || 0 }} 个节点 · {{ item.definition.edges?.length || 0 }} 条连线</p><small>定义哈希：{{ item.definition_hash || '草稿未冻结' }}</small><a-button v-if="userStore.isSuperAdmin && item.definition.schema_version === 3 && item.status !== 'published'" class="workflow-action" size="small" type="primary" @click="publishWorkflow(item)">校验并发布</a-button><details><summary>查看工作流 JSON 定义</summary><pre>{{ JSON.stringify(item.definition, null, 2) }}</pre></details></article></div>
        </a-tab-pane>
      </a-tabs>
    </section>

    <section class="version-list">
      <div class="section-heading"><div><h2>规则版本记录</h2><p>历史任务始终保留创建时绑定的规则版本。</p></div></div>
      <div v-for="item in ruleVersions" :key="item.id" :class="{ selected: item.id === selectedVersionId }">
        <strong>v{{ item.version }}</strong>
        <span class="version-status" :class="`version-${item.status}`">{{ item.status === 'published' ? '已发布' : item.status === 'draft' ? '草稿' : '已归档' }}</span>
        <span>{{ item.changelog }}</span><code>{{ item.id }}</code>
        <a-button size="small" @click="changeVersion(item.id)">查看</a-button>
      </div>
    </section>

    <ContentRuleEditorDrawer
      :open="editorOpen"
      :type="editorType"
      :item="editingItem"
      :method-options="coreMethods"
      :title-options="enabledTitles"
      :content-options="enabledBodies"
      :content-type-options="ruleBundle?.content_types || []"
      @close="editorOpen = false"
      @save="saveEditor"
    />

    <a-modal v-model:open="publishOpen" title="发布规则版本" ok-text="确认发布" cancel-text="取消" @ok="publishDraft">
      <a-alert type="warning" show-icon message="发布后，新创建的内容任务会立即使用该版本；历史任务不受影响。" />
      <label class="publish-label">变更说明</label>
      <a-textarea v-model:value="publishNote" :rows="4" placeholder="说明本次新增、删除或调整了哪些规则，方便后续回滚和审计" />
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.rule-library-page { min-height: 100vh; padding: 24px var(--page-padding) 48px; background: var(--gray-25); color: var(--color-text); }
.page-header, .overview-grid, .workspace-bar, .status-alert, .validation-panel, .rule-card, .version-list { max-width: 1180px; margin-left: auto; margin-right: auto; }
.page-header { margin-bottom: 18px; display: flex; justify-content: space-between; gap: 20px; }
.header-main { display: flex; gap: 10px; }
.back-button { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--gray-150); border-radius: 6px; background: var(--gray-0); color: var(--color-text); cursor: pointer; }
.header-main span { color: var(--main-700); font-size: 12px; }
.header-main h1 { margin: 2px 0; font-size: 24px; }
.header-main p { margin: 0; color: var(--color-text-secondary); }
.published-badge { align-self: flex-start; padding: 5px 10px; border-radius: 999px; background: var(--color-success-50); color: var(--color-success-700); font-size: 12px; }
.overview-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
.overview-grid > div { display: grid; grid-template-columns: auto 1fr; gap: 3px 10px; padding: 16px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.overview-grid svg { grid-row: 1 / 3; color: var(--main-700); }
.overview-grid strong { font-size: 18px; }
.overview-grid span { color: var(--color-text-secondary); }
.workspace-bar { min-height: 66px; padding: 14px 16px; display: flex; justify-content: space-between; align-items: center; gap: 16px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.version-picker, .workspace-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.version-picker label { color: var(--color-text-secondary); font-size: 13px; }
.draft-badge, .unsaved-badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 999px; font-size: 12px; }
.draft-badge { color: var(--main-700); background: var(--main-30); }
.unsaved-badge { color: var(--color-warning-900); background: var(--color-warning-50); }
.status-alert, .validation-panel { margin-top: 12px; }
.validation-panel { display: flex; gap: 10px; padding: 13px 16px; border: 1px solid; border-radius: 8px; }
.validation-panel strong { display: block; margin-bottom: 3px; }
.validation-panel p { margin: 2px 0; font-size: 13px; }
.validation-error { border-color: var(--color-error-200); background: var(--color-error-50); color: var(--color-error-700); }
.validation-success { border-color: var(--color-success-200); background: var(--color-success-50); color: var(--color-success-700); }
.rule-card, .version-list { padding: 20px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.rule-card { margin-top: 16px; }
.rule-card.is-loading { opacity: .68; pointer-events: none; }
.tab-toolbar { margin-bottom: 14px; display: flex; justify-content: space-between; gap: 12px; }
.tab-toolbar :deep(.ant-input-affix-wrapper) { max-width: 360px; }
.rule-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.rule-grid article { position: relative; min-height: 185px; padding: 16px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.rule-grid article.disabled { background: var(--gray-25); opacity: .72; }
.rule-heading { min-height: 22px; display: flex; align-items: center; gap: 7px; }
.rule-heading span, .rule-heading i, .status-on, .status-off { padding: 2px 6px; border-radius: 999px; font-size: 12px; font-style: normal; }
.rule-heading span { color: var(--main-700); background: var(--main-30); }
.rule-heading i, .status-on { margin-left: auto; color: var(--color-success-700); background: var(--color-success-50); }
.rule-grid article.disabled .rule-heading i, .status-off { color: var(--gray-600); background: var(--gray-100); }
.rule-grid h3 { margin: 7px 0; }
.rule-grid p, .rule-grid small, .rule-grid li { color: var(--color-text-secondary); }
.rule-grid ol { padding-left: 22px; }
.tag-list { display: flex; flex-wrap: wrap; gap: 5px; padding-bottom: 38px; }
.tag-list span { padding: 2px 6px; border-radius: 999px; background: var(--main-30); color: var(--main-700); font-size: 12px; }
.item-actions { position: absolute; left: 12px; right: 12px; bottom: 10px; display: flex; justify-content: flex-end; align-items: center; gap: 4px; padding-top: 8px; border-top: 1px solid var(--gray-100); }
.item-actions .ant-switch { margin-right: auto; }
.lucide-icon-btn.danger { color: var(--color-error-600); }
.table-actions { display: flex; align-items: center; justify-content: flex-end; gap: 4px; }
.readonly-note { margin-bottom: 14px; padding: 10px 12px; border-radius: 6px; background: var(--gray-25); color: var(--color-text-secondary); font-size: 13px; }
.workflow-list { display: flex; flex-direction: column; gap: 10px; }
.workflow-list article { display: grid; grid-template-columns: 1fr auto; padding: 14px; border: 1px solid var(--gray-150); border-radius: 8px; }
.workflow-list h3, .workflow-list p { margin: 3px 0; }
.workflow-list p { grid-column: 1 / -1; color: var(--color-text-secondary); }
.workflow-list small, .workflow-list details { grid-column: 1 / -1; }
.workflow-list small { color: var(--color-text-tertiary); overflow-wrap: anywhere; }
.workflow-list details { margin-top: 10px; }
.workflow-list summary { color: var(--main-700); cursor: pointer; font-size: 12px; }
.workflow-list pre { max-height: 360px; overflow: auto; padding: 12px; border-radius: 6px; background: var(--gray-25); font-size: 11px; white-space: pre-wrap; }
.pack-actions, .workflow-action { grid-column: 1 / -1; margin-top: 10px; }
.pack-actions { display: flex; gap: 8px; }
.legacy-templates { margin-top: 16px; }
.legacy-templates > summary { color: var(--main-700); cursor: pointer; }
.version-list { margin-top: 16px; }
.section-heading h2 { margin: 0; font-size: 17px; }
.section-heading p { margin: 4px 0 12px; color: var(--color-text-secondary); font-size: 13px; }
.version-list > div:not(.section-heading) { display: grid; grid-template-columns: 48px 72px 1fr auto 58px; align-items: center; gap: 12px; padding: 10px 8px; border-top: 1px solid var(--gray-100); }
.version-list > div.selected { background: var(--main-10); }
.version-status { padding: 2px 6px; border-radius: 999px; text-align: center; font-size: 12px; }
.version-published { color: var(--color-success-700); background: var(--color-success-50); }
.version-draft { color: var(--main-700); background: var(--main-30); }
.version-archived { color: var(--gray-600); background: var(--gray-100); }
.publish-label { display: block; margin: 18px 0 7px; font-weight: 600; }
@media (max-width: 900px) { .rule-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .workspace-bar { align-items: flex-start; flex-direction: column; } }
@media (max-width: 640px) { .overview-grid, .rule-grid { grid-template-columns: 1fr; } .page-header { align-items: flex-start; } .tab-toolbar { flex-direction: column; } .tab-toolbar :deep(.ant-input-affix-wrapper) { max-width: none; } .version-list > div:not(.section-heading) { grid-template-columns: 48px 1fr; } .version-list code, .version-list > div > span:nth-child(3) { grid-column: 1 / -1; } }
</style>
