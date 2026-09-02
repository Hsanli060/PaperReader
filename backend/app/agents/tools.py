"""
Agent 工具注册表：函数 + JSON Schema + dispatch

异步化注（与 react_agent/llm.py 的 FIX 收敛配套）：
    - 所有工具函数都是 async def：内部所有同步 I/O 一律 to_thread 包住
      （rag_search 的 embedding 网络调用 + Chroma 查询、ddgs 搜索），
      LLM 调用走 chat_once（AsyncOpenAI）直接 await
    - dispatch 也是 async def：react_agent 直接 await，papers.py 路由 await 后
      LLM 调用不再占线程池
    - 出错不抛异常的老规矩不变：错误说明文字端回去，模型下轮自我修正
"""

import asyncio
import json

from app.rag.retriever import search as rag_search
from app.services.web_search import web_search as _web_search
from app.paper.summarizer import summarize_paper
from app.paper.citation import extract_citations


async def _search_paper(query: str, paper_id: int | None = None,
                        allowed_paper_ids: list[int] | None = None) -> list[dict]:
    """知识库检索：去向量库找和检索词最相关的论文块。

    :param query: (str) 检索词
    :param paper_id: (int) 只查这篇论文（LLM 可传，用于指向它想问的论文）
    :param allowed_paper_ids: (list[int]|None) 会话 scope（服务端注入，LLM 不可见）——
        传入时检索被强制圈在这些论文里；paper_id 越出 scope 会被拒绝
    :return: list[dict]，给模型看的瘦身版检索结果
    """
    # scope 安全校验：LLM 传的 paper_id 必须落在服务端圈定的范围内
    if allowed_paper_ids is not None and paper_id is not None and paper_id not in allowed_paper_ids:
        raise ValueError(f"paper_id={paper_id} 不在当前问答范围内（范围：{allowed_paper_ids}），请改用范围内的论文 ID")
    # rag_search 是同步的（内含 embedding API 网络调用 + ChromaDB 查询），
    # 按铁律 to_thread 丢线程池，检索期间事件循环继续服务其他请求
    hits = await asyncio.to_thread(
        rag_search, query,
        paper_id=paper_id, paper_ids=allowed_paper_ids,
    )
    return [
        {
            "paper_id": h["paper_id"],
            "section": h["section"],
            "distance": round(h["distance"], 3),
            "text": h["text"],
        } for h in hits
    ]


async def _web_search_tool(query: str, max_results: int = 5) -> list[dict]:
    """联网搜索：知识库里查不到的外部信息（最新进展/代码仓库/作者信息）。

    :param query: 搜索词
    :param max_results: 要几条结果，默认 5
    :return: list[dict]，每条键固定为 title / url / snippet
    """
    # ddgs 是同步库：to_thread 丢线程池，搜索期间事件循环继续服务其他请求
    return await asyncio.to_thread(_web_search, query, max_results=max_results)


async def _summarize_paper_tool(paper_id: int,
                                allowed_paper_ids: list[int] | None = None) -> dict:
    """结构化总结一篇论文：先从向量库抓该论文的块拼成原材料，再让 LLM 加工。"""
    # scope 安全校验：总结也只许落在服务端圈定的范围内
    if allowed_paper_ids is not None and paper_id not in allowed_paper_ids:
        raise ValueError(f"paper_id={paper_id} 不在当前问答范围内（范围：{allowed_paper_ids}）")
    # 原材料质量决定摘要质量：检索词必须是"正文核心话题"而不是裸词 "paper"
    hits = await asyncio.to_thread(
        rag_search,
        query="method experiment results state space model attention",
        paper_id=paper_id, top_k=15,
    )
    if not hits:
        raise ValueError(f"向量库里没有 paper_id={paper_id} 的内容，请先运行流水线索引该论文")
    text = "\n\n".join(h["text"] for h in hits)
    return await summarize_paper(text)          # AsyncOpenAI，非阻塞等待


