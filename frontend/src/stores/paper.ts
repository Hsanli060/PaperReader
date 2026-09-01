/**
 * 论文库状态 store：分页列表 / 搜索 / 添加（arXiv + 上传）/ 详情 / 摘要引用。
 *
 * Library.vue 和 PaperDetail.vue 都从这里取数——组件只管画，数据怎么来的不用知道。
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
  }),

  actions: {
    /** 拉一页我的论文。不传参=用当前页配置；keyword 只在前端过滤（教学规模列表小） */
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
      } finally {
        this.loading = false
      }
    },

    /** 按 arXiv ID/链接添加。返回错误文案或 null；duplicated=true 时后端不重复跑流水线 */
    async addByArxiv(input: string): Promise<{ ok: boolean; message: string }> {
      try {
        const resp = await http.post('/papers/arxiv', { arxiv: input })
        // 新论文插到列表头（列表按 id 降序，新论文 id 最大）
        await this.fetchPage(1)
        return { ok: true, message: resp.data.duplicated ? '该论文已在你的库里，已为你定位' : '添加成功，已索引' }
      } catch (err) {
        return { ok: false, message: apiErrorMessage(err) }
      }
    },

    /** 上传本地 PDF（后端会补跑 parse→split→index 流水线） */
    async uploadPdf(file: File): Promise<{ ok: boolean; message: string }> {
      const form = new FormData()
      form.append('file', file)
      try {
        await http.post('/papers/upload', form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 300_000,   // 大 PDF 解析+向量化要几分钟
        })
        await this.fetchPage(1)
        return { ok: true, message: '上传成功，已索引' }
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
        await http.delete(`/papers/${paperId}`)
        await this.fetchPage()
        return { ok: true, message: '已删除' }
      } catch (err) {
        return { ok: false, message: apiErrorMessage(err) }
      }
    },
  },
})
