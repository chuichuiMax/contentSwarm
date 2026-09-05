<script setup>
import { ref, onMounted, computed, provide, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  BarChart3,
  ClipboardList,
  Database,
  LibraryBig,
  Box,
  FolderKanban,
  FilePenLine,
  PanelLeftClose,
  PanelLeftOpen,
  MessageCirclePlus,
  Users,
  IdCard,
  SlidersHorizontal,
  Tags,
  UserRoundPen,
  ShieldCheck,
  Layers,
  PanelsTopLeft,
  Braces,
  ChevronRight,
  ChevronDown
} from 'lucide-vue-next'

import { useConfigStore } from '@/stores/config'
import { useAgentStore } from '@/stores/agent'
import { useChatThreadsStore } from '@/stores/chatThreads'
import { useChatUIStore } from '@/stores/chatUI'
import { useDatabaseStore } from '@/stores/database'
import { useInfoStore } from '@/stores/info'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import { useContentStudioStore } from '@/stores/contentStudio'
import { storeToRefs } from 'pinia'
import { contentApi } from '@/apis/content_api'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import DebugComponent from '@/components/DebugComponent.vue'
import TaskCenterDrawer from '@/components/TaskCenterDrawer.vue'
import SettingsModal from '@/components/SettingsModal.vue'
import ConversationNavSection from '@/components/ConversationNavSection.vue'
import RecentContentNavSection from '@/components/content/RecentContentNavSection.vue'

const configStore = useConfigStore()
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const chatUIStore = useChatUIStore()
const databaseStore = useDatabaseStore()
const infoStore = useInfoStore()
const taskerStore = useTaskerStore()
const userStore = useUserStore()
const contentStudioStore = useContentStudioStore()
const { activeCount: activeCountRef, isDrawerOpen } = storeToRefs(taskerStore)
const { threads, currentThreadId, hasMoreThreads, isLoadingMoreThreads } =
  storeToRefs(chatThreadsStore)

// Add state for debug modal
const showDebugModal = ref(false)

// Add state for settings modal
const showSettingsModal = ref(false)
const settingsInitialTab = ref('')
const recentContentTasks = ref([])
const recentContentLoading = ref(false)

const { sidebarCollapsed } = storeToRefs(chatUIStore)

// Provide settings modal methods to child components
const openSettingsModal = (tab) => {
  settingsInitialTab.value = tab || (userStore.isAdmin ? 'base' : 'account')
  showSettingsModal.value = true
}

// Handle debug modal close
const handleDebugModalClose = () => {
  showDebugModal.value = false
}

const getRemoteConfig = async () => {
  if (!userStore.isAdmin) return
  try {
    await configStore.refreshConfig()
  } catch (error) {
    console.warn('加载系统配置失败:', error)
  }
}

const getRemoteDatabase = async () => {
  try {
    await databaseStore.loadDatabases()
  } catch (error) {
    console.warn('加载知识库列表失败:', error)
  }
}

onMounted(async () => {
  // 加载信息配置与知识库数据无依赖，可并行
  await Promise.all([infoStore.loadInfoConfig(), getRemoteDatabase()])
  await initAgentNavigation()
  // 仅管理员加载系统配置和任务中心数据
  if (userStore.isAdmin) {
    await getRemoteConfig()
    taskerStore.loadTasks()
  }
})

const route = useRoute()
const router = useRouter()

const activeTaskCount = computed(() => activeCountRef.value || 0)
const activeConversationThreadId = computed(() => {
  return route.path.startsWith('/agent') ? currentThreadId.value : null
})
const isContentRoute = computed(() => route.path.startsWith('/content'))
const activeContentTaskId = computed(() => {
  if (!isContentRoute.value) return null
  return typeof route.params.taskId === 'string' ? route.params.taskId : null
})

