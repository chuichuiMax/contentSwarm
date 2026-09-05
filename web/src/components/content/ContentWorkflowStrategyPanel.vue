<script setup>
import { computed } from 'vue'
import { CheckCircle2, Sigma } from 'lucide-vue-next'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  presentation: {
    type: Object,
    required: true
  },
  evidenceGroups: {
    type: Array,
    default: () => []
  }
})

const markdownCell = (value) =>
  String(value || '')
    .replaceAll('|', '\\|')
    .replace(/\r?\n/g, '<br>')

const strategyTableMarkdown = computed(() =>
  [
    '| 类型 | 编码 | 选择结果 | 使用目的 |',
    '| --- | --- | --- | --- |',
    ...(props.presentation.rows || []).map(
      (item) =>
        `| ${markdownCell(item.type)} | \`${markdownCell(item.code)}\` | ${markdownCell(item.name)} | ${markdownCell(item.purpose)} |`
    )
  ].join('\n')
)

const evidenceTables = computed(() =>
  props.evidenceGroups.map((group) => ({
    ...group,
    markdown: [
      '| 实际引用内容 | 来源文档 | 使用位置 |',
      '| --- | --- | --- |',
      ...group.rows.map(
        (item) =>
          `| ${markdownCell(item.value)} | ${markdownCell(item.source)} | ${markdownCell(item.usage)} |`
      )
    ].join('\n')
  }))
)
</script>

<template>
  <section
    v-if="presentation.formulaCodes?.length || evidenceGroups.length"
    class="workflow-strategy-panel"
  >
    <template v-if="presentation.formulaCodes?.length">
      <header class="strategy-section-heading">
        <span class="strategy-heading-icon"><Sigma :size="17" /></span>
        <div>
          <strong>内容生成公式</strong>
          <span>已锁定 {{ presentation.formulaCodes.length }} 项创作策略</span>
        </div>
      </header>

      <MarkdownPreview
        compact
        :content="presentation.formulaDescription"
        class="strategy-formula"
        aria-label="内容生成公式"
      />
      <div class="strategy-table-wrap">
        <MarkdownPreview compact :content="strategyTableMarkdown" class="strategy-table" />
      </div>
    </template>

    <template v-if="evidenceGroups.length">
      <header class="strategy-section-heading evidence-heading">
        <span class="strategy-heading-icon"><CheckCircle2 :size="17" /></span>
        <div>
          <strong>实际引用的知识库内容</strong>
          <span>仅展示最终标题和正文采用的资料</span>
        </div>
      </header>
      <div v-for="group in evidenceTables" :key="group.id" class="evidence-group">
        <div class="evidence-group-heading">
          <strong>{{ group.name }}</strong>
          <span>{{ group.rows.length }} 条引用</span>
        </div>
        <div class="strategy-table-wrap">
          <MarkdownPreview
            compact
            :content="group.markdown"
            class="strategy-table evidence-table"
          />
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped lang="less">
.workflow-strategy-panel {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}

.strategy-section-heading {
  display: flex;
  align-items: center;
  gap: 9px;
}

.strategy-section-heading > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.strategy-section-heading strong {
  color: var(--color-text);
  font-size: 14px;
  line-height: 1.5;
}

.strategy-section-heading span:not(.strategy-heading-icon) {
  color: var(--color-text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}

.strategy-heading-icon {
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-success-700);
  background: var(--color-success-50);
  border-radius: 6px;
}

.strategy-formula {
  min-height: 48px;
  padding: 10px 12px;
  background: var(--gray-25);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.strategy-formula :deep(p) {
  margin: 0;
}

.strategy-table-wrap {
  width: 100%;
  overflow-x: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.strategy-table {
  color: var(--color-text);
  font-size: 12px;
}

.strategy-table :deep(table) {
  width: 100%;
  min-width: 620px;
  margin: 0;
  border-radius: 0;
  outline: 0;
  table-layout: fixed;
}

.strategy-table :deep(th),
.strategy-table :deep(td) {
  padding: 9px 11px;
  text-align: left;
  line-height: 1.55;
  vertical-align: top;
  border-bottom: 1px solid var(--gray-100);
  overflow-wrap: anywhere;
}

.strategy-table :deep(th) {
  color: var(--color-text-secondary);
  background: var(--gray-25);
  font-weight: 600;
}

.strategy-table :deep(th:nth-child(1)) {
  width: 19%;
}
.strategy-table :deep(th:nth-child(2)) {
  width: 13%;
}
.strategy-table :deep(th:nth-child(3)) {
  width: 27%;
}
.strategy-table :deep(th:nth-child(4)) {
  width: 41%;
}
.strategy-table :deep(tbody tr:last-child td) {
  border-bottom: 0;
}
.strategy-table :deep(tbody tr:hover td) {
  background: var(--gray-10);
}

.strategy-table :deep(code) {
  color: var(--main-700);
  font-size: 12px;
  font-weight: 600;
}

.evidence-heading {
  margin-top: 8px;
}
.evidence-group {
  display: grid;
  gap: 7px;
}
.evidence-group-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  color: var(--color-text);
  font-size: 13px;
}
.evidence-group-heading span {
  flex: 0 0 auto;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.evidence-table :deep(table) {
  min-width: 560px;
}
.evidence-table :deep(th:nth-child(1)) {
  width: 58%;
}
.evidence-table :deep(th:nth-child(2)) {
  width: 22%;
}
.evidence-table :deep(th:nth-child(3)) {
  width: 20%;
}

@media (max-width: 600px) {
  .workflow-strategy-panel {
    margin-top: 14px;
  }
  .strategy-formula {
    padding-inline: 8px;
  }
}
</style>
