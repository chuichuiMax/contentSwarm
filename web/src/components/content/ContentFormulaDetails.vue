<script setup>
defineProps({ item: { type: Object, required: true } })
</script>

<template>
  <div class="formula-details">
    <details v-if="item.source_content?.cross_industry">
      <summary>通用应用说明（其他行业共用）</summary>
      <strong>{{ item.source_content.cross_industry.name }}</strong>
      <p>{{ item.source_content.cross_industry.core_goal }}</p>
      <p v-for="paragraph in item.source_content.cross_industry.structure_schema || []" :key="paragraph">{{ paragraph }}</p>
    </details>
    <template v-if="item.source_content?.core_goal"><strong>核心</strong><p>{{ item.source_content.core_goal }}</p></template>
    <strong>适用场景</strong><p>{{ item.suitable_scenes?.join('、') || '—' }}</p>
    <template v-if="item.source_content?.method_combinations?.length"><strong>适配创作手法</strong><p>{{ item.source_content.method_combinations.join('、') }}</p></template>
    <template v-if="item.source_content?.emotion_lexicon?.length"><strong>情绪词库</strong><p>{{ item.source_content.emotion_lexicon.join('、') }}</p></template>
    <strong>变量</strong>
    <p v-for="line in item.source_content?.variables || item.variable_schema || item.required_variables" :key="line">{{ line }}</p>
    <strong>参考案例</strong><p v-for="example in item.reference_examples" :key="example">{{ example }}</p>
    <a v-if="item.source_content?.source?.url" :href="item.source_content.source.url" target="_blank" rel="noopener noreferrer">查看飞书来源（{{ item.source_content.source.source_revision }}）</a>
  </div>
</template>

<style scoped lang="less">
.formula-details { line-height: 1.8; color: var(--color-text); white-space: normal; overflow-wrap: anywhere; }
p { margin: 4px 0 12px; white-space: pre-wrap; }
strong { color: var(--color-text-secondary); font-size: 12px; }
a { color: var(--main-700); font-size: 12px; }
</style>
