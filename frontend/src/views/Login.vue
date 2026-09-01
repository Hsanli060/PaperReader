<script setup lang="ts">
/**
 * 登录/注册页：一个页面两个 tab，共享用户名密码输入。
 * 成功后跳聊天页；注册成功自动切到登录 tab（注册不自动登录——让用户亲手体验一次登录流程）。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const errorMsg = ref('')
const submitting = ref(false)

async function submit() {
  errorMsg.value = ''
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '用户名和密码都要填'
    return
  }
  submitting.value = true
  try {
    if (mode.value === 'register') {
      const err = await userStore.register(username.value.trim(), password.value)
      if (err) {
        errorMsg.value = err
        return
      }
      // 注册成功 → 切到登录 tab，提示已就绪
      mode.value = 'login'
      errorMsg.value = ''
      window.alert('注册成功，请登录')
      return
    }
    const err = await userStore.login(username.value.trim(), password.value)
    if (err) {
      errorMsg.value = err
      return
    }
    router.push('/chat')   // 登录成功 → 进聊天
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card pr-card">
      <h1 class="login-title">📄 PaperReader</h1>
      <p class="login-sub">AI 论文研读助手 · 基于知识库的学术论文问答</p>

      <div class="mode-tabs">
        <button :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
        <button :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
      </div>

      <form @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <input v-model="username" placeholder="3-50 个字符" autocomplete="username" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" type="password" :placeholder="mode === 'register' ? '至少 6 位' : '你的密码'"
            autocomplete="current-password" />
        </label>

        <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

        <button type="submit" class="submit-btn" :disabled="submitting">
          {{ submitting ? '请稍候…' : mode === 'login' ? '登录' : '注册' }}
        </button>
      </form>

      <p class="login-footnote">
        注册/登录后，你的论文库和对话历史与其他用户完全隔离
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(1200px 600px at 70% -10%, #eef6f5 0%, transparent 60%),
    var(--color-bg);
}
.login-card { width: 380px; padding: 32px; }
.login-title { margin: 0; font-size: 26px; text-align: center; }
.login-sub {
  margin: 8px 0 20px;
  text-align: center;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.mode-tabs {
  display: flex;
  background: var(--color-code-bg);
  border-radius: 10px;
  padding: 3px;
  margin-bottom: 18px;
}
.mode-tabs button {
  flex: 1;
  border: none;
  background: transparent;
  padding: 8px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  color: var(--color-text-secondary);
}
.mode-tabs button.active {
  background: var(--color-surface);
  color: var(--color-primary);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px; }
.field span { font-size: 13px; color: var(--color-text-secondary); }
.field input {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 14px;
  outline: none;
}
.field input:focus { border-color: var(--color-primary); }

.error-msg {
  color: #dc2626;
  font-size: 13px;
  margin-bottom: 10px;
}
.submit-btn {
  width: 100%;
  border: none;
  background: var(--color-primary);
  color: #fff;
  font-size: 15px;
  padding: 10px;
  border-radius: 8px;
  cursor: pointer;
}
.submit-btn:disabled { opacity: 0.6; cursor: wait; }
.submit-btn:hover:not(:disabled) { background: var(--color-primary-hover); }

.login-footnote {
  margin: 16px 0 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  text-align: center;
}
</style>