// 下面是导航菜单部分，添加智能体项
const mainList = computed(() => {
  const items = [
    {
      name: '创建新对话',
      path: '/agent',
      icon: MessageCirclePlus,
      activeIcon: MessageCirclePlus,
      action: true,
      exactActive: true
    }
  ]

  items.push({
    name: '内容生产',
    path: '/content/new',
    activePaths: ['/content/new', '/content/tasks', '/content/history', '/content/results', '/content/accounts', '/content/admin'],
    icon: FilePenLine,
    activeIcon: FilePenLine
  })

  items.push({
    name: '视觉创作',
    path: '/hycanvas',
    activePaths: ['/hycanvas', '/materials'],
    icon: PanelsTopLeft,
    activeIcon: PanelsTopLeft
  })

  items.push({
    name: '图片打标',
    path: 'http://47.111.188.85:18081/',
    external: true,
    icon: Tags,
    activeIcon: Tags
  })

  items.push({
    name: '工作区',
    path: '/workspace',
    icon: FolderKanban,
    activeIcon: FolderKanban
  })

  if (userStore.isAdmin) {
    items.push({
      name: infoStore.kbMenuLabel,
      path: '/knowledge',
      activePaths: ['/knowledge'],
      icon: Database,
      activeIcon: Database
    })
  }

  items.push({
    name: '智能体扩展',
    path: '/extensions',
    activePaths: ['/extensions'],
    icon: LibraryBig,
    activeIcon: LibraryBig
  })

  items.push({
    name: '智能体管理',
    path: '/model-manage',
    icon: Box,
    activeIcon: Box,
    exactActive: true
  })

  items.push({
    name: '账号管理',
    path: '/model-manage/accounts',
    icon: Users,
    activeIcon: Users
  })

  items.push({
    name: '员工管理',
    path: '/model-manage/employees',
    icon: IdCard,
    activeIcon: IdCard
  })

  items.push({
    name: '配置管理',
    icon: SlidersHorizontal,
    activeIcon: SlidersHorizontal,
    activePaths: ['/config-manage'],
    children: [
      {
        name: '人设管理',
        path: '/config-manage/personas',
        icon: UserRoundPen,
        activeIcon: UserRoundPen
      },
      {
        name: '权限配置',
        path: '/config-manage/permissions',
        icon: ShieldCheck,
        activeIcon: ShieldCheck
      },
      {
        name: '内容类型配置',
        path: '/config-manage/content-types',
        icon: Layers,
        activeIcon: Layers
      },
      {
        name: '变量配置',
        path: '/config-manage/variables',
        icon: Braces,
        activeIcon: Braces
      }
    ]
  })

  if (userStore.isAdmin) {
    items.push({
      name: '数据总览',
      path: '/dashboard',
      icon: BarChart3,
      activeIcon: BarChart3
    })
  }

  return items
})

const expandedGroups = ref({ 配置管理: true })

const isNavItemActive = (item) => {
  const activePaths = item.activePaths || (item.path ? [item.path] : [])
  if (!activePaths.length) return false
  if (item.exactActive) {
    return activePaths.some((path) => route.path === path)
  }
  return activePaths.some((path) => route.path === path || route.path.startsWith(`${path}/`))
}

const isNavGroup = (item) => !!(item.children?.length && !item.path)

const isGroupExpanded = (item) => !!expandedGroups.value[item.name]

const toggleGroup = (item) => {
  expandedGroups.value = {
    ...expandedGroups.value,
    [item.name]: !expandedGroups.value[item.name]
  }
}

const setSidebarCollapsed = (collapsed) => {
  sidebarCollapsed.value = collapsed
}

const toggleSidebar = () => {
  setSidebarCollapsed(!sidebarCollapsed.value)
}

const initAgentNavigation = async () => {
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }
    await chatThreadsStore.loadThreads()
  } catch (error) {
    console.warn('加载对话导航失败:', error)
  }
}

const loadRecentContent = async () => {
  if (!isContentRoute.value || recentContentLoading.value) return
  recentContentLoading.value = true
  try {
    const response = await contentApi.listTasks({ page: 1, page_size: 100 })
    recentContentTasks.value = (response.items || [])
      .filter(
        (item) =>
          item.current_stage === 'review' &&
          ['review_required', 'reviewed', 'review_blocked', 'completed'].includes(item.status)
      )
      .slice(0, 10)
  } catch (error) {
    console.warn('加载最近生成内容失败:', error)
  } finally {
    recentContentLoading.value = false
  }
}

