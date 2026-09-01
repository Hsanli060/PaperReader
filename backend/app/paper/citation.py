"""
引用关系提取：论文文本 → LLM → 引用列表 JSON
和 summarizer 共用同一个 _parse_json_block 的思路，但结构不同

异步化注：走 chat_once()（AsyncOpenAI 非流式），与 summarizer 同一条异步链路
"""
import json
import re

from app.services.llm import chat_once

_CITATION_PROMPT = """以下是论文的参考文献部分和引言部分。请提取这篇论文的引用关系，输出 JSON：
1. 只输出 JSON 本身，不要代码块标记，不要解释文字
2. cited_by_us = 这篇论文引用了谁（看参考文献列表），

示例：
{{"cited_by_us": [{{"ref": "参考文献编号或简称", "title": "论文标题", "why": "引用它做什么用（从引言里找）"}}]}}

论文内容：
{content}"""


async def extract_citations(text: str) -> list[dict]:
    """论文文本 → LLM → 引用关系列表（异步版）。

    :param text: (str) 论文文本（参考文献 + 引言部分最有信息量）
    :return: list[dict]，每项 {ref, title, why}：
        [{"ref": "[1]", "title": "HiPPO: ...", "why": "状态空间模型的奠基工作"}, ...]
    """
    prompt = _CITATION_PROMPT.format(content=text[:8000])
    raw = await chat_once([
        {"role": "system", "content": "你是论文分析助手，只输出 JSON。"},
        {"role": "user", "content": prompt},
    ])
    return _parse_citations(raw)


def _parse_citations(raw: str) -> list[dict]:
    """剥壳逻辑和 summarizer._parse_json_block 相同；prompt 约定输出 {"cited_by_us": [...]}，
    解析后取出里面的列表，保持 extract_citations 返回 list[dict] 的约定。"""
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    start, end = s.find("{"), s.rfind("}")   # 最外层是 dict，找 { }
    if start != -1 and end > start:
        s = s[start:end+1]                   # 含头含尾：+1 才能保住最后的 }
    d = json.loads(s)
    # 兜底：模型偶尔不按 prompt 输出裸 list，也能接住
    return d["cited_by_us"] if isinstance(d, dict) else d
