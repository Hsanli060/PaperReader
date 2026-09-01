"""
LLM 调用封装（DeepSeek，OpenAI 兼容格式）—— 全异步版
业务代码只 import 这个文件，不直接 import openai —— 换厂商只改这里

全项目只有一个 async_client（FIX-4 收敛的终点）：
    聊天流式   → react_agent 直接用 async_client（stream=True 逐 token）
    一次性问答 → chat_once()（summarizer / citation 等拿到完整文本再处理）

旧的同步 client 和 chat()/chat_stream() 已删除（无人调用 + 与异步架构割裂，
留着只会让"同步/异步两套"的困惑继续存在）。
"""
from openai import AsyncOpenAI

from app.config import settings

#唯一的 LLM 客户端：事件循环托管，流式/非流式都走它
async_client = AsyncOpenAI(
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
)


async def chat_once(messages: list[dict], temperature: float | None = None) -> str:
    """非流式问答（异步）：一次调用，拿到完整回答文本。

    适用场景：调用方要拿到完整文本再处理（比如让模型输出 JSON 再解析）。
    参数：
        messages (list[dict]): OpenAI messages 格式，如
            [{"role":"system","content":"..."},{"role":"user","content":"..."}]
        temperature (float|None): 不传用 settings.LLM_TEMPERATURE
    返回：
        str，模型的完整回答（可能为空串，调用方自行兜底）
    """
    resp = await async_client.chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
        messages=messages,
    )
    return resp.choices[0].message.content or ""
