<script setup>
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { onMounted } from 'vue'

const agentStore = useAgentStore()
const userStore = useUserStore()
const themeStore = useThemeStore()

onMounted(async () => {
  if (userStore.isLoggedIn) {
    await agentStore.initialize()
  }
})
</script>
<template>
  <a-config-provider :theme="themeStore.currentTheme" :locale="zhCN">
    <div class="app-shell">
      <div class="brand-bar" aria-hidden="true"></div>
      <router-view />
    </div>
  </a-config-provider>
</template>

<style>
.app-shell {
  min-height: 100vh;
}

.brand-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--main-900), var(--main-700), var(--main-500));
  z-index: 9999;
  pointer-events: none;
}
</style>