const handleSelectChat = (threadId) => {
  if (!threadId) return
  chatThreadsStore.setCurrentThreadId(threadId)
  router.push({ name: 'AgentCompWithThreadId', params: { thread_id: threadId } })
}

const handleSelectContent = (taskId) => {
  if (!taskId) return
  router.push({ name: 'ContentResult', params: { taskId } })
}

const handleDeleteChat = async (threadId) => {
  if (!threadId) return
  try {
    await chatThreadsStore.deleteThread(threadId)
    if (route.params.thread_id === threadId) {
      await router.replace({ name: 'AgentComp' })
    }
  } catch (error) {
    console.warn('删除对话失败:', error)
  }
}

const handleRenameChat = async ({ chatId, title }) => {
  try {
    await chatThreadsStore.updateThread(chatId, title)
  } catch (error) {
    console.warn('重命名对话失败:', error)
  }
}

const handleTogglePinChat = async (threadId) => {
  const thread = threads.value.find((item) => item.id === threadId)
  if (!thread) return
  try {
    await chatThreadsStore.updateThread(threadId, null, !thread.is_pinned)
    await chatThreadsStore.loadThreads()
    if (currentThreadId.value) {
      chatThreadsStore.setCurrentThreadId(currentThreadId.value)
    }
  } catch (error) {
    console.warn('更新置顶状态失败:', error)
  }
}

watch(
  () => [route.path, route.params.thread_id],
  () => {
    if (!route.path.startsWith('/agent')) return
    const threadId = typeof route.params.thread_id === 'string' ? route.params.thread_id : null
    chatThreadsStore.setCurrentThreadId(threadId)
  },
  { immediate: true }
)

watch(
  () => [
    route.path,
    contentStudioStore.task?.status,
    contentStudioStore.artifact?.id,
    contentStudioStore.artifact?.current_version,
    contentStudioStore.artifact?.updated_at
  ],
  ([path]) => {
    if (path.startsWith('/content')) void loadRecentContent()
  },
  { immediate: true }
)

// Provide settings modal methods to child components
provide('settingsModal', {
  openSettingsModal
})
</script>

