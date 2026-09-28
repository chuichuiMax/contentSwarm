<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, toRaw } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw, Plus, RotateCcw, Trash2 } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import { databaseApi } from '@/apis/knowledge_api'
import { useUserStore } from '@/stores/user'

import {
  CREATION_TYPE_NAMES as creationTypeNames,
  CREATION_TYPE_OPTIONS
} from '@/utils/content_creation_types'

const props = defineProps({ industries: { type: Array, default: () => [] } })
const userStore = useUserStore()
const items = ref([])
const fileJobs = ref([])
const loading = ref(false)
const submitting = ref(false)
const importOpen = ref(false)
const detail = ref(null)
const selectedRowKeys = ref([])
const rejectOpen = ref(false)
const rejectReason = ref('')
const correctionOpen = ref(false)
const correcting = ref(false)
const correctionForm = reactive({
  content_type_code: '',
  content_type_reason: '',
  required_slots: [],
  reason: ''
})
const databases = ref([])
const files = ref([])
const importType = ref()
const mappedDatabases = computed(() =>
  databases.value.filter((item) => item.contentType === importType.value)
)
function changeImportType() {
  form.kb_id = undefined
  form.file_ids = []
  files.value = []
}
const industryFilter = ref()
const form = reactive({ kb_id: undefined, file_ids: [] })
const statusNames = {
  pending: '等待准备',
  running: '准备中',
  ready: '可用',
  completed: '文章识别完成',
  needs_review: '需要处理',
  failed: '准备失败',
  invalidated: '已失效'
}
const industryOptions = computed(() =>
  props.industries.map((item) => ({ value: item.slug, label: item.name }))
)
let refreshTimer
let disposed = false

async function refresh() {
  clearTimeout(refreshTimer)
  loading.value = true
  try {
    const [result, jobs] = await Promise.all([
      contentApi.listViralAssets({ industry_slug: industryFilter.value }),
      contentApi.listViralFileJobs()
    ])
    items.value = result.items
    fileJobs.value = jobs.items
    if (
      !disposed &&
      [...items.value, ...fileJobs.value].some(
        (item) => ['pending', 'running'].includes(item.status) || item.preparing_count > 0
      )
    ) {
      refreshTimer = setTimeout(refresh, 5000)
    }
  } catch (error) {
    message.error(error.message || '读取参考资产失败')
  } finally {
    loading.value = false
  }
}
async function openImport() {
  try {
    const result = await databaseApi.getDatabases()
    databases.value = Object.values(result.databases || result).map((item) => ({
      value: item.kb_id,
      label: item.name,
      contentType: item.additional_params?.viral_content_type
    }))
    changeImportType()
    importOpen.value = true
  } catch (error) {
    message.error(error.message || '读取知识库失败')
  }
}
async function loadFiles(kbId) {
  form.file_ids = []
  files.value = []
  try {
    const result = await databaseApi.getDatabaseInfo(kbId)
    if (form.kb_id !== kbId) return
    files.value = Object.values(result.files || {})
      .filter((item) => !item.is_folder)
      .map((item) => ({ value: item.file_id, label: item.filename }))
  } catch (error) {
    message.error(error.message || '读取原文文件失败')
  }
}
async function submit() {
  if (!importType.value || !form.kb_id || !form.file_ids.length) {
    message.warning('请选择需要作为参考的文件')
    return
  }
  submitting.value = true
  try {
    const result = await contentApi.prepareViralFiles({ ...form })
    if (result.errors.length) message.warning(result.errors.map((item) => item.message).join('；'))
    if (result.items.length) message.success(`已提交 ${result.items.length} 个文件自动准备`)
    if (!result.errors.length) importOpen.value = false
    await refresh()
  } catch (error) {
    message.error(error.message || '准备失败')
  } finally {
    submitting.value = false
  }
}
async function retryFile(item) {
  try {
    const result = await contentApi.prepareViralFiles({
      kb_id: item.kb_id,
      file_ids: [item.file_id],
      retry: true
    })
    if (result.errors.length) message.warning(result.errors[0].message)
    await refresh()
  } catch (error) {
    message.error(error.message || '重试失败')
  }
}
const blueprintLabels = {
  title_pattern: '标题表达方式',
  title_slot_sequence: '标题组成顺序',
  opening_hook: '开头方式',
  content_block_sequence: '正文信息块顺序',
  narrative_structure: '叙述逻辑',
  paragraph_rhythm: '段落节奏',
  list_pattern: '列表形式',
  emoji_pattern: 'Emoji 使用',
  interaction_style: '互动方式'
}
const renderValue = (value) =>
  Array.isArray(value)
    ? value.map(renderValue).join(' → ')
    : value && typeof value === 'object'
      ? Object.values(value).map(renderValue).join('；')
      : String(value ?? '')
