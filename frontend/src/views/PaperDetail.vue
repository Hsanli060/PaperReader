<script setup lang="ts">
/**
 * 论文详情视图：论文元信息 + 四段结构化摘要 + 引用关系图。
 * 数据全部来自 paper store 的 openDetail()（进页面时并行拉三份数据）。
 */
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePaperStore } from '@/stores/paper'
import { useChatStore } from '@/stores/chat'
import CitationGraph from '@/components/CitationGraph.vue'

const route = useRoute()
const router = useRouter()
const paperStore = usePaperStore()
const chatStore = useChatStore()

const paperId = Number(route.params.paper_id)
onMounted(() => paperStore.openDetail(paperId))

const paper = computed(() => paperStore.current)

const SUMMARY_META = [
  { key: 'problem', label: '🎯 要解决的问题' },
  { key: 'method', label: '🔧 核心方法' },
  { key: 'experiment', label: '🧪 关键实验' },
  { key: 'conclusion', label: '✅ 主要结论' },
] as const

function askThis() {
  chatStore.paperScope = paperId
  router.push('/chat')
}

function back() {
  router.push('/library')
}
</script>

<template>
  <div class="detail-page">
    <div v-if="!paper" class="loading">加载中…</div>
    <template v-else>
      <!-- 论文头 -->
      <div class="paper-head pr-card">
        <button class="back-btn" @click="back">‹ 返回论文库</button>
        <h1 class="paper-title">{{ paper.title }}</h1>
        <div class="paper-meta">
          <span v-if="paper.arxiv_id" class="mono">arXiv:{{ paper.arxiv_id }}</span>
          <span v-if="paper.authors.length">{{ paper.authors.slice(0, 6).join('、') }}{{ paper.authors.length > 6 ? ' 等' : '' }}</span>
          <span class="status">{{ paper.status }}</span>
        </div>
        <p v-if="paper.abstract" class="paper-abstract">{{ paper.abstract }}</p>
        <button class="ask-btn" @click="askThis">💬 针对这篇论文提问</button>
      </div>

      <!-- 摘要四段 -->
      <div class="summary-block pr-card">
        <h2>结构化摘要 <span v-if="paperStore.summaryLoading" class="loading-inline">（LLM 生成中，首次约 30~60 秒）</span></h2>
        <div v-if="paperStore.summary" class="summary-grid">
          <div v-for="m in SUMMARY_META" :key="m.key" class="summary-item">
            <div class="summary-label">{{ m.label }}</div>
            <div class="summary-text">{{ paperStore.summary[m.key] }}</div>
          </div>
        </div>
        <div v-else-if="paperStore.summaryLoading" class="loading">正在从向量库抓内容并生成摘要…</div>
        <div v-else class="empty-hint">摘要生成失败（向量库里可能还没有这篇论文的内容）</div>
      </div>

      <!-- 引用图 + 引用列表 -->
      <CitationGraph :paper-title="paper.title" :citations="paperStore.citations" />

      <div class="citations-block pr-card">
        <h2>引用列表 <span v-if="paperStore.citationsLoading" class="loading-inline">（提取中…）</span></h2>
        <div v-if="paperStore.citations.length" class="cite-list">
          <div v-for="(c, i) in paperStore.citations" :key="i" class="cite-item">
            <span class="cite-ref">{{ c.ref }}</span>
            <div class="cite-body">
              <div class="cite-title">{{ c.title }}</div>
              <div class="cite-why">{{ c.why }}</div>
            </div>
          </div>
        </div>
        <div v-else-if="!paperStore.citationsLoading" class="empty-hint">没有提取到引用关系</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-page { max-width: 900px; margin: 0 auto; padding: 20px; display: flex; flex-direction: column; gap: 14px; }

.paper-head { display: flex; flex-direction: column; gap: 10px; }
.back-btn {
  align-self: flex-start;
  border: none;
  background: transparent;
  color: var(--color-primary);
  cursor: pointer;
  font-size: 13px;
  padding: 0;
}
.paper-title { margin: 0; font-size: 22px; line-height: 1.4; }
.paper-meta { display: flex; flex-wrap: wrap; gap: 12px; color: var(--color-text-secondary); font-size: 13px; }
.mono { font-family: var(--font-mono); }
.status {
  background: #eef6f5;
  color: var(--color-primary);
  border-radius: 10px;
  padding: 1px 10px;
  font-size: 12px;
}
.paper-abstract { margin: 0; color: var(--color-text-secondary); line-height: 1.7; font-size: 13px; }
.ask-btn {
  align-self: flex-start;
  border: none;
  background: var(--color-primary);
  color: #fff;
  border-radius: 8px;
  padding: 8px 16px;
  cursor: pointer;
  font-size: 13px;
}
.ask-btn:hover { background: var(--color-primary-hover); }

.summary-block h2, .citations-block h2 { margin: 0 0 12px; font-size: 17px; }
.loading-inline { font-size: 12px; font-weight: 400; color: var(--color-text-secondary); }

.summary-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.summary-item {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 12px;
  background: #fdfcfa;
}
.summary-label { font-weight: 600; margin-bottom: 6px; font-size: 13px; }
.summary-text { font-size: 13px; line-height: 1.7; color: var(--color-text); }

.cite-list { display: flex; flex-direction: column; gap: 10px; }
.cite-item {
  display: flex;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--color-border);
}
.cite-item:last-child { border-bottom: none; }
.cite-ref {
  font-family: var(--font-mono);
  color: var(--color-accent-amber);
  font-size: 13px;
  min-width: 34px;
}
.cite-title { font-size: 13px; font-weight: 600; }
.cite-why { font-size: 12px; color: var(--color-text-secondary); margin-top: 2px; }

.loading, .empty-hint {
  color: var(--color-text-secondary);
  font-size: 13px;
  padding: 10px 0;
}
</style>
