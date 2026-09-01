/**
 * 聊天状态 store：消息列表、会话管理、SSE 流式发送。
 *
 * 这是最复杂的一个 store，三个关键点：
 *   1. 流式消息的"响应式陷阱"——ssePost 的回调里必须改"数组里取出的那个对象"，
 *      不能改闭包里存下来的原始对象（见 sendMessage 里的注释）
 *   2. 停止生成 —— AbortController，点了停止就 abort()，流读取立即中断
 *   3. 会话切换 —— X-Conversation-Id 响应头 + history 接口回放
 */

import { defineStore } from 'pinia'
import { ssePost, http } from '@/services/api'
import type { ChatMessage, Conversation, ConversationDetail, ToolCallInfo } from '@/types'

export const useChatStore = defineStore('chat', {
  state: () => ({
    /** 当前聊天界面的消息列表（渲染层唯一数据源） */
    messages: [] as ChatMessage[],

    /** 当前会话 ID（0 = 还没有会话，第一条消息发出后端会建会话并回传 ID） */
    conversationId: 0,

    /** 侧边栏会话列表 */
    conversations: [] as Conversation[],

    /** 是否正在流式接收（输入框禁用 + 停止按钮显示用） */
    streaming: false,

    /** 中断控制器：停止生成用 */
    _abort: null as AbortController | null,

    /** 聊天范围：null=全库问答；数字=限定这篇论文 */
    paperScope: null as number | null,
  }),

  actions: {
    /** 发送一条消息：先挤进一条用户气泡，再开 SSE 流攒回答 */
    async send(question: string): Promise<void> {
      if (this.streaming || !question.trim()) return

      // 1. 用户气泡立刻上屏（不等网络）
      this.messages.push({ role: 'user', content: question })

      // 2. 预置一个空的 assistant 气泡占位，流式往里灌字
      //    注意：这里取的 raw 变量是刚 push 的原始对象；渲染层拿到的是
      //    Vue 包过的响应式代理。下面回调里必须用 msgs[msgs.length-1]
      //    取"数组里的代理对象"来改——直接改 raw 同样生效于同一底层对象，
      //    但为避免混淆统一走数组取（Vue3 reactive 数组里存的对象会被代理）
      const msgs = this.messages
      msgs.push({ role: 'assistant', content: '', toolCalls: [], streaming: true })

      this.streaming = true
      this._abort = new AbortController()
      const respHeaders: Record<string, string> = {}

      try {
        await ssePost(
          '/api/chat',
          {
            question,
            paper_id: this.paperScope,
            // 0 转成 null：让后端新建会话
            conversation_id: this.conversationId || null,
          },
          (ev) => {
            // 每次都重新取最后一个气泡（就是刚才 push 的那个）——代理对象，改了必触发渲染
            const bubble = this.messages[this.messages.length - 1]
            if (bubble.role !== 'assistant') return   // 琐碎防御：万一用户中途清屏

            if (ev.type === 'content') {
              bubble.content += ev.text          // 打字机：往气泡里追加片段
            } else if (ev.type === 'tool_call') {
              // 模型申请调工具 → 攒进 toolCalls 列表（ToolCallDetail 折叠面板展示）
              const info: ToolCallInfo = { name: ev.name, arguments: ev.arguments }
              bubble.toolCalls = [...(bubble.toolCalls ?? []), info]
            } else if (ev.type === 'tool_result') {
              // 工具执行完 → 给最后一条匹配的调用补结果摘要
              const list = bubble.toolCalls ?? []
              const last = [...list].reverse().find((t) => t.name === ev.name && !t.resultSummary)
              if (last) last.resultSummary = ev.summary
            }
          },
          respHeaders,
          this._abort.signal,
        )

        // 3. 流正常结束：第一次对话会从响应头拿到后端分配的会话 ID
        if (respHeaders['X-Conversation-Id']) {
          this.conversationId = Number(respHeaders['X-Conversation-Id'])
        }
      } catch (err) {
        const aborted = err instanceof DOMException && err.name === 'AbortError'
        const bubble = this.messages[this.messages.length - 1]
        if (!aborted && bubble.role === 'assistant') {
          // 真错误（非用户主动停止）：把错误显示在气泡里，不让界面静默失败
          bubble.content += (bubble.content ? '\n\n' : '') + `⚠️ ${err instanceof Error ? err.message : '请求失败'}`
        }
      } finally {
        const bubble = this.messages[this.messages.length - 1]
        if (bubble.role === 'assistant') bubble.streaming = false
        this.streaming = false
        this._abort = null
        // 会话列表可能变了（新会话）→ 后台静默刷新侧边栏
        this.fetchConversations()
      }
    },

    /** 用户点"停止生成"：中断流。后端检测到断连后照样落库已生成的部分 */
    stop(): void {
      this._abort?.abort()
    },

    /** 清空当前界面，从指定会话恢复（History 页"继续对话"用） */
    async loadConversation(conversationId: number): Promise<void> {
      const resp = await http.get<ConversationDetail>(`/history/${conversationId}`)
      this.conversationId = resp.data.id
      this.paperScope = resp.data.paper_id
      // 历史消息转成展示消息（没有工具调用过程可回放——后端只落库最终文本）
      this.messages = resp.data.messages.map((m) => ({
        role: m.role,
        content: m.content,
        toolCalls: [],
        streaming: false,
      }))
    },

    /** 开一个新会话：清屏 + 会话 ID 归零（范围保留还是清掉由调用方决定） */
    newConversation(): void {
      this.conversationId = 0
      this.messages = []
    },

    /** 拉侧边栏会话列表 */
    async fetchConversations(): Promise<void> {
      const resp = await http.get<{ items: Conversation[] }>('/history')
      this.conversations = resp.data.items
    },

    /** 删除一个会话（History 页用） */
    async deleteConversation(conversationId: number): Promise<void> {
      await http.delete(`/history/${conversationId}`)
      if (this.conversationId === conversationId) this.newConversation()
      await this.fetchConversations()
    },
  },
})
