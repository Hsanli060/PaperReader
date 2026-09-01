<script setup lang="ts">
/**
 * 消息气泡：一条 user / assistant 消息的完整渲染。
 *
 * 结构：
 *   - user 气泡：右对齐、浅绿底、纯文本
 *   - assistant 气泡：左对齐、白底卡片、MarkdownRenderer 渲染 + 工具调用面板
 *   - streaming 中的 assistant 气泡尾部加闪烁光标
 */
import MarkdownRenderer from './MarkdownRenderer.vue'
import ToolCallDetail from './ToolCallDetail.vue'
import type { ChatMessage } from '@/types'

const props = defineProps<{ message: ChatMessage }>()
</script>

<template>
  <div class="bubble-row" :class="props.message.role">
    <div v-if="props.message.role === 'user'" class="bubble-user">
      {{ props.message.content }}
    </div>

    <div v-else class="bubble-assistant">
      <ToolCallDetail v-if="props.message.toolCalls?.length" :calls="props.message.toolCalls" />
      <template v-if="props.message.content">
        <MarkdownRenderer :text="props.message.content" />
        <span v-if="props.message.streaming" class="streaming-cursor" />
      </template>
      <div v-else-if="props.message.streaming" class="thinking">
        思考中<span class="dots"><i>.</i><i>.</i><i>.</i></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bubble-row {
  display: flex;
  margin-bottom: 14px;
}
.bubble-row.user { justify-content: flex-end; }
.bubble-row.assistant { justify-content: flex-start; }

.bubble-user {
  max-width: 78%;
  background: var(--color-bubble-user);
  border: 1px solid #d3e6e4;
  border-radius: 14px 14px 4px 14px;
  padding: 10px 14px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}

.bubble-assistant {
  max-width: 88%;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 14px 14px 14px 4px;
  padding: 12px 16px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.thinking { color: var(--color-text-secondary); }
.dots i {
  animation: dot-blink 1.2s infinite;
  display: inline-block;
  font-style: normal;
}
.dots i:nth-child(2) { animation-delay: 0.2s; }
.dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot-blink { 0%, 60%, 100% { opacity: 0.2; } 30% { opacity: 1; } }
</style>