async function showDetail(item) {
  try {
    detail.value = (await contentApi.getViralAsset(item.id)).asset
  } catch (error) {
    message.error(error.message || '读取原文失败')
  }
}
async function retry(item) {
  try {
    await contentApi.retryViralAsset(item.id)
    await refresh()
  } catch (error) {
    message.error(error.message || '重试失败')
  }
}
async function remove(item) {
  try {
    await contentApi.deleteViralAsset(item.id)
    if (detail.value?.id === item.id) detail.value = null
    selectedRowKeys.value = selectedRowKeys.value.filter((id) => id !== item.id)
    message.success('爆款资产已删除')
    await refresh()
  } catch (error) {
    message.error(error.message || '删除爆款资产失败')
  }
}
async function review(item, action, reason = '') {
  try {
    const result = await contentApi.reviewViralAsset(item.id, { action, reason })
    detail.value = result.asset
    message.success(
      { approve: '审核通过', reject: '已驳回', disable: '已停用', enable: '已重新启用' }[action]
    )
    await refresh()
  } catch (error) {
    message.error(error.message || '资产状态更新失败')
  }
}
async function submitReject() {
  if (!rejectReason.value.trim()) {
    message.warning('请填写驳回原因')
    return
  }
  await review(detail.value, 'reject', rejectReason.value.trim())
  rejectOpen.value = false
  rejectReason.value = ''
}
function openCorrection() {
  const card = detail.value?.reference_card
  if (!card) return
  Object.assign(correctionForm, {
    content_type_code: card.content_type_code,
    content_type_reason: card.content_type_reason,
    required_slots: structuredClone(card.required_slots || []).map((slot) => ({
      slot_key: slot.slot_key,
      name: slot.name,
      description: slot.description,
      variable_codes: slot.variable_codes || [],
      match_mode: slot.match_mode || 'all',
      evidence_required: slot.evidence_required !== false,
      required: Boolean(slot.required)
    })),
    reason: ''
  })
  correctionOpen.value = true
}
async function submitCorrection() {
  if (
    !correctionForm.content_type_code ||
    !correctionForm.content_type_reason.trim() ||
    !correctionForm.reason.trim()
  ) {
    message.warning('请填写创作类型、分类依据和修正原因')
    return
  }
  if (
    correctionForm.required_slots.some(
      (slot) => !slot.slot_key || !slot.name || !slot.description || !slot.variable_codes.length
    )
  ) {
    message.warning('每个槽位都必须填写编码、名称、说明和变量')
    return
  }
  correcting.value = true
  try {
    const result = await contentApi.correctViralAsset(
      detail.value.id,
      structuredClone(toRaw(correctionForm))
    )
    detail.value = result.asset
    correctionOpen.value = false
    message.success('类型与槽位已修正，请审核后发布')
    await refresh()
  } catch (error) {
    message.error(error.message || '修正失败')
  } finally {
    correcting.value = false
  }
}
async function batchReprepare() {
  if (!selectedRowKeys.value.length) return
  try {
    const result = await contentApi.reprepareViralAssets(selectedRowKeys.value)
    message.success(`已提交 ${result.items.length} 个资产重新准备`)
    selectedRowKeys.value = []
    await refresh()
  } catch (error) {
    message.error(error.message || '批量重新准备失败')
  }
}
const rowSelection = computed(() =>
  userStore.isAdmin
    ? {
        selectedRowKeys: selectedRowKeys.value,
        onChange: (keys) => {
          selectedRowKeys.value = keys
        }
      }
    : null
)
onMounted(refresh)
onUnmounted(() => {
  disposed = true
  clearTimeout(refreshTimer)
})
</script>

