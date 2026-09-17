"""
RAG 读侧：把用户的问题变成图钉，去向量库里找最近的几块
写侧（入库/删除）在 vector_store.py；两课共用同一个 papers_col

FIX-3' 注：检索范围支持 paper_ids 列表（会话 scope 圈定）——
由服务端注入（chat.py 把 conversation_papers 查出来传进来），绝不信任 LLM 传参。
"""

from app.config import settings
from app.rag.vector_store import papers_col
from app.services.embedding import embed

def _build_where(
    paper_id:int|None=None,
    paper_ids:list[int]|None=None,
    section:str|None=None
)->dict|None:
    """把过滤参数组装成 Chroma 的 where 条件。

   返回 None 表示不过滤（Chroma 实测接受 where=None）。
   """
    conds=[]
    if paper_ids is not None:
        # 批②：None=不过滤（内部/脚本用）；**空列表=什么都搜不到**——
        # 用户库为空时绝不能退化成"全库"（那是别人的论文）。
        # 注意：Chroma 实测拒绝 $in 空列表（ValueError），用不可能命中的哨兵 -1 代替
        conds.append({"paper_id":{"$in":paper_ids if paper_ids else [-1]}})
    elif paper_id is not None:
        conds.append({"paper_id":paper_id})
    if section is not None:
        conds.append({"section":{"$eq":section}})
    if len(conds)==1:
        return conds[0]
    elif conds:
        return {"$and":conds}
    return None

def _unpack(r:dict,qi:int)->list[dict]:
    """把 Chroma 的双层嵌套结果拆成一层字典列表。

    外层是"第几次查询"，内层是"这次查询的 top_k 条"——所以要多传一个 qi
    指定拆第几次。单查询的 search() 传 0，多查询的 search_many() 逐个传。

    "id" 是这个块在向量库里的主键。检索本身用不到它，但 RRF 融合必须靠它
    给不同路径的结果"认亲"（同一个块在不同路径里排名不同，只有 id 相同）。
    """
    hits=[]
    for i in range(len(r["ids"][qi])):
        hits.append(
            {
                "id": r["ids"][qi][i],
                "text": r["documents"][qi][i],
                "section": r["metadatas"][qi][i]["section"],
                "paper_id": r["metadatas"][qi][i]["paper_id"],
                "distance": r["distances"][qi][i],
            }
        )
    return hits

def search_many(
    queries:list[str],
    paper_id:int|None=None,
    paper_ids:list[int]|None=None,
    section:str|None=None,
    top_k:int|None=None,
)->list[list[dict]]:
    """多路检索：一次发 N 个检索词，拿回 N 个结果列表。

    :param queries: 检索词列表，如 [原问题, 改写后的检索词]
    :param paper_id: 单篇论文过滤（旧接口，兼容保留）
    :param paper_ids: 会话 scope——只在这些论文里检索；
        批②语义：None=不过滤（内部用），[]=匹配不到任何块（用户库为空）
    :param section: 只在这个章节里找（精确匹配章节标题）；None = 不限章节
    :param top_k: 每路取前几块；None = 用 settings.RETRIEVAL_TOP_K
    :return: 外层按 queries 顺序，每个元素是该路按距离升序的结果列表

    关键点：embed 一次批量算 N 个向量，query 一次发 N 个查询向量，
    不是循环调 N 次。Chroma 返回的"双层嵌套"就是为这个场景设计的——
    外层对应你发进去的几个查询向量。
    """
    if top_k is None:
        top_k=settings.RETRIEVAL_TOP_K
    if not queries:
        return []

    qvecs=embed(queries)
    where=_build_where(paper_id,paper_ids,section)

    r=papers_col.query(
        query_embeddings=qvecs,
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return [_unpack(r,qi) for qi in range(len(queries))]


def search(
    query: str,
    paper_id: int | None = None,
    paper_ids: list[int] | None = None,
    section: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """检索：问题 → 向量 → 最近 top_k 块。

    :param query: 用户问题
    :param paper_id: 单篇论文过滤（旧接口，兼容保留）
    :param paper_ids: 会话 scope——只在这些论文里检索；
        批②语义：None=不过滤（内部用），[]=匹配不到任何块（用户库为空）
    :param section: 只在这个章节里找（精确匹配章节标题）；None = 不限章节
    :param top_k: 取前几块；None = 用 settings.RETRIEVAL_TOP_K（默认 5，在 config.py 改）
    :return: 按相似度从近到远排好的结果列表：
        [{"id": 块主键, "text": 块原文, "section": 章节名,
          "paper_id": 论文ID, "distance": 余弦距离}, ...]

    单路检索其实就是"只发一个检索词"的多路检索，所以直接复用 search_many，
    把第一个（也是唯一一个）结果列表取出来。
    """
    return search_many(
        [query],
        paper_id=paper_id, paper_ids=paper_ids, section=section, top_k=top_k,
    )[0]
