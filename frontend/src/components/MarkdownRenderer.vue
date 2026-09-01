<script setup lang="ts">
/**
 * Markdown + LaTeX 统一渲染器。
 *
 * 谁用它：MessageBubble（聊天回答）、PaperDetail（摘要四段）、Login 无关。
 *
 * 技术栈分工：
 *   markdown-it      → Markdown 语法 → HTML（表格/列表/代码块…）
 *   KaTeX 自定义规则  → 行内 $...$ 和块级 $$...$$ → KaTeX 排版（自写规则，不引第三方插件，
 *                      因为 markdown-it-katex 系插件年久失修，规则本身只有几十行）
 *   highlight.js     → 代码块上色
 *
 * 流式场景的关键设计：每来一片文本就整串重渲染。markdown-it 每次都从头
 * 解析整段字符串——内容几 KB 时毫秒级完成，不需要增量解析的复杂度。
 */
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import katex from 'katex'
import hljs from 'highlight.js'
import 'katex/dist/katex.min.css'
import 'highlight.js/styles/github-dark.css'

const props = defineProps<{ text: string }>()

// ---------- markdown-it 实例（模块级单例，所有组件共用一个解析器） ----------

const md: MarkdownIt = new MarkdownIt({
  html: false,        // 不许原始 HTML 进来（防 XSS；后端回答是我们自己的 LLM 生成的，也走不出这层）
  linkify: true,      // 裸 URL 自动变链接
  breaks: true,       // 单个换行也换行（聊天场景更像聊天）
  highlight(code, lang) {
    // 代码块上色：认识的语言用 hljs，不认识的原样输出
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre class="hljs"><code>${hljs.highlight(code, { language: lang, ignoreIllegals: true }).value}</code></pre>`
      } catch { /* 落到下面的兜底 */ }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(code)}</code></pre>`
  },
})

// ---------- KaTeX 规则（仿 markdown-it-katex 的实现，剥掉历史包袱） ----------

/** 把一段公式源码交给 KaTeX 渲染；渲染失败原样返回（别让一条坏公式炸掉整段渲染） */
function renderKatex(src: string, displayMode: boolean): string {
  try {
    return katex.renderToString(src, { displayMode, throwOnError: false })
  } catch {
    return src
  }
}

/** 行内公式 $...$ 的规则。要处理转义 \$：数反斜杠奇偶判断是真公式还是文字里的美元符号 */
function inlineMathRule(state: any, silent: boolean) {
  const src = state.src
  const pos = state.pos
  if (src[pos] !== '$') return false

  // 数 pos 之前连续反斜杠的个数——偶数个说明这个 $ 没被转义，是公式边界
  let backslashes = 0
  for (let i = pos - 1; i >= 0 && src[i] === '\\'; i--) backslashes++
  const escaped = backslashes % 2 === 1
  if (escaped) return false

  // 找配对的结束 $（跳过被 \$ 转义的）
  let end = -1
  for (let i = pos + 1; i < src.length; i++) {
    if (src[i] === '\\') { i++; continue }        // \x 两个字符一起跳过
    if (src[i] === '$') { end = i; break }
    if (src[i] === '\n') break                    // 行内公式不许跨行
  }
  if (end < 0 || end - pos < 2) return false      // 没配对 / 空公式 → 当普通文字

  if (!silent) {
    const token = state.push('math_inline', 'math', 0)
    token.markup = '$'
    token.content = src.slice(pos + 1, end)
  }
  state.pos = end + 1
  return true
}

/** 块级公式 $$ ... $$ 的规则 */
function blockMathRule(state: any, startLine: number, endLine: number, silent: boolean) {
  let pos = state.bMarks[startLine] + state.tShift[startLine]
  const max = state.eMarks[startLine]
  if (pos + 2 > max) return false
  if (state.src.slice(pos, pos + 2) !== '$$') return false

  // 从本行开始找结束的 $$
  let firstLine = state.src.slice(pos + 2, max)
  let lastLine = ''
  let found = false
  let next = startLine
  if (firstLine.trim().endsWith('$$')) {
    firstLine = firstLine.trim().slice(0, -2)
    found = true
  }
  while (!found) {
    next++
    if (next >= endLine) break
    pos = state.bMarks[next] + state.tShift[next]
    const lineStr = state.src.slice(pos, state.eMarks[next])
    const endIdx = lineStr.indexOf('$$')
    if (endIdx !== -1) {
      lastLine = lineStr.slice(0, endIdx)
      found = true
    } else {
      lastLine += '\n' + lineStr
    }
  }
  if (!found) return false

  if (silent) return true
  const token = state.push('math_block', 'math', 0)
  token.block = true
  token.content = (firstLine && lastLine ? firstLine + '\n' + lastLine : firstLine + lastLine).trim()
  token.map = [startLine, next + 1]
  state.line = next + 1
  return true
}

md.inline.ruler.before('escape', 'math_inline', inlineMathRule)
md.block.ruler.before('fence', 'math_block', blockMathRule)

/** 渲染器：把 token 变成 HTML。math 类 token 交给 KaTeX，其他走默认 */
md.renderer.rules.math_inline = (tokens, idx) => renderKatex(tokens[idx].content, false)
md.renderer.rules.math_block = (tokens, idx) => renderKatex(tokens[idx].content, true)

// ---------- 组件逻辑 ----------

/** text 一变就重渲染（computed 缓存，流式期间频繁触发也没压力） */
const html = computed(() => md.render(props.text ?? ''))
</script>

<template>
  <!-- v-html：markdown-it 已经关了原始 HTML 且转义了文本节点，注入是安全的 -->
  <div class="md-body" v-html="html" />
</template>
