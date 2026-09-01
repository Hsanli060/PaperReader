<script setup lang="ts">
/**
 * 引用关系力导向图（D3 v7）。
 *
 * 数据：citations = 这篇论文引用了谁。图的结构：中心节点（本论文）→ 一圈被引论文。
 * D3 三个模块各司其职：
 *   forceSimulation  物理引擎：节点互斥 + 连线拉扯，迭代几百次收敛到"舒服"的布局
 *   drag             用户拖节点，拖完布局自动重新平衡
 *   zoom             画布缩放平移
 *
 * Vue + D3 的协作方式：Vue 管"这块区域存在"，D3 直接操作 <svg> 里的 DOM
 * （力导向是高频逐帧更新，走 Vue 响应式反而慢，这是官方认可的分工）。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as d3 from 'd3'
import type { Citation } from '@/types'

const props = defineProps<{ paperTitle: string; citations: Citation[] }>()

const svgRef = ref<SVGSVGElement | null>(null)
const hasData = ref(false)

/** 清掉旧图（重复渲染 / 组件卸载都走这里，防内存泄漏） */
function clearSvg() {
  d3.select(svgRef.value).selectAll('*').remove()
}

function render() {
  clearSvg()
  const svgEl = svgRef.value
  if (!svgEl || !props.citations.length) return
  hasData.value = true

  // ---- 1. 建数据：中心节点 + 被引节点 + 连线 ----
  const nodes: { id: number; label: string; kind: 'center' | 'ref'; why?: string }[] = [
    { id: 0, label: props.paperTitle, kind: 'center' },
  ]
  const links: { source: number; target: number }[] = []
  props.citations.forEach((c, i) => {
    nodes.push({ id: i + 1, label: c.ref + ' ' + c.title, kind: 'ref', why: c.why })
    links.push({ source: 0, target: i + 1 })
  })

  const width = svgEl.clientWidth || 600
  const height = 420

  const svg = d3.select(svgEl).attr('viewBox', `0 0 ${width} ${height}`)

  // ---- 2. zoom：把所有内容包进一个 <g>，缩放平移作用于这个 g ----
  const g = svg.append('g')
  svg.call(
    d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.4, 2.5])
      .on('zoom', (event) => g.attr('transform', event.transform.toString())),
  )

  // ---- 3. 力导向模拟 ----
  const simulation = d3
    .forceSimulation(nodes as any)
    .force('link', d3.forceLink(links as any).id((d: any) => d.id).distance(110))
    .force('charge', d3.forceManyBody().strength(-350))     // 节点互斥
    .force('center', d3.forceCenter(width / 2, height / 2)) // 整体居中

  // 连线
  const link = g
    .append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', '#c9c6bf')
    .attr('stroke-width', 1.5)

  // 节点组（圆 + 文字）
  const node = g
    .append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .call(
      d3
        .drag<any, any>()
        .on('start', (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on('drag', (event, d) => {
          d.fx = event.x
          d.fy = event.y
        })
        .on('end', (event, d) => {
          if (!event.active) simulation.alphaTarget(0)
          d.fx = null
          d.fy = null
        }),
    )

  node
    .append('circle')
    .attr('r', (d: any) => (d.kind === 'center' ? 26 : 14))
    .attr('fill', (d: any) => (d.kind === 'center' ? '#0f6e6b' : '#e8f3f2'))
    .attr('stroke', (d: any) => (d.kind === 'center' ? '#0d5d5a' : '#0f6e6b'))
    .attr('stroke-width', 2)

  node
    .append('text')
    .text((d: any) => (d.label.length > 22 ? d.label.slice(0, 22) + '…' : d.label))
    .attr('x', (d: any) => (d.kind === 'center' ? 0 : 18))
    .attr('y', (d: any) => (d.kind === 'center' ? 42 : 4))
    .attr('text-anchor', (d: any) => (d.kind === 'center' ? 'middle' : 'start'))
    .attr('font-size', (d: any) => (d.kind === 'center' ? 12 : 10))
    .attr('fill', (d: any) => (d.kind === 'center' ? '#0f6e6b' : '#4a4d51'))

  // 悬停提示（title 是浏览器原生 tooltip，教学项目够用）
  node.selectAll('circle').append('title').text((d: any) => d.why ?? d.label)

  // ---- 4. 每个物理 tick 重画位置 ----
  simulation.on('tick', () => {
    link
      .attr('x1', (d: any) => d.source.x)
      .attr('y1', (d: any) => d.source.y)
      .attr('x2', (d: any) => d.target.x)
      .attr('y2', (d: any) => d.target.y)
    node.attr('transform', (d: any) => `translate(${d.x},${d.y})`)
  })
}

onMounted(render)
watch(() => props.citations, render)   // 数据异步到达后重画
onBeforeUnmount(clearSvg)
</script>

<template>
  <div class="graph-wrap pr-card">
    <div class="graph-title">引用关系图 <span class="graph-hint">可拖拽节点 · 滚轮缩放 · 悬停看引用原因</span></div>
    <svg ref="svgRef" class="graph-svg" />
    <div v-if="!hasData" class="graph-empty">
      {{ citations.length ? '渲染中…' : '暂无引用数据（LLM 生成中或该论文没有参考文献部分）' }}
    </div>
  </div>
</template>

<style scoped>
.graph-wrap { padding: 12px; }
.graph-title { font-weight: 600; margin-bottom: 8px; }
.graph-hint { font-size: 12px; font-weight: 400; color: var(--color-text-secondary); }
.graph-svg { width: 100%; height: 420px; display: block; }
.graph-empty {
  position: absolute;
  inset: 48px 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
  font-size: 13px;
  pointer-events: none;
}
.graph-wrap { position: relative; }
</style>
