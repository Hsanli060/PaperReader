/**
 * 用户状态 store：登录态 + token 管理。
 *
 * Pinia 要点：
 *   - state 用工厂函数返回（每个 store 实例一份独立状态）
 *   - 动作（action）就是普通异步函数，直接改 this
 *   - 组件里通过 useUserStore() 拿到同一个全局单例
 *
 * token 存 localStorage：刷新页面不丢（JWT 本身带过期时间，泄露风险可控——教学项目够用；
 * 生产做法是 httpOnly cookie，超出本课范围）
 */

import { defineStore } from 'pinia'
import { http, apiErrorMessage } from '@/services/api'
import type { TokenPair } from '@/types'

export const useUserStore = defineStore('user', {
  state: () => ({
    /** 登录后回填；刷新页面时从 localStorage 恢复 */
    userId: Number(localStorage.getItem('user_id')) || 0,
    username: localStorage.getItem('username') ?? '',
    /** 组件要的提交中标记（登录按钮转圈用） */
    loading: false,
  }),

  getters: {
    /** 有 token 就算已登录（token 无效会被 API 层的 401 拦截器兜住） */
    isLoggedIn: (state) => Boolean(localStorage.getItem('access_token')),
  },

  actions: {
    /** 注册：返回 null=成功；返回字符串=失败原因（表单上直接展示） */
    async register(username: string, password: string): Promise<string | null> {
      this.loading = true
      try {
        await http.post('/auth/register', { username, password })
        return null
      } catch (err) {
        return apiErrorMessage(err)
      } finally {
        this.loading = false
      }
    },

    /** 登录：成功把 token 对存进 localStorage 并回填用户信息；失败返回错误文案 */
    async login(username: string, password: string): Promise<string | null> {
      this.loading = true
      try {
        const resp = await http.post<TokenPair>('/auth/login', { username, password })
        localStorage.setItem('access_token', resp.data.access_token)
        localStorage.setItem('refresh_token', resp.data.refresh_token)

        // JWT 的 payload 中段是 base64 编码的明文 JSON（签名只保证没被篡改，不保密），
        // 前端解出来拿 sub（user_id）和 username，省一次"我是谁"接口
        const payload = JSON.parse(atob(resp.data.access_token.split('.')[1]))
        this.userId = Number(payload.sub)
        this.username = payload.username
        localStorage.setItem('user_id', String(this.userId))
        localStorage.setItem('username', this.username)
        return null
      } catch (err) {
        return apiErrorMessage(err)
      } finally {
        this.loading = false
      }
    },

    /** 退出：清 token + 清状态，路由守卫会把用户送回登录页 */
    logout(): void {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user_id')
      localStorage.removeItem('username')
      this.userId = 0
      this.username = ''
    },
  },
})
