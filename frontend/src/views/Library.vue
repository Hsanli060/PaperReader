<script setup lang="ts">
/**
 * 论文库视图：顶部操作条（添加/上传/搜索/分页）+ 卡片网格。
 * 搜索是前端过滤（教学规模足够；后端分页接口没有 keyword 参数）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { usePaperStore } from '@/stores/paper'
import { useChatStore } from '@/stores/chat'
import PaperCard from '@/components/PaperCard.vue'
import FileUploader from '@/components/FileUploader.vue'
import type { Paper } from '@/types'

const paperStore = usePaperStore()
const chatStore = useChatStore()
const router = useRouter()

const keyword = ref('')
const busy = ref(false)          // 添加/上传进行中（整个操作条转圈防重复提交）
const banner = ref<{ type: 'ok' | 'err'; text: string } | null>(null)

onMounted(() => paperStore.fetchPage())

/** 关键字过滤（标题/作者/arXiv ID 都搜） */
const filtered = computed<Paper[]>(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return paperStore.items
  return paperStore.items.filter(
    (p) =>
      p.title.toLowerCase().includes(kw) ||
      (p.arxiv_id ?? '').includes(kw) ||
      p.authors.some((a) => a.toLowerCase().includes(kw)),
  )
})

const totalPages = computed(() => Math.max(1, Math.ceil(paperStore.total / paperStore.pageSize)))

async function goPage(p: number) {
  if (p < 1 || p > totalPages.value || paperStore.loading) return
  await paperStore.fetchPage(p)
}

async function onAddArxiv(input: string) {
  busy.value = true
  banner.value = null
  const result = await paperStore.addByArxiv(input)
  banner.value = { type: result.ok ? 'ok' : 'err', text: result.message }
  busy.value = false
}

async function onUpload(file: File) {
  busy.value = true
  banner.value = null
  const result = await paperStore.uploadPdf(file)
  banner.value = { type: result.ok ? 'ok' : 'err', text: result.message }
  busy.value = false
}

async function onDelete(id: number) {
  if (!window.confirm('删除后向量库里的内容也一并清除，确定？')) return
  const result = await paperStore.removePaper(id)
  if (!result.ok) banner.value = { type: 'err', text: result.message }
}

function toDetail(id: number) {
  router.push(`/paper/${id}`)
}

/** 去问它：把范围带到聊天页 */
function toChat(id: number) {
  chatStore.paperScope = id
  router.push('/chat')
}
</script>

<template>
  <div class="library-page">
    <div class="toolbar pr-card">
      <FileUploader :disabled="busy" @add-arxiv="onAddArxiv" @upload="onUpload" />
    </div>

    <div v-if="banner" class="banner" :class="banner.type">{{ banner.text }}</div>

    <div class="filter-row">
      <input v-model="keyword" class="search-input" placeholder="🔍 搜索标题 / 作者 / arXiv ID" />
      <span class="count-hint">{{ filtered.length }} / {{ paperStore.total }} 篇</span>
    </div>

    <div v-if="paperStore.loading" class="loading">加载中…</div>
    <div v-else-if="!filtered.length" class="empty">
      {{ keyword ? '没有匹配的论文' : '库里还没有论文 —— 上面粘贴 arXiv 链接添加第一篇吧' }}
    </div>
    <div v-else class="card-grid">
      <PaperCard
        v-for="p in filtered"
        :key="p.id"
        :paper="p"
        @detail="toDetail"
        @chat="toChat"
        @delete="onDelete"
      />
    </div>

    <div v-if="totalPages > 1" class="pager">
      <button :disabled="paperStore.page <= 1" @click="goPage(paperStore.page - 1)">‹ 上一页</button>
      <span>{{ paperStore.page }} / {{ totalPages }}</span>
      <button :disabled="paperStore.page >= totalPages" @click="goPage(paperStore.page + 1)">下一页 ›</button>
    </div>
  </div>
</template>

<style scoped>
.library-page { max-width: 1080px; margin: 0 auto; padding: 20px; }

.toolbar { margin-bottom: 14px; }
.banner {
  padding: 8px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 13px;
}
.banner.ok { background: #eefaf5; color: #0a7d4f; border: 1px solid #bfe8d6; }
.banner.err { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }

.filter-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.search-input {
  flex: 0 0 320px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 8px 12px;
  outline: none;
  background: var(--color-surface);
}
.search-input:focus { border-color: var(--color-primary); }
.count-hint { font-size: 12px; color: var(--color-text-secondary); }

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 14px;
}

.loading, .empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 40px 0;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  margin: 20px 0;
  font-size: 13px;
}
.pager button {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 8px;
  padding: 5px 14px;
  cursor: pointer;
}
.pager button:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
