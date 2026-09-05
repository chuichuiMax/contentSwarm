<script setup>
import { computed } from 'vue'

defineOptions({ name: 'ContentExecutionPreview' })

const props = defineProps({
  value: { type: null, required: true },
  depth: { type: Number, default: 0 }
})

const labels = {
  content_brief: '业务简报',
  evidence_bundle: '已有证据',
  selected_direction_code: '内容方向',
  creation_method_codes: '创作手法',
  title_formula: '标题公式',
  body_formula: '正文公式',
  name: '名称',
  principle: '创作原则',
  reason: '选择理由',
  selection_reason: '选择理由',
  evidence_ids: '引用证据',
  core_goal: '公式目标',
  structure_schema: '正文结构',
  reference_examples: '参考示例',
  suitable_scenes: '适用场景',
  risk_rules: '风险规则',
  title: '生成标题',
  text: '内容',
  outline: '内容大纲',
  sections: '段落结构',
  goal: '段落目标',
  draft: '正文内容',
  body: '正文内容',
  topics: '推荐话题',
  checks: '检查结果',
  status: '状态',
  message: '检查说明',
  suggestion: '修改建议',
  review_report: '语义审核',
  validation_report: '规则校验',
  missing_variable_codes: '缺失资料',
  output_preview: '输出结果'
}

const hiddenKey = (key) =>
  ['snapshot_hash', 'runtime_config_snapshot', 'input_snapshot_hash'].includes(key) ||
  /(^|_)(id|ids|code|codes|version|version_id)$/.test(key)

const entries = computed(() =>
  props.value && typeof props.value === 'object' && !Array.isArray(props.value)
    ? Object.entries(props.value).filter(([key, value]) => !hiddenKey(key) && value !== null && value !== '')
    : []
)
const isObject = (value) => value && typeof value === 'object'
const displayLabel = (key) => labels[key] || key.replaceAll('_', ' ')
const scalarText = (value) => {
  if (value === true) return '是'
  if (value === false) return '否'
  return String(value ?? '')
}
</script>

<template>
  <div v-if="Array.isArray(value)" class="execution-array" :class="{ compact: value.every((item) => !isObject(item)) }">
    <template v-if="value.every((item) => !isObject(item))">
      <span v-for="(item, index) in value" :key="index">{{ scalarText(item) }}</span>
    </template>
    <div v-for="(item, index) in value" v-else :key="index" class="execution-array-item">
      <ContentExecutionPreview :value="item" :depth="depth + 1" />
    </div>
  </div>
  <div v-else-if="isObject(value)" class="execution-object" :class="{ nested: depth > 0 }">
    <section v-for="([key, item]) in entries" :key="key" class="execution-field">
      <strong>{{ displayLabel(key) }}</strong>
      <ContentExecutionPreview v-if="isObject(item)" :value="item" :depth="depth + 1" />
      <p v-else>{{ scalarText(item) }}</p>
    </section>
  </div>
  <p v-else class="execution-scalar">{{ scalarText(value) }}</p>
</template>

<style scoped lang="less">
.execution-object { display: flex; flex-direction: column; gap: 9px; }
.execution-object.nested { padding: 8px 10px; border-radius: 6px; background: var(--gray-0); }
.execution-field { min-width: 0; }
.execution-field > strong { display: block; margin-bottom: 3px; color: var(--color-text); font-size: 11px; }
.execution-field > p, .execution-scalar { margin: 0; color: var(--color-text-secondary); font-size: 11px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }
.execution-array { display: flex; flex-direction: column; gap: 7px; }
.execution-array.compact { flex-direction: row; flex-wrap: wrap; gap: 5px; }
.execution-array.compact span { padding: 2px 7px; border-radius: 999px; color: var(--color-text-secondary); background: var(--gray-50); font-size: 10px; }
.execution-array-item { padding-left: 8px; border-left: 2px solid var(--main-100); }
</style>
