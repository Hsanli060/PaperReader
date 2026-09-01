<script setup lang="ts">
/**
 * 工具调用过程折叠面板：展示模型这次回答里"偷偷"做了什么。
 *
 * 数据从哪来：chat store 攒的 ToolCallInfo[]（SSE 的 tool_call/tool_result 事件）。
 * 交互：默认收起（一行摘要），点开看每次调用的参数 JSON。
 */
import { computed, ref } from 'vue'
import type { ToolCallInfo } from '@/types'

const props = defineProps<{ calls: ToolCallInfo[] }>()

const open = ref(false)   // 收起/展开

/** 工具名的中文说明（名字本身是英文函数名，用户看不懂，翻译一层） */
const TOOL_LABELS: Record<string, string> = {
  search_paper: '📚 知识库检索',
  web_search: '🌐 联网搜索',
  summarize_paper: '📝 生成摘要',
  extract_citations: '🔗 提取引用',
}
const label = (name: string) => TOOL_LABELS[name] ?? name

/** 参数 JSON 美化（arguments 是模型给的 JSON 字符串） */
const pretty = (args: string) => {
  try {
    return JSON.stringify(JSON.parse(args), null, 2)
  } catch {
    return args
  }
}

const count = computed(() => props.calls.length)
</script>

<template>
  <div v-if="count > 0" class="tool-panel">
    <div class="tool-toggle" @click="open = !open">
      <span class="tool-toggle-arrow">{{ open ? '▾' : '▸' }}</span>
      <span>调用过程</span>
      <span class="tool-count">{{ count }} 次工具调用</span>
    </div>
    <transition name="slide">
      <div v-if="open" class="tool-list">
        <div v-for="(call, i) in calls" :key="i" class="tool-item">
          <div class="tool-head">
            <span class="tool-name">{{ label(call.name) }}</span>
            <span class="tool-status">
              {{ call.resultSummary ? '✓ 完成' : '⏳ 执行中' }}
            </span>
          </div>
          <pre class="tool-args">{{ pretty(call.arguments) }}</pre>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.tool-panel {
  margin-bottom: 8px;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  font-size: 12px;
}
.tool-toggle {
  padding: 6px 10px;
  background: var(--color-code-bg);
  cursor: pointer;
  color: var(--color-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
}
.tool-toggle-arrow { width: 10px; }
.tool-count { margin-left: auto; opacity: 0.7; }
.tool-list { padding: 8px 10px; }
.tool-item { margin-bottom: 10px; }
.tool-item:last-child { margin-bottom: 0; }
.tool-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
}
.tool-name { font-weight: 600; }
.tool-status { color: var(--color-text-secondary); }
.tool-args {
  margin: 0;
  padding: 6px 8px;
  background: var(--color-code-bg);
  border-radius: 6px;
  font-size: 11px;
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow-y: auto;
}
/* 展开收起的过渡 */
.slide-enter-active, .slide-leave-active { transition: all 0.15s ease; }
.slide-enter-from, .slide-leave-to { opacity: 0; }
</style>
