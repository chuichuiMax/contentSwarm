<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Copy } from 'lucide-vue-next'
import { contentApi } from '@/apis/content_api'
import { materialLibraryApi } from '@/apis/material_library_api'
import { readableGenerationSections } from '@/utils/readableGenerationPrompt'

const props = defineProps({
  log: { type: Object, default: null },
  taskId: { type: String, default: '' }
})

const imageUrl = ref('')
const promptView = ref('readable')
let loadSeq = 0

const readableSections = (log) => readableGenerationSections(log?.prompt)

const tokenText = (value) => (Number.isInteger(value) ? String(value) : '未返回')

const durationText = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)} 秒` : '未记录'

const samplingText = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? String(value) : '未记录'

const visibleKnowledgeHits = (log) =>
  (log?.knowledge_hits || []).filter((item) => item.role !== 'prompt_catalog')

const knowledgeLabel = (item) => {
  if (item.role === 'forbidden') return '封禁词库'
  if (item.role === 'audience_tags') return '通用标签'
  if (item.role === 'other') return '未绑定内容类型的知识库'
  return '内容知识库'
}

const knowledgeResult = (item) => {
  if (!item.hit) return '未命中'
  if (item.role === 'forbidden') return `已命中，${item.chars || 0} 条替换`
  if (item.role === 'audience_tags') return `已命中，补入 ${item.chars || 0} 个标签`
  return `已命中，读取 ${item.chars || 0} 字`
}

const copyText = async (value) => {
  try {
    await navigator.clipboard.writeText(value || '')
    message.success('已复制发给模型的内容')
  } catch {
    message.error('复制失败')
  }
}

const clearImage = () => {
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''
}

const loadImage = async (log) => {
  const seq = ++loadSeq
  clearImage()
  const itemId = log?.image_item_id
  const assetId = log?.image_asset_id
  if (!itemId && !assetId) return
  try {
    const response = itemId
      ? await materialLibraryApi.getItemFile(itemId)
      : await contentApi.getCoverAssetFile(assetId)
    const url = URL.createObjectURL(await response.blob())
    if (seq !== loadSeq) URL.revokeObjectURL(url)
    else imageUrl.value = url
  } catch {
    // 原图已删除时仍展示模型和文字。
  }
}

watch(() => props.log, loadImage, { immediate: true })
onBeforeUnmount(() => {
  loadSeq += 1
  clearImage()
})
</script>

<template>
  <a-empty v-if="!log" description="这次生成没有留下调用记录" />
  <div v-else class="generation-call-log">
    <dl>
      <div><dt>任务编码</dt><dd>{{ taskId || '未记录' }}</dd></div>
      <div><dt>使用模型</dt><dd>{{ log.model || '未记录' }}</dd></div>
      <div><dt>温度</dt><dd>{{ samplingText(log.temperature) }}</dd></div>
      <div><dt>采样范围</dt><dd>{{ samplingText(log.top_p) }}</dd></div>
      <div><dt>随机种子</dt><dd>{{ log.seed ?? '未记录' }}</dd></div>
      <div><dt>调用耗时</dt><dd>{{ durationText(log.elapsed_seconds) }}</dd></div>
      <div><dt>输入 Token</dt><dd>{{ tokenText(log.input_tokens) }}</dd></div>
      <div><dt>输出 Token</dt><dd>{{ tokenText(log.output_tokens) }}</dd></div>
      <div><dt>合计 Token</dt><dd>{{ tokenText(log.total_tokens) }}</dd></div>
    </dl>
    <section>
      <strong>参考图片</strong>
      <img v-if="imageUrl" :src="imageUrl" alt="本次发给模型的图片" />
      <p v-if="log.image_omitted_reason">{{ log.image_omitted_reason }}</p>
      <p v-else-if="!imageUrl">{{ log.image_attached ? '图片记录存在，但原图暂时无法打开' : '这次生成没有附带图片' }}</p>
    </section>
    <section v-if="visibleKnowledgeHits(log).length">
      <strong>知识库</strong>
      <ul>
        <li v-for="item in visibleKnowledgeHits(log)" :key="`${item.role || 'content'}-${item.name}`">
          {{ knowledgeLabel(item) }}「{{ item.name || '未命名' }}」{{ knowledgeResult(item) }}
        </li>
      </ul>
    </section>
    <section>
      <div class="log-heading">
        <strong>发给模型的内容</strong>
        <div class="log-actions">
          <button type="button" :class="{ active: promptView === 'readable' }" @click="promptView = 'readable'">易读</button>
          <button type="button" :class="{ active: promptView === 'raw' }" @click="promptView = 'raw'">原文</button>
          <a-button type="text" size="small" @click="copyText(log.prompt)"><Copy :size="14" />复制原文</a-button>
        </div>
      </div>
      <div v-if="promptView === 'readable'" class="readable-prompt">
        <article v-for="section in readableSections(log)" :key="section.title">
          <h3>{{ section.title }}</h3>
          <pre>{{ section.text }}</pre>
        </article>
        <p v-if="!readableSections(log).length">未记录发给模型的内容</p>
      </div>
      <pre v-else>{{ log.prompt || '未记录发给模型的内容' }}</pre>
    </section>
  </div>
</template>

<style scoped lang="less">
.generation-call-log { display: flex; flex-direction: column; gap: 14px; }
dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 0; }
dl div { min-width: 0; padding: 10px 12px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-25); }
dt { color: var(--color-text-tertiary); font-size: 12px; }
dd { margin: 4px 0 0; overflow-wrap: anywhere; color: var(--color-text); font-size: 13px; }
section strong { display: block; margin-bottom: 8px; }
img { display: block; max-width: 240px; max-height: 320px; object-fit: contain; border-radius: 6px; background: var(--gray-100); }
ul { margin: 0; padding-left: 18px; color: var(--color-text); font-size: 13px; line-height: 1.7; }
.log-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.log-actions { display: flex; align-items: center; gap: 6px; }
.log-actions > button { padding: 2px 8px; border: 1px solid var(--gray-150); border-radius: 6px; background: transparent; color: var(--color-text-secondary); font-size: 12px; cursor: pointer; }
.log-actions > button.active { border-color: var(--main-color); color: var(--main-color); background: var(--main-50, var(--gray-25)); }
.readable-prompt { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
.readable-prompt h3 { margin: 0 0 6px; color: var(--color-text-tertiary); font-size: 12px; font-weight: 600; }
.readable-prompt pre { max-height: 220px; margin: 0; }
pre { max-height: 280px; margin: 8px 0 0; padding: 12px; overflow: auto; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-25); white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; }
</style>
