/**
 * 论文库状态 store：分页列表 / 搜索 / 添加（arXiv + 上传）/ 详情 / 摘要引用。
 *
 * Library.vue 和 PaperDetail.vue 都从这里取数——组件只管画，数据怎么来的不用知道。
 * FIX-2：添加后后台流水线轮询——pending/downloaded/parsed → indexed 前每 3 秒查一次 status。
 */
import { defineStore } from 'pinia'
import { http, apiErrorMessage } from '@/services/api'
import type { Paper, PaperPage, PaperSummary, Citation } from '@/types'

export const usePaperStore = defineStore('paper', {
  state: () => ({
    /** 当前列表页的数据 */
    items: [] as Paper[],
    total: 0,
    page: 1,
    pageSize: 10,
    loading: false,

    /** 详情页正在看的论文 */
    current: null as Paper | null,

    /** 详情页的摘要四段（null=还没生成/加载失败） */
    summary: null as PaperSummary | null,
    summaryLoading: false,

    /** 详情页的引用列表 */
    citations: [] as Citation[],
    citationsLoading: false,

    /** 轮询中的论文 ID → 定时器句柄（FIX-2） */
    _pollTimers: {} as Record<number, ReturnType<typeof setInterval>>,
  }),

  actions: {
    /** 拉一页论文（全局共享库）。不传参=用当前页配置；keyword 只在前端过滤 */
    async fetchPage(page?: number, pageSize?: number): Promise<void> {
      const p = page ?? this.page
      const size = pageSize ?? this.pageSize
      this.loading = true
      try {
        const resp = await http.get<PaperPage>('/papers', {
          params: { page: p, page_size: size },
        })
        this.items = resp.data.items
        this.total = resp.data.total
        this.page = resp.data.page
        this.pageSize = resp.data.page_size
        // 对未完成 status 的论文自动续上轮询（页面刷新后也能续）
        for (const paper of this.items) {
          if (['pending', 'downloaded', 'parsed'].includes(paper.status)) {
            this.startPolling(paper.id)
          }
        }
      } finally {
        this.loading = false
      }
    },

    /** 对单篇论文开启轮询：每 3 秒查一次 status，indexed/failed 后停止 */
    startPolling(paperId: number): void {
      if (this._pollTimers[paperId]) return // 已在轮询
      const timer = setInterval(async () => {
        try {
          const resp = await http.get<Paper>(`/papers/${paperId}`)
          const updated = resp.data
          // 同步到列表里的那一项（让徽章动起来）
          const idx = this.items.findIndex((p) => p.id === paperId)
          if (idx !== -1) this.items[idx] = updated
          // 详情页正在看这篇也同步
          if (this.current?.id === paperId) this.current = updated
          if (['indexed', 'failed'].includes(updated.status)) {
            this.stopPolling(paperId)
            // 完成后刷新一页（让总数/排序正确）
            if (updated.status === 'indexed') this.fetchPage()
          }
        } catch {
          // 网络抖动忽略，下一次再试
        }
      }, 3000)
      this._pollTimers[paperId] = timer
    },

    stopPolling(paperId: number): void {
      const t = this._pollTimers[paperId]
      if (t) {
        clearInterval(t)
        delete this._pollTimers[paperId]
      }
    },

    stopAllPolling(): void {
      for (const id of Object.keys(this._pollTimers)) this.stopPolling(Number(id))
    },

    /** 按 arXiv ID/链接添加。返回错误文案或 null；duplicated=true 时后端不重复跑流水线 */
    async addByArxiv(input: string): Promise<{ ok: boolean; message: string }> {
      try {
        const resp = await http.post('/papers/arxiv', { arxiv: input })
        if (resp.data.duplicated) {
          await this.fetchPage(1)
          return { ok: true, message: '该论文已在库里，已为你定位' }
        }
        // 非重复：后端立即返回 pending，新论文插到列表头
        await this.fetchPage(1)
        // 对 pending 论文开启轮询（让徽章从 pending → indexed 自动点亮）
        const newId = resp.data.id as number
        if (newId) this.startPolling(newId)
        return { ok: true, message: '已受理，后台正在处理（3秒后自动刷新状态）' }
      } catch (err) {
        return { ok: false, message: apiErrorMessage(err) }
      }
    },

    /** 上传本地 PDF（后端后台跑 parse→split→index 流水线，前端轮询看进度） */
    async uploadPdf(file: File): Promise<{ ok: boolean; message: string }> {
      const form = new FormData()
      form.append('file', file)
      try {
        const resp = await http.post('/papers/upload', form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 30_000,   // FIX-2 后上传接口秒回 pending，不再需要 5 分钟超时
        })
        await this.fetchPage(1)
        const newId = resp.data.id as number
        if (newId) this.startPolling(newId)
        return { ok: true, message: '上传成功，后台正在解析（3秒后自动刷新状态）' }
      } catch (err) {
        return { ok: false, message: apiErrorMessage(err) }
      }
    },

    /** 打开详情页：拉论文本体 + 并行拉摘要和引用 */
    async openDetail(paperId: number): Promise<void> {
      this.current = null
      this.summary = null
      this.citations = []
      this.summaryLoading = true
      this.citationsLoading = true

      // 论文本体现拉
      const detailResp = await http.get<Paper>(`/papers/${paperId}`)
      this.current = detailResp.data

      // 摘要和引用独立拉（两个都是 LLM 慢接口，各自转圈互不阻塞）
      http.get(`/papers/${paperId}/summary`)
        .then((resp) => { this.summary = resp.data })
        .catch(() => { this.summary = null })
        .finally(() => { this.summaryLoading = false })

      http.get<{ items: Citation[] }>(`/papers/${paperId}/citations`)
        .then((resp) => { this.citations = resp.data.items })
        .catch(() => { this.citations = [] })
        .finally(() => { this.citationsLoading = false })
    },

    /** 删除论文（后端会连向量库一起删） */
    async removePaper(paperId: number): Promise<{ ok: boolean; message: string }> {
      try {
        this.stopPolling(paperId)
        await http.delete(`/papers/${paperId}`)
        await this.fetchPage()
        return { ok: true, message: '已删除' }
      } catch (err) {
        return { ok: false, message: apiErrorMessage(err) }
      }
    },
  },
})
