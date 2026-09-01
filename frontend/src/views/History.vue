<script setup lang="ts">
/**
 * 对话历史视图：所有会话的列表 + 展开回放 + 删除 + 继续聊。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { http } from '@/services/api'
import { useChatStore } from '@/stores/chat'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import type { Conversation, ConversationDetail } from '@/types'

const chatStore = useChatStore()
const router = useRouter()

const conversations = ref<Conversation[]>([])
const loading = ref(true)
/** 正在展开回放的会话 ID（0 = 没有展开的） */
const expandedId = ref(0)
const detail = ref<ConversationDetail | null>(null)
const detailLoading = ref(false)

onMounted(async () => {
  const resp = await http.get<{ items: Conversation[] }>('/history')
  conversations.value = resp.data.items
  loading.value = false
})

/** 展开/收起一条会话的消息回放 */
async function toggle(c: Conversation) {
  if (expandedId.value === c.id) {
    expandedId.value = 0
    detail.value = null
    return
  }
  expandedId.value = c.id
  detail.value = null
  detailLoading.value = true
  try {
    const resp = await http.get<ConversationDetail>(`/history/${c.id}`)
    detail.value = resp.data
  } finally {
    detailLoading.value = false
  }
}

/** 继续聊：载入进 chat store → 跳聊天页 */
async function continueChat(c: Conversation) {
  await chatStore.loadConversation(c.id)
  router.push('/chat')
}

async function remove(c: Conversation) {
  if (!window.confirm(`删除会话「${c.title}」？消息记录不可恢复`)) return
  await http.delete(`/history/${c.id}`)
  conversations.value = conversations.value.filter((x) => x.id !== c.id)
  if (expandedId.value === c.id) {
    expandedId.value = 0
    detail.value = null
  }
}
</script>

<template>
  <div class="history-page">
    <h1>对话历史</h1>

    <div v-if="loading" class="loading">加载中…</div>
    <div v-else-if="!conversations.length" class="empty">还没有任何对话</div>

    <div v-else class="conv-list">
      <div v-for="c in conversations" :key="c.id" class="conv-row pr-card">
        <div class="row-head" @click="toggle(c)">
          <div class="row-info">
            <div class="row-title">{{ c.title }}</div>
            <div class="row-meta">
              {{ c.created_at.slice(0, 16) }} · {{ c.message_count }} 条消息
              <span v-if="c.paper_id" class="paper-tag">论文 #{{ c.paper_id }}</span>
            </div>
          </div>
          <div class="row-actions">
            <button class="mini-btn" @click.stop="continueChat(c)">继续聊</button>
            <button class="mini-btn danger" @click.stop="remove(c)">删除</button>
            <span class="expand-arrow">{{ expandedId === c.id ? '▾' : '▸' }}</span>
          </div>
        </div>

        <div v-if="expandedId === c.id" class="row-body">
          <div v-if="detailLoading" class="loading">载入消息…</div>
          <template v-else-if="detail">
            <div v-for="(m, i) in detail.messages" :key="i" class="replay-msg" :class="m.role">
              <div class="replay-role">{{ m.role === 'user' ? '🧑 你' : '🤖 助手' }}</div>
              <MarkdownRenderer v-if="m.role === 'assistant'" :text="m.content" class="replay-content" />
              <div v-else class="replay-content replay-user-text">{{ m.content }}</div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.history-page { max-width: 860px; margin: 0 auto; padding: 20px; }
h1 { font-size: 22px; }

.conv-list { display: flex; flex-direction: column; gap: 10px; }
.row-head {
  display: flex;
  align-items: center;
  cursor: pointer;
  gap: 10px;
}
.row-info { flex: 1; min-width: 0; }
.row-title {
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.row-meta { font-size: 12px; color: var(--color-text-secondary); margin-top: 3px; }
.paper-tag {
  background: #eef6f5;
  color: var(--color-primary);
  border-radius: 8px;
  padding: 1px 8px;
  margin-left: 6px;
  font-size: 11px;
}
.row-actions { display: flex; align-items: center; gap: 6px; }
.expand-arrow { color: var(--color-text-secondary); width: 14px; }

.mini-btn {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
}
.mini-btn:hover { border-color: var(--color-primary); color: var(--color-primary); }
.mini-btn.danger:hover { border-color: #dc2626; color: #dc2626; }

.row-body {
  margin-top: 12px;
  border-top: 1px solid var(--color-border);
  padding-top: 12px;
  max-height: 420px;
  overflow-y: auto;
}
.replay-msg { margin-bottom: 12px; }
.replay-role { font-size: 11px; color: var(--color-text-secondary); margin-bottom: 2px; }
.replay-content {
  background: var(--color-code-bg);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.6;
}
.replay-msg.user .replay-content { background: var(--color-bubble-user); }
.replay-user-text { white-space: pre-wrap; }

.loading, .empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 30px 0;
}
</style>
