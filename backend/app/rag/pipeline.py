"""
进阶检索流水线：问题 → ①查询改写 → ②双路召回 → ③RRF融合 → ④精排 → top_k

为什么单独一个文件：
    retriever.py 是同步的（要被 to_thread 丢进线程池），而改写和精排必须 await LLM。
    编排层注定是 async，所以单独放这里，retriever.py 就能保持同步纯净，
    继续被 summarize / citations 那些"不需要改写"的场景直接复用。

数据流：
    用户问题 ─┬─→ ①改写 ──→ 英文检索词 ─┐
              │                          ├─→ ②各召回20条 ─→ ③RRF去重排序 ─→ ④按原问题精排 ─→ top5
              └──────────────────────────┘
                （原问题那一路保底：改写跑偏时它还能把原意捞回来）
"""
import asyncio

from app.config import settings
from app.rag.retriever import search_many
from app.rag.rewrite import rewrite_query
from app.rag.fusion import rrf_fuse
from app.rag.rerank import rerank

async def advanced_search(
    question:str,
    paper_id: int | None = None,
    paper_ids: list[int] | None = None,
    section: str | None = None,
    top_k: int | None = None,
)->list[dict]:
    """走完整流水线的检索。参数含义与 retriever.search() 完全一致。

    :return: 精排后的 top_k 条，每条含 id / text / section / paper_id /
       distance / rrf_score / sources / rerank_score
   """
    if top_k is None:
        top_k = settings.RETRIEVAL_TOP_K

    # ① 查询改写（失败会自动退回原问题，不会抛异常）
    rewritten = await rewrite_query(question)

    # ② 双路召回：原问题 + 改写后的检索词，各召回 RETRIEVAL_CANDIDATES 条
    #    改写失败时 rewritten == question，这时只发一路，不用重复发同一个词
    queries = [question, rewritten] if rewritten != question else [question]

    # search_many 是同步的（内含 embedding 网络调用 + Chroma 查询）
    ranked_lists=await asyncio.to_thread(
        search_many,
        queries,
        paper_id=paper_id,
        paper_ids=paper_ids,
        section=section,
        top_k=settings.RETRIEVAL_CANDIDATES,
    )

    # ③ RRF 融合：两路（或一路）结果合并去重，按"被几路认可"重新排序
    fused = rrf_fuse(ranked_lists)

    # ④ 精排：用【用户原问题】给候选打分，取前 top_k（失败自动退回 RRF 顺序）
    return await rerank(question, fused, top_k)