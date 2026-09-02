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
    """把模型输出剥成干净的引用列表，怎么抽风都不抛异常（失败返回 []）。

    剥壳顺序和 summarizer._parse_json_block 相同；区别是这里同时接受两种形状：
      {"cited_by_us": [...]}  ← prompt 约定的输出
      [{...}, ...]            ← 模型偶尔不听话直接给裸 list
    :param raw: (str) 模型的原始输出，可能带 ```json 标记、前后废话
    :return: (list[dict]) 每项 {ref, title, why}；解析不出就 []，绝不抛异常
    """
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    # 谁在前按谁剥：裸 list 时 find("{") 会切进第一个元素内部，所以不能只认 {
    p_obj, p_arr = s.find("{"), s.find("[")
    if p_obj == -1 or (p_arr != -1 and p_arr < p_obj):
        start, end = p_arr, s.rfind("]")        # 数组在前（或没有对象）→ 按列表剥
    else:
        start, end = p_obj, s.rfind("}")        # 对象在前 → 按 dict 剥
    if start != -1 and end > start:
        s = s[start:end+1]                       # 含头含尾：+1 才能保住最后一个括号
    try:
        d = json.loads(s)
    except ValueError:
        return []                                # 模型输出废话：端空列表，不炸调用方
    if isinstance(d, dict):
        v = d.get("cited_by_us")
        return v if isinstance(v, list) else []  # 字段缺失/形状不对 → []
    # 裸 list 兜底；顺手滤掉非 dict 元素，保证"list[dict]"的返回约定
    return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []
