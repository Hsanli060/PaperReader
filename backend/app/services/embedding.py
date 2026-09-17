"""Embedding 调用封装（阿里云 MaaS，OpenAI 兼容格式）

批①改造：客户端按"当前生效的 key + 服务地址"动态取用（优先级同 llm.py：
显式传参 > 请求上下文 > .env 默认；按 key 缓存复用）。
注：本模块是同步客户端——调用点在线程池/后台流水线里（批处理，非请求热点）。
"""
import hashlib

from openai import OpenAI

from app.config import settings
from app.services.user_keys import current_embedding_base_url, current_embedding_key

_MAX_CLIENTS = 64
_clients: dict[str, OpenAI] = {}


def _identifier(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def get_client(api_key: str | None = None, base_url: str | None = None) -> OpenAI:
    """取"当前生效 key + 服务地址"的客户端（按二者组合缓存复用）"""
    key = api_key or current_embedding_key() or settings.EMBEDDING_API_KEY
    base = base_url or current_embedding_base_url() or settings.EMBEDDING_BASE_URL
    cid = _identifier(base + "|" + key)
    client = _clients.get(cid)
    if client is None:
        if len(_clients) >= _MAX_CLIENTS:
            _clients.clear()
        client = OpenAI(base_url=base, api_key=key)
        _clients[cid] = client
    return client


def embed(texts: list[str], api_key: str | None = None,
          base_url: str | None = None) -> list[list[float]]:
    """把一批文本变成一批向量。

    :param texts: (list[str]) 文本列表，如 ["第一章内容...", "第二章内容..."]
    :param api_key: (str|None) 显式指定 key（后台流水线传"添加者自己的 key"）；
        不传则取当前请求上下文 → .env 默认
    :param base_url: (str|None) 显式指定服务地址（同上：显式 → 上下文 → .env 默认）
    :return: list[list[float]]，向量列表，每个向量含 EMBEDDING_DIM=1024 个小数：
        [[0.0132, -0.0871, ...], [0.0023, 0.0455, ...]]
    """
    # 1. 一批文本一次性发给服务器（批量算比一条条算快得多）返回一个字典
    resp = get_client(api_key, base_url).embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    # 2. 先挖坑：texts 有几段，就准备几个空位
    vectors = [None] * len(texts)

    # 3. 按标签归位：index=0 的向量放回第 0 个坑
    for item in resp.data:
        vectors[item.index] = item.embedding

    return vectors