<template>
  <div class="app-layout" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <div class="header">
      <div class="sidebar-brand" @click.stop>
        <router-link v-if="!sidebarCollapsed" to="/" class="brand-link">
          <img src="/contentflow-logo.svg" alt="ContentFlow" class="brand-logo" />
        </router-link>
        <button
          v-else
          type="button"
          class="brand-link brand-expand-button"
          aria-label="展开侧边栏"
          @click="setSidebarCollapsed(false)"
        >
          <img
            src="/contentflow-mark.svg"
            alt="ContentFlow"
            class="brand-logo brand-logo-collapsed"
          />
          <PanelLeftOpen class="brand-expand-icon" size="20" />
        </button>
        <button
          v-if="!sidebarCollapsed"
          type="button"
          class="sidebar-toggle"
          aria-label="折叠侧边栏"
          @click="toggleSidebar"
        >
          <PanelLeftClose size="18" />
        </button>
      </div>
      <div class="nav">
        <!-- 使用mainList渲染导航项 -->
        <div v-for="(item, index) in mainList" :key="index" class="nav-group">
          <button
            v-if="isNavGroup(item)"
            v-show="!item.hidden"
            type="button"
            class="nav-item"
            :class="{ active: isNavItemActive(item) }"
            @click.stop="toggleGroup(item)"
          >
            <span class="nav-icon">
              <a-tooltip placement="right" :open="sidebarCollapsed ? undefined : false">
                <template #title>{{ item.name }}</template>
                <component
                  class="icon"
                  :is="isNavItemActive(item) ? item.activeIcon : item.icon"
                  size="16"
                />
              </a-tooltip>
            </span>
            <span class="nav-text">{{ item.name }}</span>
            <component
              v-if="!sidebarCollapsed"
              class="nav-chevron"
              :is="isGroupExpanded(item) ? ChevronDown : ChevronRight"
              size="14"
            />
          </button>
          <a
            v-else-if="item.external"
            :href="item.path"
            v-show="!item.hidden"
            class="nav-item"
            @click.stop
          >
            <span class="nav-icon">
              <a-tooltip placement="right" :open="sidebarCollapsed ? undefined : false">
                <template #title>{{ item.name }}</template>
                <component class="icon" :is="item.icon" size="16" />
              </a-tooltip>
            </span>
            <span class="nav-text">{{ item.name }}</span>
          </a>
          <RouterLink
            v-else
            :to="item.path"
            v-show="!item.hidden"
            class="nav-item"
            :class="{ active: isNavItemActive(item) }"
            :active-class="item.action ? '' : 'active'"
            @click.stop
          >
            <span class="nav-icon">
              <a-tooltip placement="right" :open="sidebarCollapsed ? undefined : false">
                <template #title>{{ item.name }}</template>
                <component
                  class="icon"
                  :is="isNavItemActive(item) ? item.activeIcon : item.icon"
                  size="16"
                />
              </a-tooltip>
            </span>
            <span class="nav-text">{{ item.name }}</span>
          </RouterLink>
          <div
            v-if="item.children?.length && !sidebarCollapsed && (!isNavGroup(item) || isGroupExpanded(item))"
            class="nav-children"
          >
            <RouterLink
              v-for="child in item.children"
              :key="child.path || child.name"
              :to="child.path"
              class="nav-item nav-item-child"
              :class="{ active: isNavItemActive(child) }"
              @click.stop
            >
              <span class="nav-icon">
                <component
                  class="icon"
                  :is="isNavItemActive(child) ? child.activeIcon : child.icon"
                  size="16"
                />
              </span>
              <span class="nav-text">{{ child.name }}</span>
            </RouterLink>
          </div>
        </div>
      </div>
      <div class="fill">
        <ConversationNavSection
          v-if="!sidebarCollapsed && !isContentRoute"
          class="sidebar-conversations"
          :current-chat-id="activeConversationThreadId"
          :chats-list="threads"
          :has-more-chats="hasMoreThreads"
          :is-loading-more="isLoadingMoreThreads"
          @select-chat="handleSelectChat"
          @delete-chat="handleDeleteChat"
          @rename-chat="handleRenameChat"
          @toggle-pin="handleTogglePinChat"
          @load-more-chats="() => chatThreadsStore.loadMoreThreads()"
        />
        <RecentContentNavSection
          v-else-if="!sidebarCollapsed"
          class="sidebar-conversations"
          :current-task-id="activeContentTaskId"
          :items="recentContentTasks"
          :loading="recentContentLoading"
          @select="handleSelectContent"
        />
      </div>
      <div class="foo">
        <!-- 用户信息组件 -->
        <div class="nav-item user-info" @click.stop>
          <UserInfoComponent :show-role="!sidebarCollapsed">
            <template v-if="userStore.isAdmin" #actions>
              <a-tooltip placement="top" title="任务中心">
                <button
                  class="user-task-center"
                  :class="{ active: isDrawerOpen }"
                  type="button"
                  aria-label="任务中心"
                  @click.stop="taskerStore.openDrawer()"
                >
                  <a-badge
                    :count="activeTaskCount"
                    :overflow-count="99"
                    class="task-center-badge"
                    size="small"
                  >
                    <ClipboardList class="icon" size="16" />
                  </a-badge>
                </button>
              </a-tooltip>
            </template>
          </UserInfoComponent>
        </div>
      </div>
    </div>
    <router-view v-slot="{ Component, route }" id="app-router-view">
      <keep-alive v-if="route.meta.keepAlive !== false">
        <component :is="Component" />
      </keep-alive>
      <component :is="Component" v-else />
    </router-view>

    <!-- Debug Modal -->
    <a-modal
      v-model:open="showDebugModal"
      title="调试面板"
      width="90%"
      :footer="null"
      @cancel="handleDebugModalClose"
      :maskClosable="true"
      :destroyOnClose="true"
      class="debug-modal"
    >
      <DebugComponent />
    </a-modal>
    <TaskCenterDrawer v-if="userStore.isAdmin" />
    <SettingsModal
      v-model:visible="showSettingsModal"
      :initial-tab="settingsInitialTab"
      @close="() => (showSettingsModal = false)"
    />
  </div>
