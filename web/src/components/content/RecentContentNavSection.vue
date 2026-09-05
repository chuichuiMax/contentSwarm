<script setup>
import { ref } from 'vue'
import { ChevronDown, LoaderCircle } from 'lucide-vue-next'

defineProps({
  currentTaskId: {
    type: String,
    default: null
  },
  items: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  }
})

defineEmits(['select'])

const listCollapsed = ref(false)

const resultTitle = (item) => item.selected_title?.text || item.name || '未命名内容'
</script>

<template>
  <section class="recent-content-nav">
    <button
      type="button"
      class="recent-label"
      :aria-expanded="!listCollapsed"
      @click="listCollapsed = !listCollapsed"
    >
      <span>最近生成</span>
      <ChevronDown :size="14" class="collapse-icon" :class="{ collapsed: listCollapsed }" />
    </button>

    <div v-show="!listCollapsed" class="recent-list">
      <div v-if="loading && !items.length" class="recent-state">
        <LoaderCircle :size="15" class="spin" />
        <span>正在加载</span>
      </div>
      <template v-else-if="items.length">
        <button
          v-for="item in items"
          :key="item.id"
          type="button"
          class="recent-item"
          :class="{ active: currentTaskId === item.id }"
          :title="resultTitle(item)"
          @click="$emit('select', item.id)"
        >
          <span>{{ resultTitle(item) }}</span>
        </button>
      </template>
      <div v-else class="recent-state">暂无生成内容</div>
    </div>
  </section>
</template>

<style scoped lang="less">
.recent-content-nav {
  display: flex;
  min-height: 0;
  height: 100%;
  flex-direction: column;
  margin-top: 8px;
  overflow: hidden;
}

.recent-label {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 4px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-800);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  text-align: left;
}

.collapse-icon {
  transition: transform 0.2s ease;

  &.collapsed {
    transform: rotate(-90deg);
  }
}

.recent-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-width: thin;
}

.recent-item {
  display: flex;
  align-items: center;
  width: 100%;
  height: 36px;
  padding: 0 8px;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--gray-700);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition:
    background-color 0.2s ease,
    color 0.2s ease;

  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &:hover {
    background: var(--gray-50);
    color: var(--main-color);
  }

  &.active {
    background-color: color-mix(in srgb, var(--main-color) 8%, var(--gray-0));
    color: var(--main-color);
    font-weight: 600;
  }
}

.recent-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 16px;
  color: var(--gray-500);
  font-size: 12px;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
