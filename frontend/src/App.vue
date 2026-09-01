<script setup lang="ts">
/**
 * 根组件：全局导航栏 + 路由出口。
 * 导航栏只在登录页之外显示（登录页是全屏的，不该有导航）。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useChatStore } from '@/stores/chat'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const chatStore = useChatStore()

const isLogin = computed(() => route.path === '/login')

const NAV = [
  { path: '/chat', label: '💬 聊天' },
  { path: '/library', label: '📚 论文库' },
  { path: '/history', label: '📜 历史' },
]

function isActive(path: string) {
  // 详情页也高亮"论文库"（它是论文库的子页面）
  if (path === '/library') return route.path === '/library' || route.path.startsWith('/paper/')
  return route.path === path
}

function logout() {
  userStore.logout()
  chatStore.$reset()   // 聊天状态也清干净（换账号不能看到上一家的会话列表）
  router.push('/login')
}
</script>

<template>
  <!-- 登录页：全屏独立，不带导航 -->
  <router-view v-if="isLogin" />

  <template v-else>
    <header class="top-bar">
      <div class="brand" @click="router.push('/chat')">📄 PaperReader</div>
      <nav class="nav">
        <router-link
          v-for="n in NAV"
          :key="n.path"
          :to="n.path"
          class="nav-item"
          :class="{ active: isActive(n.path) }"
        >{{ n.label }}</router-link>
      </nav>
      <div class="user-box">
        <span class="username">{{ userStore.username }}</span>
        <button class="logout-btn" @click="logout">退出</button>
      </div>
    </header>
    <router-view />
  </template>
</template>

<style scoped>
.top-bar {
  height: 48px;
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 20px;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}
.brand {
  font-weight: 700;
  font-size: 16px;
  cursor: pointer;
  color: var(--color-primary);
}
.nav { display: flex; gap: 6px; flex: 1; }
.nav-item {
  padding: 6px 14px;
  border-radius: 8px;
  text-decoration: none;
  color: var(--color-text-secondary);
  font-size: 14px;
}
.nav-item:hover { background: var(--color-code-bg); }
.nav-item.active {
  background: #e8f3f2;
  color: var(--color-primary);
  font-weight: 600;
}
.user-box { display: flex; align-items: center; gap: 10px; }
.username { font-size: 13px; color: var(--color-text-secondary); }
.logout-btn {
  border: 1px solid var(--color-border);
  background: transparent;
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.logout-btn:hover { border-color: #dc2626; color: #dc2626; }
</style>
