"""
Agent 上下文压缩管线（借鉴 Claude Code context compact 机制）

每次 LLM 调用前由主循环调用 prepare() 跑一遍（react_agent.run_stream_async）：
    ① 当前批裁剪：本轮新到的工具结果，单条超硬限 / 批量超预算 → 截断留预览
    ② 已消费老化：模型已消化过的旧结果（最后一条 assistant 之前），消息总字符
       超软线时压成一行指针（保留论文/章节等结构信息；工具只读可重取，属安全降级）
    ③ 反应式兜底：上下文超限报错 → emergency() 全量压旧后重试一次（在 react_agent 接线）

设计约束（对照 s08 的工程细节）：
    - 只改 content 字符串、从不禁删消息 → tool_calls/tool 配对天然完整（OpenAI 协议友好）
    - 幂等：已压缩带 [已压缩] 标记、已截断带 [已截断 标记，重复跑不二次压缩
    - 零 LLM 成本：纯字符串处理；LLM 摘要（s08 的最高一级）留作后续扩展
    - 新鲜度规则：未消费批（最后一条 assistant 之后的结果）保持全文；
      已消费的只压掉除最近 K 条以外的部分
    - 压到软线的 80% 即停（留余量，避免下一轮立刻又触发）

阈值按实测校准：search_paper 单条 ≈5K 字符、web_search ≈2.2K、
summarize_paper ≈0.5K；默认值见 config.AGENT_*（可通过 .env 调整）。
"""
import json

from app.config import settings

COMPACT_MARK = "[已压缩]"
TRUNC_MARK = "[已截断]"


