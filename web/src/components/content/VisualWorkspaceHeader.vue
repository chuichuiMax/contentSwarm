<script setup>
import { Image, PanelsTopLeft } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

defineProps({
  subtitle: { type: String, default: '设计、模板与品牌素材' }
})

const route = useRoute()
const router = useRouter()
const sections = [
  { label: '设计工作台', path: '/hycanvas', icon: PanelsTopLeft },
  { label: '素材库', path: '/materials/images', icon: Image }
]

const isActive = (path) => path === '/hycanvas'
  ? route.path.startsWith('/hycanvas')
  : route.path.startsWith('/materials')
</script>

<template>
  <header class="visual-workspace-header">
    <div class="visual-workspace-title">
      <PanelsTopLeft :size="20" />
      <strong>视觉创作</strong>
      <span>{{ subtitle }}</span>
    </div>
    <nav aria-label="视觉创作功能">
      <button
        v-for="section in sections"
        :key="section.path"
        type="button"
        :class="{ active: isActive(section.path) }"
        @click="router.push(section.path)"
      >
        <component :is="section.icon" :size="15" />
        {{ section.label }}
      </button>
    </nav>
    <div class="visual-workspace-actions"><slot name="actions" /></div>
  </header>
</template>

<style scoped lang="less">
.visual-workspace-header {
  display: grid;
  flex: 0 0 52px;
  grid-template-columns: minmax(220px, 1fr) auto minmax(220px, 1fr);
  align-items: center;
  gap: 16px;
  padding: 0 16px;
  border-bottom: 1px solid var(--gray-100);
  background: var(--main-1);
}

.visual-workspace-title,
.visual-workspace-header nav,
.visual-workspace-actions,
.visual-workspace-header button {
  display: flex;
  align-items: center;
}

.visual-workspace-title { gap: 8px; min-width: 0; }
.visual-workspace-title span { overflow: hidden; color: var(--gray-500); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.visual-workspace-header nav { gap: 4px; padding: 3px; border-radius: 10px; background: var(--gray-50); }
.visual-workspace-header button { gap: 6px; padding: 6px 11px; border: 0; border-radius: 8px; color: var(--gray-600); background: transparent; cursor: pointer; }
.visual-workspace-header button:hover { color: var(--main-700); }
.visual-workspace-header button.active { color: var(--main-700); background: var(--main-1); box-shadow: 0 1px 3px var(--shadow-1); }
.visual-workspace-actions { justify-content: flex-end; }

@media (max-width: 900px) {
  .visual-workspace-header { grid-template-columns: minmax(0, 1fr) auto; gap: 8px; padding: 0 10px; }
  .visual-workspace-title { display: none; }
  .visual-workspace-header nav { justify-self: start; }
}
</style>
