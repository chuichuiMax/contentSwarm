<script setup>
import PageHeader from '@/components/shared/PageHeader.vue'
</script>

<template>
  <div class="persona-manage-view">
    <PageHeader title="人设管理" :show-border="true" />
    <div class="persona-manage-content">
      <a-empty :image="false" description="暂无人设" />
    </div>
  </div>
</template>

<style scoped lang="less">
.persona-manage-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.persona-manage-content {
  padding: 80px var(--page-padding);
}
</style>
