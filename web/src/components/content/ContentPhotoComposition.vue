<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ImagePlus, GripVertical } from 'lucide-vue-next'
import { materialLibraryApi } from '@/apis/material_library_api'

const props = defineProps({
  modelValue: Object,
  primaryImageId: String,
  layouts: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:modelValue', 'update:primaryImageId', 'select'])
const urls = ref({})
const cropIndex = ref(null)
const dragging = ref(null)
let generation = 0
const layout = computed(() => props.layouts.find(item => item.id === props.modelValue?.layout_id))
const missing = computed(() => props.modelValue?.slots.filter(slot => !slot.image_item_id).length || 0)
const emptySlot = (id = null) => ({ image_item_id: id, focal_x: 0.5, focal_y: 0.5 })
const cellStyle = cell => ({ gridRow: `${cell.row + 1} / span ${cell.rowSpan}`, gridColumn: `${cell.col + 1} / span ${cell.colSpan}` })
function setLayout(id) {
  const next = props.layouts.find(item => item.id === id)
  const old = props.modelValue?.slots || []
  const primary = old.find(slot => slot.image_item_id === props.primaryImageId) || emptySlot(props.primaryImageId)
  const remaining = old.filter(slot => slot !== primary && slot.image_item_id !== props.primaryImageId)
  emit('update:modelValue', { layout_id: id, slots: next.cells.map((_, i) => i === 0 ? primary : remaining[i - 1] || emptySlot()) })
  cropIndex.value = null
}
function replaceSlots(slots) { emit('update:modelValue', { ...props.modelValue, slots }) }
function swap(from, to) {
  if (from === null || from === to) return
  const slots = [...props.modelValue.slots]
  ;[slots[from], slots[to]] = [slots[to], slots[from]]
  replaceSlots(slots)
  dragging.value = null
}
function setPrimary(index) {
  const id = props.modelValue.slots[index].image_item_id
  swap(index, 0)
  emit('update:primaryImageId', id)
}
function focal(key, value) {
  replaceSlots(props.modelValue.slots.map((slot, i) => i === cropIndex.value ? { ...slot, [key]: value } : slot))
}
watch(() => props.primaryImageId, id => {
  if (props.modelValue && !props.modelValue.slots.some(slot => slot.image_item_id === id)) {
    replaceSlots(props.modelValue.slots.map((slot, i) => i === 0 ? emptySlot(id) : slot))
  }
})
watch(() => props.modelValue?.slots.map(slot => slot.image_item_id).join('|'), async () => {
  const current = ++generation
  const next = {}
  await Promise.all([...new Set((props.modelValue?.slots || []).map(slot => slot.image_item_id).filter(Boolean))].map(async id => {
    try {
      const response = await materialLibraryApi.getItemThumbnail(id)
      next[id] = URL.createObjectURL(await response.blob())
    } catch { next[id] = '' }
  }))
  if (generation !== current) { Object.values(next).filter(Boolean).forEach(URL.revokeObjectURL); return }
  Object.values(urls.value).filter(Boolean).forEach(URL.revokeObjectURL)
  urls.value = next
}, { immediate: true })
onBeforeUnmount(() => { generation++; Object.values(urls.value).filter(Boolean).forEach(URL.revokeObjectURL) })
</script>

<template>
  <section class="photo-composition" aria-label="封面图片模式">
    <div class="composition-heading">
      <strong>图片模式</strong>
      <a-radio-group :value="modelValue ? 'composition' : 'single'" @change="event => event.target.value === 'single' ? emit('update:modelValue', null) : setLayout('grid-2')">
        <a-radio-button value="single">单图</a-radio-button>
        <a-radio-button value="composition" :disabled="!primaryImageId || !layouts.length">图片组合</a-radio-button>
      </a-radio-group>
      <small v-if="!primaryImageId">先从图库选择一张首图</small>
    </div>
    <template v-if="modelValue && layout">
      <div class="composition-layouts" aria-label="组合布局">
        <button v-for="item in layouts" :key="item.id" type="button" :class="{ selected: item.id === modelValue.layout_id }" @click="setLayout(item.id)">
          <span class="layout-icon" :style="{ gridTemplateColumns: `repeat(${item.cols}, 1fr)`, gridTemplateRows: `repeat(${item.rows}, 1fr)` }">
            <i v-for="(cell, index) in item.cells" :key="index" :style="cellStyle(cell)" />
          </span>{{ item.name }}
        </button>
      </div>
      <p>点击位置可一次插入多张图库图片并依次填入空位，也可拖动换位。设置首图后，它会进入布局的主要位置。</p>
      <div class="composition-slots" :style="{ gridTemplateColumns: `repeat(${layout.cols}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${layout.rows}, minmax(0, 1fr))`, aspectRatio: `${layout.cols} / ${layout.rows}` }">
        <div v-for="(slot, index) in modelValue.slots" :key="index" class="composition-slot" :style="cellStyle(layout.cells[index])" draggable="true" @dragstart="dragging = index" @dragend="dragging = null" @dragover.prevent @drop.prevent="swap(dragging, index)">
          <button class="slot-image" type="button" :aria-label="`选择组合图片 ${index + 1}`" @click="emit('select', index)">
            <img v-if="urls[slot.image_item_id]" :src="urls[slot.image_item_id]" :alt="`组合图片 ${index + 1}`" :style="{ objectPosition: `${slot.focal_x * 100}% ${slot.focal_y * 100}%` }" />
            <span v-else><ImagePlus :size="24" />{{ slot.image_item_id ? '点击更换图片' : '插入图片' }}</span>
          </button>
          <div class="slot-actions">
            <GripVertical :size="14" />
            <a-tag v-if="slot.image_item_id === primaryImageId" color="purple">首图</a-tag>
            <button v-else-if="slot.image_item_id" type="button" @click="setPrimary(index)">设为首图</button>
            <button v-if="slot.image_item_id" type="button" @click="cropIndex = cropIndex === index ? null : index">调整裁切</button>
          </div>
        </div>
      </div>
      <div v-if="cropIndex !== null" class="composition-crop">
        <strong>图片 {{ cropIndex + 1 }} · 裁切焦点</strong>
        <label>左右<a-slider :value="modelValue.slots[cropIndex].focal_x" :min="0" :max="1" :step="0.01" @change="value => focal('focal_x', value)" /></label>
        <label>上下<a-slider :value="modelValue.slots[cropIndex].focal_y" :min="0" :max="1" :step="0.01" @change="value => focal('focal_y', value)" /></label>
      </div>
      <a-alert v-if="missing" type="info" :message="`还需插入 ${missing} 张图片，填满后自动生成模板合成预览`" />
    </template>
  </section>
</template>

<style scoped lang="less">
.photo-composition { display: grid; gap: 16px; margin: 20px 0; }
.composition-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; }
p, small { color: var(--color-text-secondary); margin: 0; }
.composition-layouts { display: flex; flex-wrap: wrap; gap: 8px; button { background: var(--gray-0); border: 1px solid var(--gray-200); color: var(--color-text); border-radius: 8px; padding: 10px; display: grid; justify-items: center; gap: 8px; cursor: pointer; &.selected { border-color: var(--main-color); background: var(--main-10); } } }
.layout-icon { display: grid; gap: 3px; width: 36px; height: 32px; i { background: var(--gray-400); border-radius: 2px; } }
.composition-slots { display: grid; gap: 8px; max-width: 660px; }
.composition-slot { display: flex; min-height: 0; min-width: 0; flex-direction: column; border: 1px solid var(--gray-200); border-radius: 8px; overflow: hidden; }
.slot-image { flex: 1; min-height: 0; width: 100%; padding: 0; border: 0; background: var(--gray-25); cursor: pointer; img { width: 100%; height: 100%; object-fit: cover; display: block; } span { display: grid; justify-items: center; gap: 8px; color: var(--color-text-secondary); } }
.slot-actions { display: flex; gap: 4px; padding: 6px; align-items: center; flex-wrap: wrap; button { background: transparent; border: 0; color: var(--main-color); cursor: pointer; padding: 2px; } }
.composition-crop { max-width: 660px; display: grid; gap: 8px; label { display: grid; grid-template-columns: 40px 1fr; align-items: center; } }
</style>
