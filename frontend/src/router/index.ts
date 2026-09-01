/**
 * 路由表 + 登录守卫。
 *
 * 两个亮点：
 *   1. meta.requiresLogin —— �条路由自己声明"要不要登录"，守卫只看这个标记，
 *      不用在每个页面里重复写 if
 *   2. 未登录跳 /login 时带上 redirect 参数 —— 登录成功后能回到用户原本想去的页面
 */
import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  // hash 模式（#/chat）：不需要后端配合 fallback 路由，教学项目最省事
  history: createWebHashHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/Login.vue') },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('@/views/Chat.vue'),
      meta: { requiresLogin: true },
    },
    {
      path: '/library',
      name: 'library',
      component: () => import('@/views/Library.vue'),
      meta: { requiresLogin: true },
    },
    {
      path: '/paper/:paper_id',
      name: 'paper-detail',
      component: () => import('@/views/PaperDetail.vue'),
      meta: { requiresLogin: true },
    },
    {
      path: '/history',
      name: 'history',
      component: () => import('@/views/History.vue'),
      meta: { requiresLogin: true },
    },
    // 兜底：任何没匹配的路径回聊天页
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

/** 全局前置守卫：每次跳转前跑一次 */
router.beforeEach((to) => {
  const loggedIn = Boolean(localStorage.getItem('access_token'))
  if (to.meta.requiresLogin && !loggedIn) {
    // 记住用户想去哪，登录后送回去
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.path === '/login' && loggedIn) {
    return { path: '/chat' }   // 已登录就别再逛登录页了
  }
  return true
})

export default router
