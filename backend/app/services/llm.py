"""
LLM 调用封装（DeepSeek，OpenAI 兼容格式）
业务代码只 import 这个文件，不直接 import openai —— 换厂商只改这里
"""
from openai import OpenAI

from app.config import settings


SYSTEM_PROMPT="""你是一个专业的学术论文研读助手，帮助用户高效阅读、理解和分析学术论文。你能检索用户知识库中的论文内容，对论文进行深度分析，并支持多篇论文的对比研究。
# 核心能力
1. **论文检索**：从用户上传的论文知识库中检索相关内容
2. **论文总结**：生成结构化的论文阅读笔记
3. **论文对比**：对多篇论文进行多维度对比分析
4. **引用分析**：提取和分析论文的引用关系
5. **联网搜索**：搜索论文相关的补充信息（代码仓库、作者主页、后续工作等）
"""

#创建客户端
client=OpenAI(
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
)

def chat(prompt:str,system_prompt:str=SYSTEM_PROMPT)->str:
    """非流式问答：等模型把整段回答写完，一次性返回完整字符串。

    参数：
        prompt (str): 用户的问题/指令，如 "用一句话总结这篇论文"
        system_prompt (str): 系统提示词，默认用本文件的 SYSTEM_PROMPT
    返回：
        str，模型的完整回答，如 "这篇论文提出了选择性状态空间模型..."
    适用场景：调用方要拿到完整文本再处理（比如让模型输出 JSON 再解析）。
    """
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    resp=client.chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        messages=messages,
    )
    return resp.choices[0].message.content

def chat_stream(prompt:str,system_prompt:str=SYSTEM_PROMPT):
    """流式问答：返回一个"逐字吐出"的生成器（打字机效果的后端来源）。

    参数：
        prompt (str): 用户的问题/指令
        system_prompt (str): 系统提示词，默认用本文件的 SYSTEM_PROMPT
    返回：
        Generator[str]，逐块产出文本片段，用 for 循环消费：
        for piece in chat_stream("这篇论文讲什么"):
            print(piece, end="", flush=True)
    """
    messages=[
        {"role": "system", "content":system_prompt},
        {"role":"user","content":prompt},
    ]
    stream=client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=messages,
        temperature=settings.LLM_TEMPERATURE,
        stream=True
    )

    for chunk in stream:
        piece=chunk.choices[0].delta.content
        if piece:
            yield piece