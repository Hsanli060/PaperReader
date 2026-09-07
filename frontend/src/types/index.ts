/**
 * 全局 TypeScript 类型定义 —— 一面"合同墙"。
 *
 * 这里每个类型的字段，都照着后端路由返回的 JSON 抄的（对照后端源码）：
 *   User      ← /api/auth/login|register 的返回
 *   Paper     ← /api/papers 的 _paper_to_dict()
 *   Citation  ← /api/papers/{id}/citations 的 items[]
 *   SseEvent  ← /api/chat SSE 流里 data 行的 JSON
 *
 * 前端永远不猜后端给什么形状——改了后端字段，先来这里对齐，
 * TypeScript 的编译器会替你把所有对不上的地方揪出来（build 时就报错）。
 */

// ---------- 认证 ----------

/** 登录/注册接口的返回：一对 token */
export interface TokenPair {
  access_token: string
  refresh_token: string
}

/** 从 JWT 里解出来的当前用户信息（我们不用登录接口返回用户详情，直接存 username） */
export interface CurrentUser {
  userId: number
  username: string
}

// ---------- 论文 ----------

/** 论文生命周期：pending(刚登记) → downloaded(PDF到手) → parsed(解析成md) → indexed(进向量库) → failed(流水线出错) */
export type PaperStatus = 'pending' | 'downloaded' | 'parsed' | 'indexed' | 'failed'

/** /api/papers 列表和详情里的单篇论文。authors 在库里存 JSON 字符串，后端已解析成数组 */
export interface Paper {
  id: number
  arxiv_id: string | null
  title: string
  authors: string[]
  abstract: string | null
  pdf_path: string | null
  status: PaperStatus
  last_error: string | null  // failed 时的错误信息（徽章 tooltip 用）
  added_by: number | null    // 谁添加的（署名展示，不做隔离）
  created_at: string
}

/** POST /api/papers/arxiv 的返回（duplicated=true 表示"库里已经有了"） */
export interface PaperAddResult extends Paper {
  duplicated: boolean
}

/** GET /api/papers 的分页返回 */
export interface PaperPage {
  total: number
  page: number
  page_size: number
  items: Paper[]
}

/** GET /api/papers/{id}/summary 的返回：四段结构化摘要 */
export interface PaperSummary {
  problem: string
  method: string
  experiment: string
  conclusion: string
}

/** 引用关系里的一条：这篇论文引用了谁、为什么引用 */
export interface Citation {
  ref: string    // 参考文献编号，如 "[1]"
  title: string  // 被引论文标题
  why: string    // 引用它做什么
}

// ---------- 会话与消息 ----------

/** /api/history 列表里的一条会话（FIX-3'：paper_ids 多选 scope，空数组=全库） */
export interface Conversation {
  id: number
  title: string
  paper_ids: number[]   // 问答范围（NotebookLM 式源选择）
  message_count: number
  created_at: string
}

/** /api/history/{id} 的返回：整个会话的消息回放 */
export interface ConversationDetail {
  id: number
  title: string
  paper_ids: number[]   // 同上，前端据此恢复 scope 勾选
  messages: HistoryMessage[]
}

/** 历史消息（落库的只有 user/assistant 两种 role） */
export interface HistoryMessage {
  role: 'user' | 'assistant'
  content: string
}

/**
 * 聊天界面里的"展示用消息"。
 * 比历史消息多两样：role='assistant' 时可携带工具调用过程；流式阶段有 streaming 标记
 */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  /** 这条 assistant 消息过程中发生的工具调用（从 SSE 事件攒出来） */
  toolCalls?: ToolCallInfo[]
  /** 还在流式接收中（渲染打字光标用） */
  streaming?: boolean
}

/** 一次工具调用的事件记录（ToolCallDetail 组件的数据） */
export interface ToolCallInfo {
  name: string
  arguments: string       // 模型给的 JSON 字符串参数
  resultSummary?: string  // 执行完的摘要（收到 tool_result 事件后补上）
}

// ---------- SSE 事件协议（和后端 chat.py 的 docstring 一一对齐） ----------

/** 工具调用事件 */
export interface ToolCallEvent {
  type: 'tool_call'
  name: string
  arguments: string
}

/** 工具结果事件 */
export interface ToolResultEvent {
  type: 'tool_result'
  name: string
  summary: string
}

/** 文本片段事件 */
export interface ContentEvent {
  type: 'content'
  text: string
}

/** 生成中断/空回答事件：后端流中途出错或 LLM 返回空时显式下发（SSE 头已 200，只能流内报错） */
export interface StreamErrorEvent {
  type: 'error'
  message: string
}

/** SSE data 行可能的全部形状（联合类型：解析后按 type 收窄） */
export type SseEvent = ToolCallEvent | ToolResultEvent | ContentEvent | StreamErrorEvent