async def _extract_citations_tool(paper_id: int,
                                  allowed_paper_ids: list[int] | None = None) -> list[dict]:
    # scope 安全校验：引用提取同样受会话范围约束
    if allowed_paper_ids is not None and paper_id not in allowed_paper_ids:
        raise ValueError(f"paper_id={paper_id} 不在当前问答范围内（范围：{allowed_paper_ids}）")
    hits = await asyncio.to_thread(
        rag_search, query="references bibliography introduction", paper_id=paper_id, top_k=10
    )
    if not hits:
        raise ValueError(f"向量库里没有 paper_id={paper_id} 的内容，请先运行流水线索引该论文")
    text = "\n\n".join(h["text"] for h in hits)
    return await extract_citations(text)        # AsyncOpenAI，非阻塞等待


# 工具列表及说明书
TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_paper",
            "description": "在用户的知识库里检索学术论文内容。"
                          "凡是用户问'论文里怎么说的'、'XX 是什么意思'、要求引用原文，都必须用这个工具。"
                          "先用宽泛的词查，结果不够具体再换更精确的关键词重查",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索词。把用户的口语问题换算成论文里会出现的关键词，"
                                      "如用户问'注意力怎么算的'应填 'scaled dot-product attention'",
                    },
                    "paper_id": {
                        "type": "integer",
                        "description": "只检索这篇论文。不确定是哪篇时不要传——"
                                      "检索结果自带的 paper_id 字段能帮你定位论文",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索互联网。当知识库检索不到、或用户问的是知识库"
                          "覆盖不了的内容（最新进展、后续工作、代码仓库、作者背景）时使用。"
                          "注意：先试知识库检索，查不到再联网",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索词",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "要几条结果，默认 5，一般不用传",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_paper",
            "description": "生成一篇论文的结构化摘要（问题/方法/实验/结论四部分）。"
                          "当用户要求'总结/概括/这篇论文讲了什么/给我一份阅读笔记'时使用。"
                          "需要 paper_id 参数——不确定时先用 search_paper 找到论文再说",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "论文 ID。检索结果里的 paper_id 字段可以告诉你某篇论文的 ID",
                    },
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_citations",
            "description": "提取一篇论文的引用关系：它引用了哪些前序工作、各是什么用途。"
                          "当用户问'这篇论文引用了谁/基于哪些工作/和哪些研究有关'时使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {
                        "type": "integer",
                        "description": "论文 ID",
                    },
                },
                "required": ["paper_id"],
            },
        },
    },
]

# 封装所有工具到一个字典中方便查询
TOOLS_IMPLS: dict = {
    "search_paper": _search_paper,
    "web_search": _web_search_tool,
    "summarize_paper": _summarize_paper_tool,
    "extract_citations": _extract_citations_tool,
}


async def dispatch(name: str, arguments: str,
                   allowed_paper_ids: list[int] | None = None) -> str:
    """执行一次工具调用（异步版）：找到工具 → 解析参数 → await 调用 → 结果转 JSON 字符串。

    :param name: (str) 模型要调用的工具名，如 "search_paper"
    :param arguments: (str) 模型给的参数，实测是 JSON 字符串如 '{"query": "..."}'，不是 dict
    :param allowed_paper_ids: (list[int]|None) 会话 scope（服务端注入，LLM 不可见）。
        只注入给声明了该参数的工具——web_search 等无 scope 概念的工具不收
    :return: (str) 工具结果的 JSON 字符串；出错时不抛异常，
        而是返回错误说明文字 —— 模型下轮读到会自己修正参数重试，
        比直接崩掉主循环好
    """
    tool = TOOLS_IMPLS.get(name)
    if tool is None:
        return f"错误：没有叫 {name!r} 的工具，可用工具：{list(TOOLS_IMPLS)}"
    try:
        args = json.loads(arguments) if arguments else {}     # 反序列化参数
        # scope 注入：只有函数签名里有 allowed_paper_ids 的工具才收
        # （inspect 按需注入，不污染无 scope 概念的工具）
        if allowed_paper_ids is not None:
            import inspect as _inspect
            if "allowed_paper_ids" in _inspect.signature(tool).parameters:
                args["allowed_paper_ids"] = allowed_paper_ids
        result = await tool(**args)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"工具执行失败：{type(e).__name__}: {e}，请检查参数后重试"
