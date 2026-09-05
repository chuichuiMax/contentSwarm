import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/layouts/AppLayout.vue'
import BlankLayout from '@/layouts/BlankLayout.vue'
import { useUserStore } from '@/stores/user'
import { useAgentStore } from '@/stores/agent'
import { sanitizeRedirect } from '@/utils/oidcAutoStart'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'main',
      component: BlankLayout,
      children: [
        {
          path: '',
          name: 'Home',
          component: () => import('../views/HomeView.vue'),
          meta: { keepAlive: true, requiresAuth: false }
        }
      ]
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { requiresAuth: false }
    },
    {
      path: '/auth/oidc/callback', // oidc登录回调页面
      name: 'OIDCCallback',
      component: () => import('@/views/OIDCCallbackView.vue'),
      meta: { public: true }
    },
    {
      path: '/agent',
      name: 'AgentMain',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'AgentComp',
          component: () => import('../views/AgentView.vue'),
          meta: { keepAlive: true, requiresAuth: true }
        },
        {
          path: ':thread_id',
          name: 'AgentCompWithThreadId',
          component: () => import('../views/AgentView.vue'),
          meta: { keepAlive: true, requiresAuth: true }
        }
      ]
    },
    {
      path: '/workspace',
      name: 'workspace',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'WorkspaceComp',
          component: () => import('../views/WorkspaceView.vue'),
          meta: { keepAlive: true, requiresAuth: true }
        }
      ]
    },
    {
      path: '/materials',
      name: 'materials',
      component: AppLayout,
      redirect: '/materials/images',
      children: [
        {
          path: 'images',
          name: 'MaterialImages',
          component: () => import('../views/MaterialLibraryView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'cover-templates',
          redirect: '/materials/images'
        }
      ]
    },
    {
      path: '/content',
      name: 'content',
      component: AppLayout,
      redirect: '/content/new',
      children: [
        {
          path: 'new',
          name: 'ContentNew',
          component: () => import('../views/ContentStudioView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'tasks/:taskId',
          name: 'ContentTask',
          component: () => import('../views/ContentStudioView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'results/:taskId',
          name: 'ContentResult',
          component: () => import('../views/ContentResultView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'history',
          name: 'ContentHistory',
          component: () => import('../views/ContentHistoryView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'covers',
          name: 'ContentCovers',
          component: () => import('../views/CoverGenerationView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'covers/:assetId/edit',
          name: 'ContentCoverEditor',
          component: () => import('../views/CoverEditorView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'results/:taskId',
          name: 'ContentResult',
          component: () => import('../views/ContentResultView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'accounts',
          name: 'ContentAccounts',
          component: () => import('../views/XiaohongshuAccountsView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'admin/rules',
          name: 'ContentRules',
          component: () => import('../views/ContentRuleLibraryView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/hycanvas',
      name: 'hycanvas',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'HyCanvasWorkspace',
          component: () => import('../views/HyCanvasWorkspaceView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        }
      ]
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'DashboardComp',
          component: () => import('../views/DashboardView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/model-manage',
      name: 'model-manage',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ModelManageComp',
          component: () => import('../views/ModelManageView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'accounts',
          name: 'AccountManageComp',
          component: () => import('../views/AccountManageView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'employees',
          name: 'EmployeeManageComp',
          component: () => import('../views/EmployeeManageView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        }
      ]
    },
    {
      path: '/config-manage',
      name: 'config-manage',
      component: AppLayout,
      redirect: '/config-manage/personas',
      children: [
        {
          path: 'personas',
          name: 'PersonaManageComp',
          component: () => import('../views/PersonaManageView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'permissions',
          name: 'PermissionConfigComp',
          component: () => import('../views/PermissionConfigView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'permissions/:roleId',
          name: 'RoleAuthorizeComp',
          component: () => import('../views/RoleAuthorizeView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'content-types',
          name: 'ContentTypeConfigComp',
          component: () => import('../views/ContentTypeConfigView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        },
        {
          path: 'variables',
          name: 'VariableConfigComp',
          component: () => import('../views/VariableConfigView.vue'),
          meta: { keepAlive: false, requiresAuth: true },
        }
      ]
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'KnowledgeComp',
          component: () => import('../views/DataBaseView.vue'),
          meta: {
            keepAlive: false,
            requiresAuth: true,
            requiresAdmin: true
          }
        },
        {
          path: ':kbId',
          name: 'KnowledgeBaseDetail',
          component: () => import('../views/DataBaseInfoView.vue'),
          meta: {
            keepAlive: false,
            requiresAuth: true,
            requiresAdmin: true
          }
        }
      ]
    },
    {
      path: '/extensions/knowledgebase/:kbId',
      redirect: (to) => `/knowledge/${to.params.kbId}`
    },
    {
      path: '/extensions',
      name: 'extensions',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ExtensionsComp',
          component: () => import('../views/ExtensionsView.vue'),
          meta: {
            keepAlive: false,
            requiresAuth: true
          },
          beforeEnter: (to) => {
            if (to.query.tab === 'knowledge') {
              return { path: '/knowledge' }
            }
            return true
          },
          children: [
            {
              path: 'mcp/:slug',
              name: 'ExtensionMcpDetail',
              component: () => import('../components/extensions/McpDetailView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true,
                requiresAdmin: true
              }
            },
            {
              path: 'skill/:slug',
              name: 'ExtensionSkillDetail',
              component: () => import('../components/extensions/SkillDetailView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true
              }
            }
          ]
        }
      ]
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('../views/EmptyView.vue'),
      meta: { requiresAuth: false }
    }
  ]
})

// 全局前置守卫
router.beforeEach(async (to) => {
  // 检查路由是否需要认证
  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth === true)
  const requiresAdmin = to.matched.some((record) => record.meta.requiresAdmin)
  const requiresSuperAdmin = to.matched.some((record) => record.meta.requiresSuperAdmin)

  const userStore = useUserStore()

  // 如果有 token 但用户信息未加载，先获取用户信息
  if (userStore.token && !userStore.userId) {
    try {
      await userStore.getCurrentUser()
    } catch (error) {
      // 如果获取用户信息失败（如 token 过期），清除 token
      console.error('获取用户信息失败:', error)
      userStore.logout()
    }
  }

  const isLoggedIn = userStore.isLoggedIn
  const isAdmin = userStore.isAdmin
  const isSuperAdmin = userStore.isSuperAdmin

  // 如果路由需要认证但用户未登录
  if (requiresAuth && !isLoggedIn) {
    // 保存尝试访问的路径，登录后跳转
    sessionStorage.setItem('redirect', to.fullPath)
    return '/login'
  }

  // 如果路由需要管理员权限但用户不是管理员
  if (requiresAdmin && !isAdmin) {
    // 如果是普通用户，跳转到聊天页空态
    try {
      const agentStore = useAgentStore()
      // 等待 store 初始化完成
      if (!agentStore.isInitialized) {
        await agentStore.initialize()
      }
      return '/agent'
    } catch (error) {
      console.error('获取智能体信息失败:', error)
      return '/agent'
    }
  }

  // 如果路由需要超级管理员权限但用户不是超级管理员
  if (requiresSuperAdmin && !isSuperAdmin) {
    try {
      const agentStore = useAgentStore()
      if (!agentStore.isInitialized) {
        await agentStore.initialize()
      }
      return '/agent'
    } catch (error) {
      console.error('获取智能体信息失败:', error)
      return '/agent'
    }
  }

  // 如果用户已登录但访问登录页，按 redirect 参数跳转
  if (to.path === '/login' && isLoggedIn) {
    return sanitizeRedirect(to.query.redirect)
  }

  // 其他情况正常导航
  return true
})

export default router
