<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { contentApi } from '@/apis/content_api'
import { formatEvidenceReference } from '@/utils/contentEvidencePresentation'

const props = defineProps({ taskId: String, runStatus: String, fieldLabels: { type: Object, default: () => ({}) } })
const record = ref(null)
const loading = ref(false)
const source = ref(null)
const direction = computed(() => record.value?.decision?.strategy?.direction_code)
const directionName = computed(() => record.value?.direction_names?.[direction.value] || direction.value)
const labels = { goal: '目标', material: '资料', audience_scene: '受众场景', channel: '渠道', persona: '人设', strategy: '策略' }
const rows = computed(() => {
  const decision = record.value?.decision
  if (!decision) return []
  const strategy = decision.strategy
  return [
    ...strategy.title_assessments.map(item => ({ ...item, kind: '标题公式', name: record.value.candidate_names?.title_formulas?.[item.candidate_id] || '名称未留存', selected: item.candidate_id === strategy.title_formula_code })),
    ...strategy.body_assessments.map(item => ({ ...item, kind: '正文公式', name: record.value.candidate_names?.content_formulas?.[item.candidate_id] || '名称未留存', selected: item.candidate_id === strategy.body_formula_code })),
    ...strategy.method_assessments.map(item => ({ ...item, kind: '创作手法', name: record.value.candidate_names?.methods?.[item.candidate_id] || '名称未留存', selected: strategy.creation_method_codes.includes(item.candidate_id) }))
  ].filter(item => item.selected).map(item => ({ ...item, key: `${item.kind}:${item.candidate_id}` }))
})
const evidenceText = (path, index) => {
  const evidence = record.value?.input_evidence?.[path]
  return evidence ? formatEvidenceReference(evidence, props.fieldLabels, index) : '历史输入依据（原始资料未留存）'
}
async function refresh() {
  if (!props.taskId) return
  const taskId = props.taskId
  loading.value = true
  try { const result = await contentApi.getStrategyDecision(taskId); if (taskId === props.taskId) record.value = result }
  catch (error) { message.error(error.message || '读取策略决策失败') }
  finally { loading.value = false }
}
async function openSource() {
  try { source.value = (await contentApi.getViralAsset(record.value.decision.reference.selected_asset_id)).asset }
  catch (error) { message.error(error.message || '读取参考原文失败') }
}
watch(() => [props.taskId, props.runStatus], refresh, { immediate: true })
</script>

<template>
  <section v-if="record?.decision" class="strategy-decision">
    <h3>本次选择依据</h3>
    <a-alert v-if="record.automatic_direction && direction" class="automatic-direction" type="info" show-icon :message="`根据资料与参考结构，采用：${directionName}`" :description="record.decision.strategy.reason" />
    <template v-if="record?.decision">
    <p>{{ record.decision.strategy.strategy_mode === 'direction_scoped' ? '按本次采用方向匹配公式；创作手法独立评分。' : '按本次输入对行业公式和创作手法分别评分。' }}</p>
    <p v-if="!record.automatic_direction || !direction">{{ record.decision.strategy.reason }}</p>
    <a-alert v-if="record.decision.strategy.unresolved_questions.length || record.decision.reference.unresolved_questions.length" type="warning" :message="[...record.decision.strategy.unresolved_questions, ...record.decision.reference.unresolved_questions].join('；')" />
    <a-button v-if="record.decision.reference.selected_asset_id" size="small" @click="openSource">查看选中参考的完整原文</a-button>
    <details>
      <summary>已选公式与创作手法的依据</summary>
      <a-table :data-source="rows" row-key="key" size="small" :pagination="false" :scroll="{ x: 680 }">
        <a-table-column title="选择结果" width="240"><template #default="{ record: row }">{{ row.kind }} · {{ row.name }}<a-tag v-if="row.selected" color="green">已选</a-tag></template></a-table-column>
        <a-table-column title="结论" width="110"><template #default="{ record: row }">{{ !row.eligible ? '淘汰' : row.total == null ? '方向内匹配' : `${row.total} 分` }}</template></a-table-column>
        <a-table-column title="理由与维度"><template #default="{ record: row }"><p>{{ row.reason }}</p><small>{{ Object.entries(row.dimensions).map(([key, value]) => `${labels[key] || key} ${value}/4`).join(' · ') }}</small><details v-if="row.input_paths.length"><summary>输入依据</summary><ul><li v-for="(path, index) in row.input_paths" :key="path">{{ evidenceText(path, index) }}</li></ul></details></template></a-table-column>
      </a-table>
    </details>
    <small>评分按本次锁定标准计算；语义适配由 Agent 根据当前资料判断。</small>
    <a-drawer :open="Boolean(source)" title="参考原文" width="680" @close="source = null"><template v-if="source"><h3>{{ source.title }}</h3><p>{{ source.locator }} · {{ source.source.viral_basis }}</p><pre>{{ source.source.body }}</pre></template></a-drawer>
    </template>
  </section>
</template>

<style scoped lang="less">
.strategy-decision { margin-top: 18px; padding: 16px; border: 1px solid var(--gray-200); border-radius: 8px; display: grid; gap: 10px; p { margin: 0; } small { color: var(--color-text-tertiary); } summary { cursor: pointer; margin: 8px 0; } }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