class ContextCompactor:
    """上下文压缩器（无状态，随 ReactAgent 实例化一次即可复用）"""

    def __init__(self,
                 result_char_limit: int | None = None,
                 batch_char_limit: int | None = None,
                 context_char_limit: int | None = None,
                 keep_recent: int | None = None,
                 min_compact_len: int = 400):
        """
        :param result_char_limit: 单条工具结果进上下文的硬上限（超出截断留预览）
        :param batch_char_limit: 单轮新增工具结果的批量预算（并行多工具按份数均分）
        :param context_char_limit: 消息总字符软线：超过才触发旧结果老化
        :param keep_recent: 已消费结果保留最近几条原文不压
        :param min_compact_len: 短于这个长度的结果不压（压了也没收益）
        """
        self.result_char_limit = settings.AGENT_RESULT_CHAR_LIMIT if result_char_limit is None else result_char_limit
        self.batch_char_limit = settings.AGENT_BATCH_CHAR_LIMIT if batch_char_limit is None else batch_char_limit
        self.context_char_limit = settings.AGENT_CONTEXT_CHAR_LIMIT if context_char_limit is None else context_char_limit
        self.keep_recent = settings.AGENT_KEEP_RECENT_RESULTS if keep_recent is None else keep_recent
        self.min_compact_len = min_compact_len

    # ---------- 对外接口 ----------

    def prepare(self, messages: list[dict]) -> dict:
        """LLM 调用前跑一遍。返回本次动作的埋点（只改 content，不动消息结构）。"""
        chars_before = self.estimate_chars(messages)
        clipped = self._clip_current_batch(messages)    #压缩工具content超出，并返回截断个数
        aged = 0
        if self.estimate_chars(messages) > self.context_char_limit: #判断总体上下文是否超出
            aged = self._age_consumed(messages)     #压缩工具content至80%以下，压缩内容：包含原文字符数、论文ID、章节，块数
        chars_after = self.estimate_chars(messages)
        return {"clipped": clipped, "aged": aged,
                "chars_before": chars_before, "chars_after": chars_after,
                "changed": bool(clipped or aged)}

    def emergency(self, messages: list[dict]) -> dict:
        """反应式兜底：全部已消费结果压到最短指针（不保留最近 K）。
        上下文超限报错后的重试前清理；当前批保持（模型正要读它）。"""
        chars_before = self.estimate_chars(messages)
        last = self._last_assistant_index(messages)
        n = 0
        for i in range(last):
            m = messages[i]
            if m.get("role") != "tool":
                continue
            content = self._content(m)
            if content.startswith(COMPACT_MARK) or self._is_todo_render(content):
                continue
            m["content"] = f"{COMPACT_MARK} 原工具结果 {len(content)} 字符已省略（需要时可重新检索）"
            n += 1
        return {"aged": n, "chars_before": chars_before,
                "chars_after": self.estimate_chars(messages)}

    # ---------- 工具函数 ----------

    @staticmethod
    def estimate_chars(messages: list[dict]) -> int:
        """消息体积估计（字符数）：不依赖 tokenizer 的最便宜近似。
        真实 token 数从响应尾部 usage 埋点校准（react_agent.stats）。"""
        return len(json.dumps(messages, ensure_ascii=False, default=str))

    @staticmethod
    def _last_assistant_index(messages: list[dict]) -> int:
        """最后一条 assistant 消息的位置；其后的工具结果 = 未消费（模型还没回应过）"""
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant":
                return i
        return -1

    @staticmethod
    def _content(m: dict) -> str:
        return m.get("content") or ""

    def _clip_current_batch(self, messages: list[dict]) -> int:
        """本轮新结果：单条超硬限截断；批量超预算时按份数均分收紧后再截。幂等。返回：截断了n条"""
        last = self._last_assistant_index(messages)
        idxs = [i for i in range(last + 1, len(messages)) if messages[i].get("role") == "tool"]
        if not idxs:
            return 0
        cap = self.result_char_limit
        total = sum(len(self._content(messages[i])) for i in idxs)  #如果单轮新增工具content超过了限制
        if total > self.batch_char_limit and len(idxs) > 1:
            cap = max(self.batch_char_limit // len(idxs), 1000)  # 均分预算，最小保底 1000
        n = 0
        for i in idxs:
            content = self._content(messages[i])
            if len(content) > cap and TRUNC_MARK not in content:
                messages[i]["content"] = self._truncate(content, cap)
                n += 1
        return n

    @staticmethod
    def _is_todo_render(text: str) -> bool:
        """todo 清单渲染文本判定（格式见 app/agents/todo.py）——
        它是"状态数据"不是检索证据：压掉会让模型丢掉自己的计划 → 压缩豁免"""
        return (text or "").lstrip().startswith(("[ ]", "[>]", "[x]", "（任务清单"))

    def _age_consumed(self, messages: list[dict]) -> int:
        """已消费结果压成指针：从最老的开始，压到软线 80% 即停；最近 K 条不压。
        todo 清单豁免（状态数据，防模型丢计划）。"""
        last = self._last_assistant_index(messages)
        consumed = [i for i in range(last) if messages[i].get("role") == "tool"]
        if not consumed:
            return 0
        targets = consumed[:-self.keep_recent] if self.keep_recent > 0 else consumed
        target_chars = int(self.context_char_limit * 0.8)
        n = 0
        for i in targets:
            if self.estimate_chars(messages) <= target_chars:
                break
            content = self._content(messages[i])
            if len(content) <= self.min_compact_len or content.startswith(COMPACT_MARK):
                continue
            if self._is_todo_render(content):
                continue
            messages[i]["content"] = self._stub(content)
            n += 1
        return n

    # ---------- 压缩动作 ----------

    @staticmethod
    def _truncate(text: str, keep: int) -> str:
        return text[:keep] + f"\n…{TRUNC_MARK}，原 {len(text)} 字符；如需完整内容可重新检索]"

    @staticmethod
    def _stub(text: str) -> str:
        """长结果 → 一行指针：尽量保留结构信息（工具只读可重取，属安全降级）"""
        return (f"{COMPACT_MARK} 原工具结果 {len(text)} 字符已省略："
                f"{ContextCompactor._stub_info(text)}；需要原文可重新检索")

    @staticmethod
    def _stub_info(text: str) -> str:
        """从压缩前的 JSON 里提取结构性信息（论文/章节/网页标题等），失败退回预览"""
        try:
            data = json.loads(text)
        except Exception:
            return "预览：" + text[:120].replace("\n", " ")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            if "paper_id" in data[0]:                      # search_paper 的结果
                papers, sections = [], []
                for item in data:
                    pid = item.get("paper_id")
                    if pid is not None and pid not in papers:
                        papers.append(pid)
                    sec = item.get("section")
                    if sec and sec not in sections:
                        sections.append(sec)
                return f"涉及论文 {papers}，章节 {sections[:3]}，共 {len(data)} 个块"
            if "url" in data[0]:                           # web_search 的结果（url 是它的专属键）
                titles = [str(d.get("title", ""))[:50] for d in data[:3]]
                return "网页结果：" + "；".join(t for t in titles if t)
            if "why" in data[0]:                           # extract_citations 的结果
                refs = [str(d.get("ref", "")) for d in data[:5]]
                return f"引用关系 {len(data)} 条，涉及 {refs}"
        return "预览：" + json.dumps(data, ensure_ascii=False)[:120]
