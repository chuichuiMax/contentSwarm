<script setup>
import { DECORATION_GALLERY_STYLES } from '@/data/decorationGalleryStyles'

defineProps({
  modelValue: { type: String, default: '' },
  variant: { type: String, default: 'select' },
  placeholder: { type: String, default: '全部风格' },
  styles: { type: Array, default: () => DECORATION_GALLERY_STYLES }
})

const emit = defineEmits(['update:modelValue'])

function pick(style) {
  emit('update:modelValue', style)
}
</script>

<template>
  <a-select
    v-if="variant === 'select'"
    :value="modelValue || undefined"
    class="case-style-select"
    :placeholder="placeholder"
    allow-clear
    @update:value="pick($event || '')"
  >
    <a-select-option v-for="style in styles" :key="style" :value="style">
      {{ style }}
    </a-select-option>
  </a-select>
  <div v-else class="case-style-tabs" role="tablist" aria-label="设计风格筛选">
    <button type="button" role="tab" :aria-selected="!modelValue" :class="{ active: !modelValue }" @click="pick('')">
      全部
    </button>
    <button
      v-for="style in styles"
      :key="style"
      type="button"
      role="tab"
      :aria-selected="modelValue === style"
      :class="{ active: modelValue === style }"
      @click="pick(style)"
    >
      {{ style }}
    </button>
  </div>
</template>

<style scoped lang="less">
.case-style-select {
  width: 150px;
  flex-shrink: 0;
}

.case-style-tabs {
  display: flex;
  gap: 24px;
  margin: -2px 0 16px;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-100);
  white-space: nowrap;
}

.case-style-tabs button {
  position: relative;
  padding: 0 0 10px;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: 14px;
}

.case-style-tabs button::after {
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  background: transparent;
  content: '';
}

.case-style-tabs button:hover,
.case-style-tabs button.active {
  color: var(--color-primary);
}

.case-style-tabs button.active {
  font-weight: 600;
}

.case-style-tabs button.active::after {
  background: var(--color-primary);
}
</style>
