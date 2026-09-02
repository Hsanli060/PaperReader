<script setup lang="ts">
/**
 * 聊天主界面：左侧会话栏 + 右侧消息流 + 底部输入框。
 *
 * 三块职责：
 *   1. 会话侧边栏：列出历史会话，点一条 = 载入回放并继续聊；"新会话"清屏
 *   2. 问答范围（FIX-3'）：多选 checkbox 列表（NotebookLM 式源选择），空=全库
 *   3. 消息流：chat store 的 messages → MessageBubble；流式时自动滚到底
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useChatStore } from '@/stores/chat'
import { usePaperStore } from '@/stores/paper'
import MessageBubble from '@/components/MessageBubble.vue'
import type { Conversation } from '@/types'

const chat = useChatStore()
const paperStore = usePaperStore()
const input = ref('')
const listRef = ref<HTMLElement | null>(null)

onMounted(() => {
  chat.fetchConversations()
  paperStore.fetchPage(1)
})

/** 多选 scope：勾中的论文 id 列表 */
const scopeIds = computed({
  get: () => chat.scopeIds,
  set: (v: number[]) => chat.setScopeIds(v),
})

function toggleScope(id: number) {
  const next = scopeIds.value.includes(id)
    ? scopeIds.value.filter((x) => x !== id)
    : [...scopeIds.value, id]
  chat.setScopeIds(next)
  chat.newConversation() // 换范围 = 上下文完全不同，开新会话最干净
}

function clearScope() {
  chat.setScopeIds([])
  chat.newConversation()
}

function send() {
  const q = input.value.trim()
  if (!q || chat.streaming) return
  input.value = ''
  chat.send(q)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

/** 新消息/流式更新时滚到底部（nextTick 等 DOM 先画完） */
watch(
  () => chat.messages.length + (chat.messages[chat.messages.length - 1]?.content.length ?? 0),
  async () => {
    await nextTick()
    listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
  },
)

function openConversation(c: Conversation) {
  if (chat.streaming) return
  chat.loadConversation(c.id)
}

function newChat() {
  if (chat.streaming) return
  chat.newConversation()
}

function fmtTime(s: string) {
  return s.slice(5, 10) + ' ' + s.slice(11, 16)   // MM-DD HH:mm
}
</script>

<template>
  <div class="chat-page">
    <!-- 左：会话栏 -->
    <aside class="conv-sidebar">
      <button class="new-chat-btn" @click="newChat">＋ 新会话</button>
      <div class="scope-box">
        <div class="scope-label">问答范围 <span v-if="scopeIds.length" class="scope-count">已选 {{ scopeIds.length }}</span></div>
        <label class="scope-item">
          <input type="checkbox" :checked="scopeIds.length === 0" @change="clearScope()" />
          全库检索
        </label>
        <label v-for="p in paperStore.items" :key="p.id" class="scope-item">
          <input type="checkbox" :checked="scopeIds.includes(p.id)" @change="toggleScope(p.id)" />
          <span class="scope-title" :title="p.title">{{ p.title.slice(0, 28) }}{{ p.title.length > 28 ? '…' : '' }}</span>
        </label>
        <div v-if="!paperStore.items.length" class="scope-empty">论文库为空</div>
      </div>
      <div class="conv-list">
        <div
          v-for="c in chat.conversations"
          :key="c.id"
          class="conv-item"
          :class="{ active: c.id === chat.conversationId }"
          @click="openConversation(c)"
        >
          <div class="conv-title">{{ c.title }}</div>
          <div class="conv-meta">{{ fmtTime(c.created_at) }} · {{ c.message_count }} 条<template v-if="c.paper_ids?.length"> · 限定 {{ c.paper_ids.length }} 篇</template></div>
        </div>
        <div v-if="!chat.conversations.length" class="conv-empty">还没有会话</div>
      </div>
    </aside>

    <!-- 右：消息区 -->
    <main class="chat-main">
      <div ref="listRef" class="msg-list">
        <div v-if="!chat.messages.length" class="chat-welcome">
          <h2>👋 开始研读</h2>
          <p>试试这些问题：</p>
          <ul>
            <li>"Mamba 的核心创新是什么？"</li>
            <li>"这篇论文引用了哪些前序工作？"</li>
            <li>"注意力机制和状态空间模型有什么区别？"</li>
          </ul>
        </div>
        <MessageBubble v-for="(m, i) in chat.messages" :key="i" :message="m" />
      </div>

      <div class="input-bar">
        <textarea
          v-model="input"
          class="chat-input"
          rows="2"
          placeholder="提问…（Enter 发送，Shift+Enter 换行）"
          :disabled="chat.streaming"
          @keydown="onKeydown"
        />
        <button v-if="chat.streaming" class="stop-btn" @click="chat.stop()">⏹ 停止生成</button>
        <button v-else class="send-btn" :disabled="!input.trim()" @click="send">发送</button>
      </div>
    </main>
  </div>
</template>

<style scoped>
.chat-page { display: flex; height: calc(100vh - 48px); }
/* ---- 侧边栏 ---- */
.conv-sidebar {
  width: 250px;
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
}
.new-chat-btn {
  margin: 12px;
  border: 1px solid var(--color-primary);
  color: var(--color-primary);
  background: transparent;
  border-radius: 8px;
  padding: 8px;
  cursor: pointer;
  font-size: 13px;
}
.new-chat-btn:hover { background: #eef6f5; }

.scope-box {
  padding: 0 12px 10px;
  border-bottom: 1px solid var(--color-border);
  max-height: 40vh;
  overflow-y: auto;
}
.scope-label { font-size: 11px; color: var(--color-text-secondary); margin-bottom: 6px; }
.scope-count { color: var(--color-primary); }
.scope-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 3px 0;
  cursor: pointer;
}
.scope-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.scope-empty { font-size: 11px; color: var(--color-text-secondary); padding: 4px 0; }

.conv-list { flex: 1; overflow-y: auto; padding: 8px; }
.conv-item {
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
}
.conv-item:hover { background: var(--color-code-bg); }
.conv-item.active { background: #e8f3f2; }
.conv-title {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-meta { font-size: 11px; color: var(--color-text-secondary); margin-top: 2px; }
.conv-empty { text-align: center; color: var(--color-text-secondary); font-size: 12px; margin-top: 20px; }

/* ---- 主区 ---- */
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.msg-list { flex: 1; overflow-y: auto; padding: 20px 8%; }

.chat-welcome {
  text-align: center;
  margin-top: 10vh;
  color: var(--color-text-secondary);
}
.chat-welcome h2 { color: var(--color-text); }
.chat-welcome ul { display: inline-block; text-align: left; }
.chat-welcome li {
  margin: 6px 0;
  cursor: pointer;
}
.chat-welcome li:hover { color: var(--color-primary); }

.input-bar {
  display: flex;
  gap: 10px;
  padding: 12px 8%;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}
.chat-input {
  flex: 1;
  resize: none;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
  font-family: var(--font-sans);
  outline: none;
  line-height: 1.5;
}
.chat-input:focus { border-color: var(--color-primary); }
.chat-input:disabled { background: var(--color-code-bg); }

.send-btn, .stop-btn {
  border: none;
  border-radius: 10px;
  padding: 0 22px;
  font-size: 14px;
  cursor: pointer;
  align-self: flex-end;
  height: 52px;
}
.send-btn {
  background: var(--color-primary);
  color: #fff;
}
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.send-btn:hover:not(:disabled) { background: var(--color-primary-hover); }
.stop-btn {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}
</style>
