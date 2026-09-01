/**
 * 接口层：所有 HTTP 请求都从这里走。
 *
 * 三件事：
 *   1. axios 实例 —— 统一 baseURL、统一带 JWT、401 时自动用 refresh token 换新票重试一次
 *   2. ssePost —— SSE 流式请求。EventSource 只支持 GET，我们的 /api/chat 是 POST + 请求体，
 *      所以用 fetch + ReadableStream 手工解析 SSE 格式
 *   3. downloadPdf —— 论文 PDF 是本地文件，走后端静态路由拿不到（没挂 StaticFiles），
 *      前端直接用 pdf_path 提示用户去 data/papers 目录看（教学项目够用）
 *
 * 谁调用它：三个 Pinia store（user/chat/paper）。组件不直接 import axios。
 */

import axios, { AxiosError, type AxiosRequestConfig } from 'axios'
import type { SseEvent } from '@/types'

// ---------- 1. axios 实例 ----------

export const http = axios.create({
  baseURL: '/api',      // 全部接口都在 /api 前缀下；开发期 vite 代理转发给 8000
  timeout: 120_000,     // LLM 生成可能要一两分钟
})

/** 请求拦截器：每个请求自动带上 Authorization: Bearer <access token> */
http.interceptors.request.use((config) => {
  const access = localStorage.getItem('access_token')
  if (access) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${access}`
  }
  return config
})

/** 是否正在刷新 token（防止 401 风暴时并发发 N 个 refresh 请求） */
let refreshing: Promise<string | null> | null = null

/** 用 refresh token 换一对新 token。失败返回 null（意味着该重新登录了） */
async function tryRefresh(): Promise<string | null> {
  const refresh = localStorage.getItem('refresh_token')
  if (!refresh) return null
  try {
    // 注意用裸 axios（不带拦截器），避免"refresh 请求自己也 401"死循环
    const resp = await axios.post('/api/auth/refresh', { refresh_token: refresh })
    localStorage.setItem('access_token', resp.data.access_token)
    localStorage.setItem('refresh_token', resp.data.refresh_token)
    return resp.data.access_token as string
  } catch {
    // refresh token 也过期/无效：清干净，逼用户回登录页
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    return null
  }
}

/** 响应拦截器：401 → 先试刷新 → 刷新成功重发原请求，失败则跳登录页 */
http.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError) => {
    const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined
    if (error.response?.status !== 401 || !original || original._retried) {
      return Promise.reject(error)
    }
    original._retried = true   // 只重试一次：refresh 也 401 就认输

    // 并发去重：多个请求同时 401 时，共享同一个刷新 Promise
    refreshing = refreshing ?? tryRefresh()
    const newAccess = await refreshing
    refreshing = null

    if (!newAccess) {
      // 彻底失效 → 回登录页
      window.location.hash = '#/login'
      return Promise.reject(error)
    }
    original.headers = { ...original.headers, Authorization: `Bearer ${newAccess}` }
    return http.request(original)   // 拿新票重发原请求，用户无感
  },
)

// ---------- 2. SSE（POST 版） ----------

/**
 * 发起 SSE 流式 POST 请求，把每个事件交给 onEvent 回调。
 *
 * SSE 的线上格式（后端 sse_starlette 吐的）：
 *   event: message
 *   data: {"type":"content","text":"..."}
 *   （空行 = 一条消息结束）
 *
 * 解析套路：按字节流读 → 攒到缓冲区 → 按 "\n\n" 切成一条条消息 →
 * 每条消息里找 "data: " 开头的行 → JSON.parse 出事件对象。
 *
 * :param headers: 额外响应头收集器。SSE 的自定义头（X-Conversation-Id）
 *   只能在流建立后从 resp.headers 读——所以由调用方传对象进来，我们往里写
 * :param signal: AbortSignal —— 用户点"停止生成"时中断流
 */
export async function ssePost(
  url: string,
  body: unknown,
  onEvent: (ev: SseEvent) => void,
  headers: Record<string, string>,
  signal?: AbortSignal,
): Promise<void> {
  const access = localStorage.getItem('access_token')
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(access ? { Authorization: `Bearer ${access}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })

  // 流式接口的错误（如 401/404）不会走 axios 拦截器，这里手工处理
  if (!resp.ok) {
    let message = `HTTP ${resp.status}`
    try {
      const errJson = await resp.json()
      message = errJson.message ?? message
    } catch { /* 响应体不是 JSON 就用默认消息 */ }
    throw new Error(message)
  }

  // 后端把会话 ID 写在响应头里（新会话时前端据此记住"现在是几号会话"）
  const convId = resp.headers.get('X-Conversation-Id')
  if (convId) headers['X-Conversation-Id'] = convId

  // 拿到字节流读取器，开始逐块读
  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // 消息之间用空行分隔。攒够一条就切出来处理，剩下的留在缓冲区
    // （一次网络包可能只送来半条消息，剩下半条等下一块）
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const rawMessage = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      for (const line of rawMessage.split('\n')) {
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()   // 剥掉 "data:" 前缀
        if (!payload) continue
        try {
          onEvent(JSON.parse(payload) as SseEvent)
        } catch {
          // 个别坏行不炸整个流（比如 sse_starlette 的 ping 注释行）
          console.warn('SSE 事件解析失败：', payload)
        }
      }
    }
  }
}

// ---------- 3. 通用错误消息提取 ----------

/** 从 axios 错误里提取后端统一格式 {"message": "..."} 的人话文本 */
export function apiErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err) && err.response?.data) {
    const data = err.response.data as { message?: string }
    if (data.message) return data.message
  }
  if (err instanceof Error) return err.message
  return '未知错误'
}
