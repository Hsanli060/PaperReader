"""
ChromaDB 论文向量库（写侧）：把 splitter 切好的块存进向量库
读侧（按问题检索）在 retriever.py，下一课
"""
from collections import Counter
import chromadb
from chromadb.config import Settings

from app.config import settings
from app.services.embedding import embed

_client=chromadb.PersistentClient(
    path=settings.CHROMA_DIR,
    settings=Settings(anonymized_telemetry=False),       ## 关掉匿名上报（默认会发统计到官方）
)
#如果数据库里已经存在名为 "papers" 的集合，有就返回，无则创建
papers_col=_client.get_or_create_collection(
    name="papers",
    configuration={"hnsw":{"space":"cosine"}},      #hnsw算法，space向量之间的计算方式，这里选择余弦距离
)

EMBED_BATCH = 16  # 每批发给 embedding 服务的条数（实测 20 条 OK，留点余量）

def index_paper(paper_id:int,chunks:list[dict])->int:
    """把一篇论文的全部块写入向量库，返回入库块数。

    :param paper_id: 论文ID
    :param chunks: 切片后的论文块
    :return:向量的长度
    """
    delete_paper(paper_id)

    ids=[f"{paper_id}-{i}" for i in range(len(chunks))]     #[str]
    documents=[c["text"]for c in chunks]                    #论文内容
    metadatas=[                                             #论文标题
        {"paper_id":paper_id,"section":c["section"]}
        for c in chunks
    ]

    #开始向量化
    vectors: list[list[float]] = []
    for start in range(0,len(documents),EMBED_BATCH):
        batch=documents[start:start+EMBED_BATCH]
        vectors.extend(embed(batch))

    papers_col.add(
        ids=ids,        #主键ID
        documents=documents,    #向量对应原文（便于LLM用于回答）
        embeddings=vectors,     #1024维4向量
        metadatas=metadatas,    # 标签，用于过滤
    )
    return len(ids)

def delete_paper(paper_id:int)->None:
    """删掉某篇论文在向量库里的全部块（重新索引前 / 日后删论文功能用）"""
    papers_col.delete(where={"paper_id":paper_id})

def get_stats()->dict:
    """向量库体检：总块数total + 每篇论文各占多少块per_paper（验收时用）"""
    got=papers_col.get(include=["metadatas"])   # 只取含有"included": ["metadatas"]字典的结果
    per_paper=Counter(m["paper_id"] for m in got["metadatas"])
    return {"total":papers_col.count(),"per_paper":dict(per_paper)}