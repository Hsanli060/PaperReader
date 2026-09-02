<script setup lang="ts">
/**
 * 论文卡片：Library 列表里的一张论文。
 * 信息密度取舍：标题、作者（前 3 位 + 等）、状态徽章、摘要两行截断。
 * 三个动作：进详情 / 去问这篇 / 删除（带确认）。
 */
import { computed } from 'vue'
import type { Paper } from '@/types'

const props = defineProps<{ paper: Paper }>()
const emit = defineEmits<{
  (e: 'detail', id: number): void
  (e: 'chat', id: number): void
  (e: 'delete', id: number): void
}>()

/** 状态徽章的文案和颜色（FIX-2 增加 failed） */
const STATUS_META: Record<string, { label: string; color: string; pulse?: boolean }> = {
  pending: { label: '处理中', color: '#9ca3af', pulse: true },
  downloaded: { label: '已下载', color: '#b45309', pulse: true },
  parsed: { label: '已解析', color: '#2563eb', pulse: true },
  indexed: { label: '已索引', color: '#0f6e6b' },
  failed: { label: '失败', color: '#dc2626' },
}
const statusMeta = computed(() => STATUS_META[props.paper.status] ?? STATUS_META.pending)

/** 作者太多只显示前 3 个 */
const authorText = computed(() => {
  const a = props.paper.authors
  if (!a?.length) return '作者未录入'
  return a.length > 3 ? `${a.slice(0, 3).join('、')} 等` : a.join('、')
})

/** 摘要两行截断由 CSS line-clamp 做，这里只兜 null */
const abstractText = computed(() => props.paper.abstract ?? '（无摘要——上传的本地 PDF 没有元数据）')
</script>

<template>
  <div class="paper-card pr-card">
    <div class="card-head">
      <span
        class="status-badge"
        :class="{ pulse: statusMeta.pulse }"
        :style="{ background: statusMeta.color }"
        :title="paper.last_error ?? undefined"
      >{{ statusMeta.label }}</span>
      <span v-if="paper.arxiv_id" class="arxiv-id">arXiv:{{ paper.arxiv_id }}</span>
      <span class="head-actions">
        <button class="mini-btn" title="查看详情" @click="emit('detail', paper.id)">详情</button>
        <button class="mini-btn" title="针对这篇论文提问" @click="emit('chat', paper.id)">去问它</button>
        <button class="mini-btn danger" title="删除" @click="emit('delete', paper.id)">删除</button>
      </span>
    </div>

    <h3 class="card-title" @click="emit('detail', paper.id)">{{ paper.title }}</h3>
    <div class="card-authors">{{ authorText }} · {{ paper.created_at.slice(0, 10) }}</div>
    <p class="card-abstract">{{ abstractText }}</p>
    <div v-if="paper.status === 'failed' && paper.last_error" class="error-hint" :title="paper.last_error">
      ❌ {{ paper.last_error.slice(0, 80) }}
    </div>
  </div>
</template>

<style scoped>
.paper-card { display: flex; flex-direction: column; gap: 8px; }
.card-head { display: flex; align-items: center; gap: 10px; }
.status-badge {
  color: #fff;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
}
.status-badge.pulse { animation: badge-pulse 1.2s ease-in-out infinite; }
@keyframes badge-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
.arxiv-id {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-text-secondary);
}
.head-actions { margin-left: auto; display: flex; gap: 6px; }
.mini-btn {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
  color: var(--color-text);
}
.mini-btn:hover { border-color: var(--color-primary); color: var(--color-primary); }
.mini-btn.danger:hover { border-color: #dc2626; color: #dc2626; }

.card-title {
  margin: 0;
  font-size: 16px;
  line-height: 1.4;
  cursor: pointer;
}
.card-title:hover { color: var(--color-primary); }
.card-authors { font-size: 12px; color: var(--color-text-secondary); }
.card-abstract {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;      /* 两行截断 */
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.error-hint { font-size: 11px; color: #dc2626; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
