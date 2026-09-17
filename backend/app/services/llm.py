"""LLM 调用封装（DeepSeek，OpenAI 兼容格式）—— 全异步版
业务代码只 import 这个文件，不直接 import openai —— 换厂商只改这里

批①改造：客户端按"当前生效的 key + 服务地址"动态取用。解析优先级：
    显式传参 > 当前请求上下文（用户自带，见 user_keys.py）> .env 默认
    （base_url 同理——用户可能用不同运营商的 OpenAI 兼容端点）
    客户端按 key 缓存复用（同 key 共享连接池；脚本/测试无上下文时自动落 .env，
    行为与改造前一致）。

    聊天流式   → react_agent 用 get_async_client()（stream=True 逐 token）
    一次性问答 → chat_once()（summarizer / citation 等拿到完整文本再处理）
"""
import hashlib

from openai import AsyncOpenAI

from app.config import settings
from app.services.user_keys import current_llm_base_url, current_llm_key

# 客户端缓存上限：正常场景远达不到（=活跃 key 数）；超了整表清空重建，
# 防止"很多用户各配各的 key"把内存攒爆
_MAX_CLIENTS = 64
_clients: dict[str, AsyncOpenAI] = {}


def _identifier(api_key: str) -> str:
    """key 的短指纹（缓存键；不进日志、不落盘）"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def get_async_client(api_key: str | None = None, base_url: str | None = None) -> AsyncOpenAI:
    """取"当前生效 key + 服务地址"的客户端（按二者组合缓存复用）。

    :param api_key: 显式指定的 key（可选）；不传则取当前请求上下文，
        无上下文再退 .env 默认 key
    :param base_url: 显式指定的服务地址（可选）；不传则取当前请求上下文
        （用户自带地址），无上下文再退 .env 默认地址
    """
    key = api_key or current_llm_key() or settings.LLM_API_KEY
    base = base_url or current_llm_base_url() or settings.LLM_BASE_URL
    cid = _identifier(base + "|" + key)     # 客户端身份 = 地址+key 组合
    client = _clients.get(cid)
    if client is None:
        if len(_clients) >= _MAX_CLIENTS:
            _clients.clear()
        client = AsyncOpenAI(base_url=base, api_key=key)
        _clients[cid] = client
    return client


async def chat_once(messages: list[dict], temperature: float | None = None,
                    api_key: str | None = None, base_url: str | None = None) -> str:
    """非流式问答（异步）：一次调用，拿到完整回答文本。

    适用场景：调用方要拿到完整文本再处理（比如让模型输出 JSON 再解析）。
    参数：
        messages (list[dict]): OpenAI messages 格式，如
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
        temperature (float|None): 不传用 settings.LLM_TEMPERATURE
        api_key (str|None): 覆盖当前请求上下文的 key（默认自动解析）
        base_url (str|None): 覆盖当前请求上下文的服务地址（默认自动解析）
    返回：
        str，模型的完整回答（可能为空串，调用方自行兜底）
    """
    resp = await get_async_client(api_key, base_url).chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
        messages=messages,
    )
    return resp.choices[0].message.content or ""
