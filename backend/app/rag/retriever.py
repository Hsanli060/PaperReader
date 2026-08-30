"""
RAG 读侧：把用户的问题变成图钉，去向量库里找最近的几块
写侧（入库/删除）在 vector_store.py；两课共用同一个 papers_col
"""

from app.config import settings
from app.rag.vector_store import papers_col
from app.services.embedding import embed

def search(query:str,paper_id:int|None=None,section:str|None=None,top_k:int|None=None)->list[dict]:
    """检索：问题 → 向量 → 最近 top_k 块。

    :param query: 用户问题
    :param paper_id: 论文ID
    :param section: 只在这个章节里找（精确匹配章节标题）；None = 不限章节
    :param top_k: 取前几块；None = 用 settings.RETRIEVAL_TOP_K（默认 5，在 config.py 改）
    :return: 按相似度从近到远排好的结果列表：
        [{"text": 块原文, "section": 章节名, "paper_id": 论文ID, "distance": 余弦距离}, ...]
    """
    if top_k is None:
        top_k=settings.RETRIEVAL_TOP_K

    #问题向量化
    qvec=embed([query])[0]      #取第一个向量值就行

    # 2. 组装过滤条件（三级阶梯）
    conds=[]
    if paper_id is not None:
        conds.append({"paper_id":paper_id})
    if section is not None:
        conds.append({"section":{"$eq":section}})   #筛选出元数据中 section 字段的值 “等于（Equal）” 变量 section 的数据
    if len(conds)==1:
        where=conds[0]
    elif conds:
        where={"$and":conds}
    else:
        where=None

    # 3. 查询。返回双层嵌套：外层=第几次查询
    r=papers_col.query(
        query_embeddings=[qvec],
        n_results=top_k,
        where=where,
        include=["documents","metadatas","distances"],
    )

    # 4. 拆平：双层嵌套 → 一层好用的字典列表
    hits=[]
    for i in range(len(r["ids"][0])):
        hits.append(
            {
                "text":r["documents"][0][i],
                "section":r["metadatas"][0][i]["section"],
                "paper_id":r["metadatas"][0][i]["paper_id"],
                "distance": r["distances"][0][i],
            }
        )
    return hits