</template>

<style lang="less" scoped>
// Less 变量定义
@sidebar-width: 230px;
@sidebar-collapsed-width: 56px;
@sidebar-padding: 6px 8px;
@sidebar-item-height: 36px;
@sidebar-item-padding-x: 10px;
@sidebar-icon-size: 16px;

.app-layout {
  display: flex;
  flex-direction: row;
  width: 100%;
  height: 100vh;
  min-width: var(--min-width);
}

div.header,
#app-router-view {
  height: 100%;
  max-width: 100%;
}

#app-router-view {
  flex: 1 1 auto;
  overflow-y: auto;
}

.header {
  display: flex;
  flex-direction: column;
  flex: 0 0 @sidebar-width;
  justify-content: flex-start;
  align-items: stretch;
  gap: 16px;
  background-color: var(--main-5);
  height: 100%;
  width: @sidebar-width;
  border-right: 1px solid var(--gray-100);
  padding: @sidebar-padding;
  overflow: hidden;
  user-select: none;
  transition:
    width 0.18s ease,
    flex-basis 0.18s ease;

  .nav {
    display: flex;
    flex: 0 0 auto;
    flex-direction: column;
    justify-content: flex-start;
    align-items: stretch;
    position: relative;
    gap: 4px;
  }

  .nav-group {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .nav-children {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 0 0 4px;
  }

  .nav-chevron {
    flex: 0 0 14px;
    width: 14px;
    height: 14px;
    margin-left: auto;
    color: var(--gray-500);
  }

  .sidebar-conversations {
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }

  .sidebar-brand,
  :deep(.conversation-nav-section:not(.sidebar-conversations)),
  .user-info {
    flex-shrink: 0;
  }

  .fill {
    flex: 1 1 0;
    min-height: 0;
  }

  .sidebar-brand {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: @sidebar-item-height;
    gap: 8px;
  }

  .brand-link {
    display: flex;
    flex: 1 1 auto;
    align-items: center;
    min-width: 0;
    height: @sidebar-item-height;
    color: var(--gray-900);
    text-decoration: none;
    border: 0;
    background: transparent;
    padding: 0 4px;
    cursor: pointer;
  }

  .brand-logo {
    flex: 0 0 auto;
    height: 28px;
    width: auto;
    max-width: calc(100% - 40px);
    object-fit: contain;
  }

  .brand-logo-collapsed {
    max-width: 100%;
  }

  .sidebar-toggle {
    display: inline-flex;
    flex: 0 0 32px;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: var(--gray-600);
    cursor: pointer;
    transition:
      background-color 0.2s ease,
      border-color 0.2s ease,
      color 0.2s ease;

    &:hover,
    &:focus-visible {
      border-color: var(--main-50);
      background: var(--main-20);
      color: var(--main-color);
      outline: none;
    }
  }

  .nav-item {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 8px;
    width: 100%;
    height: @sidebar-item-height;
    box-sizing: border-box;
    padding: 0 @sidebar-item-padding-x;
    border: 1px solid transparent;
    border-radius: 8px;
    background-color: transparent;
    color: var(--gray-700);
    font-size: 14px;
    font-weight: 450;
    transition:
      background-color 0.2s ease-in-out,
      border-color 0.2s ease-in-out,
      color 0.2s ease-in-out;
    margin: 0;
    text-decoration: none;
    cursor: pointer;
    outline: none;
    font-family: inherit;
    text-align: left;

    .nav-icon {
      flex: 0 0 @sidebar-icon-size;
      display: flex;
      align-items: center;
      justify-content: center;
      width: @sidebar-icon-size;
      height: @sidebar-icon-size;
      overflow: hidden;

      :deep(svg),
      :deep(.icon) {
        display: block;
        flex: none;
        width: @sidebar-icon-size;
        height: @sidebar-icon-size;
        min-width: @sidebar-icon-size;
        min-height: @sidebar-icon-size;
      }
    }

    .icon {
      display: block;
      width: @sidebar-icon-size;
      height: @sidebar-icon-size;
    }

    .nav-text {
      flex: 1 1 auto;
      min-width: 0;
      margin: 0;
      overflow: hidden;
      line-height: 20px;
      font-weight: 450;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &.nav-item-child {
      height: 32px;
      padding-left: 28px;
      font-size: 13px;
    }

    & > svg:focus {
      outline: none;
    }
    & > svg:focus-visible {
      outline: none;
    }

    &.active {
      border-color: transparent;
      background-color: color-mix(in srgb, var(--main-color) 6%, var(--gray-0));
      font-weight: 600;
      color: var(--main-color);
    }

    &.primary-action {
      margin-bottom: 8px;
      border-color: var(--gray-150);
      background-color: var(--gray-0);
      color: var(--main-color);
      box-shadow: 0 3px 4px rgba(0, 10, 20, 0.02);

      &:hover {
        border-color: var(--gray-200);
        background-color: var(--gray-0);
        color: var(--main-color);
        box-shadow: 0 3px 4px rgba(0, 10, 20, 0.07);
      }
    }

    &.warning {
      color: var(--color-error-500);
    }

    &:hover {
      border-color: transparent;
      background-color: var(--main-20);
      color: var(--main-color);
    }

    &.api-docs {
      padding: 10px 12px;
    }
    &.docs {
      display: none;
    }
    &.theme-toggle-nav {
      .theme-toggle-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        height: 100%;
        cursor: pointer;
        color: var(--gray-1000);
        transition: color 0.2s ease-in-out;

        &:hover {
          color: var(--main-color);
        }
      }
    }
    &.user-info {
      margin-bottom: 8px;
      padding: 0 3px;
      overflow: hidden;

      :deep(.user-info-component) {
        width: 100%;
      }

      :deep(.user-info-dropdown) {
        width: 100%;
        height: @sidebar-item-height;
        border-radius: 8px;
        transition:
          background-color 0.2s ease,
          color 0.2s ease;
      }

      :deep(.user-info-dropdown:hover) {
        background: var(--main-20);
        color: var(--main-color);
      }
      :deep(.user-name) {
        flex: 1 1 auto;
      }

      :deep(.user-task-center) {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        padding: 0;
        border: 1px solid transparent;
        border-radius: 6px;
        background: transparent;
        color: var(--gray-600);
        cursor: pointer;
        transition:
          background-color 0.2s ease,
          color 0.2s ease;

        &:hover,
        &.active {
          background: var(--main-30);
          color: var(--main-color);
        }

        .task-center-badge {
          display: flex;
          justify-content: center;
        }

        .icon {
          display: block;
          width: 16px;
          height: 16px;
        }
      }
    }
  }
}

.app-layout.sidebar-collapsed {
  .header {
    flex-basis: @sidebar-collapsed-width;
    width: @sidebar-collapsed-width;
    align-items: stretch;
    padding: @sidebar-padding;

    .sidebar-brand {
      justify-content: flex-start;
      width: 100%;
    }

    .brand-expand-button {
      flex: 0 0 @sidebar-item-height;
      justify-content: center;
      width: @sidebar-item-height;
      padding: 0 6px;
      border-radius: 8px;

      .brand-expand-icon {
        display: none;
        width: @sidebar-icon-size;
        height: @sidebar-icon-size;
        color: var(--main-color);
      }

      &:hover,
      &:focus-visible {
        background: var(--main-20);
        outline: none;

        .brand-logo-collapsed {
          display: none;
        }

        .brand-expand-icon {
          display: block;
        }
      }
    }

    .nav {
      align-items: stretch;
      width: 100%;
    }

    .nav-item {
      justify-content: flex-start;
      width: @sidebar-item-height;
      padding: 0 10px;

      .nav-text {
        max-width: 0;
        margin-left: 0;
        opacity: 0;
        pointer-events: none;
      }

      &.user-info {
        padding: 0;
        :deep(.user-info-actions) {
          display: none;
        }
      }
    }
  }
}
</style>
