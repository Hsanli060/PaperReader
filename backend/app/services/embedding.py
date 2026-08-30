"""
Embedding 调用封装（阿里云 MaaS，OpenAI 兼容格式）
"""

from openai import OpenAI
from app.config import settings

client =OpenAI(
    base_url=settings.EMBEDDING_BASE_URL,
    api_key=settings.EMBEDDING_API_KEY,
)

def embed(texts:list[str])->list[list[float]]:
    """把一批文本变成一批向量。

    :param texts: (list[str]) 文本列表，如 ["第一章内容...", "第二章内容..."]
    :return: list[list[float]]，向量列表，每个向量含 EMBEDDING_DIM=1024 个小数：
        [[0.0132, -0.0871, ...], [0.0023, 0.0455, ...]]
    """
    # 1. 一批文本一次性发给服务器（批量算比一条条算快得多）返回一个字典
    resp=client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    # 2. 先挖坑：texts 有几段，就准备几个空位
    vectors=[None]*len(texts)

    # 3. 按标签归位：index=0 的向量放回第 0 个坑
    for item in resp.data:
        vectors[item.index]=item.embedding

    return vectors