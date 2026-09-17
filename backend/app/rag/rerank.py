"""
精排（rerank）：召回阶段求"广"（尽量不漏），精排阶段求"准"（把真正相关的顶上来）。

为什么精排喂【用户原问题】而不是改写后的检索词：
    改写是为了扩大召回面，它可能偏离用户原意（模型补的同义词不一定贴题）。
    精排这一步要对齐的是"用户到底想问什么"，所以喂原问题，起到纠偏作用。

实现：qwen3-rerank 专用交叉编码器（百炼兼容接口）。
    为什么从"LLM 打分"换成专用模型（v1 → v2）：
    v1 用 chat 模型打分——它要逐 token 自回归生成 20+ 条 JSON，实测 26 个候选
    耗时 9.1s；qwen3-rerank 是一次前向直接出全部分数，实测 ~0.2-0.5s，
    快 20 倍以上，按 token 计费也更便宜。打分任务用对了工具，就不该用生成模型。

失败时退回 RRF 顺序（精排是"锦上添花"的一环，挂了不该让整个检索失败）。
"""
import httpx
from loguru import logger

from app.config import settings
from app.services.user_keys import current_embedding_key

# 每个候选块只截前这么多字符去打分：判断"这块讲不讲这个问题"不需要读完整块
SNIPPET_CHARS = 500


def _rerank_url(base: str | None = None) -> str:
    """qwen3-rerank 的兼容接口地址：复用 embedding 的 workspace 域名（同空间同一个 key）。
    base：当前生效的嵌入服务地址（用户自带地址优先，见 services/user_keys.py）；
    自定义域名大概率没有 reranks 端点——调用失败会退回 RRF 顺序（已有兜底）。"""
    root = base or current_embedding_base_url() or settings.EMBEDDING_BASE_URL
    root = root.replace("/compatible-mode/v1", "").rstrip("/")
    return root + "/compatible-api/v1/reranks"


async def rerank(question: str, candidates: list[dict], top_k: int,
                 api_key: str | None = None, base_url: str | None = None) -> list[dict]:
    """用 qwen3-rerank 给候选块打分，返回分数最高的 top_k 条。

    :param question: 用户原问题（不是改写后的检索词——精排要对齐用户原意）
    :param candidates: RRF 融合后的候选列表
    :param top_k: 要留几条
    :param api_key: (str|None) 覆盖当前请求上下文里的嵌入 key（批①：默认自动解析）
    :param base_url: (str|None) 覆盖嵌入服务地址（默认自动解析：用户自带 → 服务器默认）
    :return: 重排后的列表，每条多一个 "rerank_score" 键
        打分失败时原样返回前 top_k 条（退回 RRF 顺序）——精排挂了不该让整个检索失败。
    """
    # 候选本来就不多，没必要花一次网络往返
    if len(candidates) <= top_k:
        return candidates[:top_k]

    payload = {
        "model": settings.RERANK_MODEL,
        "query": question,
        "documents": [c["text"][:SNIPPET_CHARS] for c in candidates],
        "top_n": top_k,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _rerank_url(base_url),
                json=payload,
                headers={"Authorization": f"Bearer {api_key or current_embedding_key() or settings.EMBEDDING_API_KEY}"},
            )
            resp.raise_for_status()
            results = resp.json()["results"]
    except Exception as e:
        logger.warning(f"精排调用失败，退回 RRF 顺序：{type(e).__name__}: {e}")
        return candidates[:top_k]

    # results 已按 relevance_score 降序：[{"index": i, "relevance_score": x}, ...]
    # index 是原候选列表的下标——按它映射回候选字典
    scored: list[dict] = []
    for item in results:
        i = item["index"]
        if not (isinstance(i, int) and 0 <= i < len(candidates)):
            continue  # 防御：结构异常时跳过坏数据，不因一条坏数据丢掉整批
        c = dict(candidates[i])
        c["rerank_score"] = float(item["relevance_score"])
        scored.append(c)

    if not scored:
        logger.warning("精排返回空结果，退回 RRF 顺序")
        return candidates[:top_k]
    return scored[:top_k]
