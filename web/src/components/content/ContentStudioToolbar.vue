<script setup>
import { useRouter } from 'vue-router'
import { History, ScanText, Settings2, UserRoundCog } from 'lucide-vue-next'

defineProps({ hasTask: Boolean, isAdmin: Boolean })
const emit = defineEmits(['recognize-image'])
const router = useRouter()
</script>

<template>
  <nav class="content-studio-toolbar" aria-label="内容工具栏" @click.stop>
    <a-button type="text" size="small" @click="router.push('/content/accounts')">
      <UserRoundCog :size="16" />账号管理
    </a-button>
    <a-button
      type="text"
      size="small"
      :disabled="!hasTask"
      :title="hasTask ? '上传图片并识别文字' : '请先创建内容任务'"
      @click="emit('recognize-image')"
    ><ScanText :size="16" />图片识别</a-button>
    <a-button type="text" size="small" @click="router.push('/content/history')">
      <History :size="16" />生产历史
    </a-button>
    <a-button v-if="isAdmin" type="text" size="small" @click="router.push('/content/admin/rules')">
      <Settings2 :size="16" />创作规则库
    </a-button>
  </nav>
</template>

<style scoped lang="less">
.content-studio-toolbar {
  display: flex;
  width: 100%;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-bottom: 4px;

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 30px;
    color: var(--color-text-secondary);
  }

  :deep(.ant-btn:disabled) { color: var(--color-text-tertiary); }
}
</style>