<template>
  <div class="viral-assets">
    <p>
      上传知识库文件时勾选“用于爆款仿写参考”，系统会自动准备。已有文件也可以批量选择；无需填写文章排列方式或列名。
    </p>
    <div class="asset-toolbar">
      <a-select
        v-model:value="industryFilter"
        allow-clear
        placeholder="全部行业"
        :options="industryOptions"
        @change="refresh"
      />
      <a-button :loading="loading" @click="refresh"><RefreshCw :size="15" />刷新</a-button>
      <a-button v-if="userStore.isAdmin" type="primary" @click="openImport"
        ><Plus :size="15" />从已有文件准备</a-button
      >
      <a-button v-if="userStore.isAdmin" :disabled="!selectedRowKeys.length" @click="batchReprepare"
        ><RotateCcw :size="15" />批量重新准备</a-button
      >
    </div>
    <a-table
      v-if="fileJobs.length"
      :data-source="fileJobs"
      row-key="id"
      size="small"
      :pagination="{ pageSize: 5 }"
    >
      <a-table-column title="自动准备文件" data-index="filename" />
      <a-table-column title="进度"
        ><template #default="{ record }"
          ><a-tag>{{ statusNames[record.status] }}</a-tag
          ><span v-if="record.article_count">
            {{ record.ready_count }} / {{ record.article_count }} 篇可用</span
          >
          <div v-if="record.error_message" class="asset-error">
            {{ record.error_message }}
          </div></template
        ></a-table-column
      >
      <a-table-column title="操作"
        ><template #default="{ record }"
          ><a-button
            v-if="
              userStore.isAdmin && ['failed', 'needs_review', 'invalidated'].includes(record.status)
            "
            type="link"
            @click="retryFile(record)"
            >重新准备</a-button
          ></template
        ></a-table-column
      >
    </a-table>
    <a-table
      :data-source="items"
      :loading="loading"
      row-key="id"
      :row-selection="rowSelection"
      :pagination="{ pageSize: 10 }"
    >
      <a-table-column title="文章标题" data-index="title" />
      <a-table-column title="来源位置" data-index="locator" />
      <a-table-column title="状态"
        ><template #default="{ record }"
          ><a-tag :color="record.status === 'ready' ? 'green' : undefined">{{
            statusNames[record.status]
          }}</a-tag>
          <div v-if="record.error_message" class="asset-error">
            {{ record.error_message }}
          </div></template
        ></a-table-column
      >
      <a-table-column title="操作"
        ><template #default="{ record }"
          ><a-button type="link" @click="showDetail(record)">原文与蓝图</a-button
          ><a-button
            v-if="userStore.isAdmin && ['failed', 'needs_review'].includes(record.status)"
            type="link"
            @click="retry(record)"
            >重试准备</a-button
          ><a-popconfirm
            v-if="
              userStore.isAdmin && ['pending', 'failed', 'needs_review'].includes(record.status)
            "
            title="确认删除这条爆款资产？知识库原文件不会被删除。"
            ok-text="删除"
            cancel-text="取消"
            @confirm="remove(record)"
          >
            <a-button type="link" danger><Trash2 :size="14" />删除</a-button>
          </a-popconfirm></template
        ></a-table-column
      >
    </a-table>
    <a-modal
      v-model:open="importOpen"
      title="从已有文件准备参考"
      :confirm-loading="submitting"
      ok-text="自动准备"
      @ok="submit"
    >
      <p>选择已解析的文件，系统自动识别行业、标题和完整正文，并准备参考卡与结构蓝图。</p>
      <a-form layout="vertical">
        <a-form-item label="内容类型"
          ><a-select
            v-model:value="importType"
            :options="CREATION_TYPE_OPTIONS"
            @change="changeImportType"
        /></a-form-item>
        <a-form-item label="爆款知识库"
          ><a-select
            v-model:value="form.kb_id"
            :options="mappedDatabases"
            :disabled="!importType"
            placeholder="请选择同类型知识库"
            @change="loadFiles"
        /></a-form-item>
        <p v-if="importType && !mappedDatabases.length">
          没有对应的爆款知识库，请在知识库创建或编辑时绑定该内容类型。
        </p>
        <a-form-item label="原文文件"
          ><a-select
            v-model:value="form.file_ids"
            mode="multiple"
            :options="files"
            show-search
            option-filter-prop="label"
        /></a-form-item>
      </a-form>
    </a-modal>
    <a-drawer :open="Boolean(detail)" title="原文与准备结果" width="720" @close="detail = null">
      <template v-if="detail">
        <a-tag>{{ statusNames[detail.status] }}</a-tag>
        <h3>{{ detail.title }}</h3>
        <p>来源：{{ detail.kb_id }} / {{ detail.file_id }} / {{ detail.locator }}</p>
        <p>参考用途：{{ detail.source.viral_basis }}</p>
        <p v-if="detail.error_message">{{ detail.error_message }}</p>
        <div v-if="userStore.isAdmin" class="review-actions">
          <a-button
            v-if="detail.status === 'needs_review'"
            type="primary"
            @click="review(detail, 'approve')"
            >审核通过</a-button
          >
          <a-button v-if="detail.status === 'needs_review'" danger @click="rejectOpen = true"
            >驳回</a-button
          >
          <a-button v-if="detail.status === 'needs_review'" @click="openCorrection"
            >修正类型与槽位</a-button
          >
          <a-button
            v-if="detail.status === 'ready'"
            danger
            @click="review(detail, 'disable', '运营停用')"
            >停用</a-button
          >
          <a-button
            v-if="
              detail.status === 'invalidated' && detail.preparation?.review?.action === 'disable'
            "
            @click="review(detail, 'enable')"
            >重新启用</a-button
          >
        </div>
        <h4>完整正文</h4>
        <pre>{{ detail.source.body }}</pre>
        <template v-if="detail.reference_card">
          <h4>参考卡 · 判断是否适合本次创作</h4>
          <a-descriptions bordered :column="1" size="small">
            <a-descriptions-item label="创作类型">{{
              creationTypeNames[detail.reference_card.content_type_code] || '待重新准备分类'
            }}</a-descriptions-item>
            <a-descriptions-item label="分类依据">{{
              detail.reference_card.content_type_reason
            }}</a-descriptions-item>
            <a-descriptions-item label="目标受众">{{
              detail.reference_card.audience
            }}</a-descriptions-item>
            <a-descriptions-item label="使用场景">{{
              detail.reference_card.scene
            }}</a-descriptions-item>
            <a-descriptions-item label="内容目标">{{
              detail.reference_card.goal
            }}</a-descriptions-item>
            <a-descriptions-item label="渠道特征">{{
              detail.reference_card.channel
            }}</a-descriptions-item>
            <a-descriptions-item label="内容摘要">{{
              detail.reference_card.summary
            }}</a-descriptions-item>
          </a-descriptions>
          <h4>改写所需资料</h4>
          <ul>
            <li
              v-for="slot in detail.reference_card.required_slots"
              :key="slot.slot_key || slot.name"
            >
              <strong>{{ slot.slot_key || slot.name }}</strong> · {{ slot.name }}：{{
                slot.description
              }}（{{ slot.required ? '必需' : '可选' }}；变量
              {{ (slot.variable_codes || []).join('、') || '历史槽位' }}；{{
                slot.evidence_required ? '需要 Evidence' : '简报即可'
              }}）
            </li>
          </ul>
        </template>
        <template v-if="detail.preparation?.reference_blueprint">
          <h4>结构蓝图 · 指导选中后的创作</h4>
          <a-descriptions bordered :column="1" size="small"
            ><a-descriptions-item
              v-for="(label, key) in blueprintLabels"
              :key="key"
              :label="label"
              >{{ renderValue(detail.preparation.reference_blueprint[key]) }}</a-descriptions-item
            ></a-descriptions
          >
          <details>
            <summary>查看对应原文依据</summary>
            <div v-for="(anchors, key) in detail.preparation.blueprint_anchors" :key="key">
              <strong>{{ blueprintLabels[key] || key }}</strong>
              <blockquote v-for="(anchor, index) in anchors" :key="index">
                {{ anchor.quote }}
              </blockquote>
            </div>
          </details>
        </template>
        <template v-if="detail.preparation?.review_history?.length">
          <h4>审核与变更记录</h4>
          <a-timeline>
            <a-timeline-item
              v-for="(entry, index) in detail.preparation.review_history"
              :key="index"
            >
              {{ entry.reviewed_at }} ·
              {{
                {
                  approve: '通过',
                  reject: '驳回',
                  disable: '停用',
                  enable: '启用',
                  correct: '修正'
                }[entry.action] || entry.action
              }}
              <div v-if="entry.reason">{{ entry.reason }}</div>
            </a-timeline-item>
          </a-timeline>
        </template>
      </template>
    </a-drawer>
    <a-modal v-model:open="rejectOpen" title="驳回爆款资产" ok-text="确认驳回" @ok="submitReject">
      <a-textarea
        v-model:value="rejectReason"
        :rows="4"
        placeholder="说明需要修正的类型、槽位或蓝图问题"
      />
    </a-modal>
    <a-modal
      v-model:open="correctionOpen"
      title="修正参考类型与槽位"
      width="760px"
      :confirm-loading="correcting"
      ok-text="保存修正"
      @ok="submitCorrection"
    >
      <a-alert
        type="info"
        show-icon
        message="修正只改机器元数据，原文锚点和结构蓝图保持不变。如需增删槽位，请重新准备资产。"
      />
      <a-form layout="vertical" class="correction-form">
        <a-form-item label="内容类型"
          ><a-select
            v-model:value="correctionForm.content_type_code"
            :options="CREATION_TYPE_OPTIONS"
        /></a-form-item>
        <a-form-item label="分类依据"
          ><a-textarea v-model:value="correctionForm.content_type_reason" :rows="2"
        /></a-form-item>
        <div
          v-for="(slot, index) in correctionForm.required_slots"
          :key="index"
          class="correction-slot"
        >
          <div class="correction-slot-title">槽位 {{ index + 1 }}</div>
          <div class="correction-grid">
            <a-form-item label="槽位编码"><a-input v-model:value="slot.slot_key" /></a-form-item>
            <a-form-item label="名称"><a-input v-model:value="slot.name" /></a-form-item>
          </div>
          <a-form-item label="说明"><a-input v-model:value="slot.description" /></a-form-item>
          <a-form-item label="变量编码"
            ><a-select v-model:value="slot.variable_codes" mode="tags"
          /></a-form-item>
          <div class="correction-grid">
            <a-form-item label="匹配方式"
              ><a-select v-model:value="slot.match_mode"
                ><a-select-option value="all">全部满足</a-select-option
                ><a-select-option value="any">任一满足</a-select-option></a-select
              ></a-form-item
            >
            <a-form-item label="约束"
              ><a-checkbox v-model:checked="slot.required">必需槽位</a-checkbox
              ><a-checkbox v-model:checked="slot.evidence_required"
                >必须 Evidence</a-checkbox
              ></a-form-item
            >
          </div>
        </div>
        <a-form-item label="修正原因"
          ><a-textarea
            v-model:value="correctionForm.reason"
            :rows="3"
            placeholder="记录本次修正的原因"
        /></a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.viral-assets {
  color: var(--gray-800);
}
:deep(.ant-descriptions-item-label) {
  width: 112px;
  white-space: nowrap;
}
.asset-toolbar {
  display: flex;
  gap: 12px;
  margin: 16px 0;
  align-items: center;
  .ant-select {
    min-width: 180px;
  }
  .ant-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}
.asset-error {
  max-width: 320px;
  color: var(--gray-700);
  font-size: 12px;
}
.review-actions {
  display: flex;
  gap: 8px;
  margin: 14px 0;
}
.correction-form {
  margin-top: 16px;
}
.correction-slot {
  margin-bottom: 12px;
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.correction-slot-title {
  margin-bottom: 8px;
  font-weight: 600;
}
.correction-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--gray-50);
  padding: 12px;
  border-radius: 8px;
}
</style>
