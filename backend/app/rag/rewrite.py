"""
查询改写：把用户的中文口语问题，换成论文正文里会出现的英文检索词。

为什么需要：
    1. 用户问"注意力怎么算的"，论文里写的是 "scaled dot-product attention"。
       向量检索能跨过这层表达差异，但跨得不彻底；补上英文术语能明显提升召回质量。
    2. 以后要接 BM25 关键词召回时，中文问题对英文语料是"零命中"——
       改写成英文是那条路能用的前提。现在先做好，将来换路径不用返工。

延迟代价（重要）：
    每调一次本函数就是一次 LLM 往返，1~3 秒。Agent 一次问答可能调好几次
    search_paper，这个开销会叠加到用户等待时间里。

    为什么这里【不做缓存】：缓存的键是 rewrite:{问题原文}，只有"同一串字符
    被再次改写"才会命中。单人使用场景下这个频率接近 0——和 chat.py 里被移除的
    对话缓存是同一个死因（真实命中率趋近 0）。要做也得先有数据：跑一段时间日志
    确认命中率值得，再回来加。
"""
from loguru import logger

from app.services.llm import chat_once

PROMPT = (
    "你是学术检索助手。把用户的问题改写成适合在英文学术论文里检索的关键词。\n"
    "要求：\n"
    "1. 输出一行英文关键词或短语，用空格分隔，不要整句、不要标点\n"
    "2. 保留问题里的专业术语，并补上同义或密切相关的术语\n"
    "   例：问题提到 attention，可补上 self-attention softmax query key value\n"
    "3. 只输出改写结果本身，不要任何解释、前缀或引号\n"
    "示例：问题「注意力机制的公式是怎么算的？」→ "
    "scaled dot-product attention softmax query key value computation formula"
)

async def rewrite_query(question:str)->str:
    """把问题改写成英文检索词。

    :param question: 用户原问题
    :return: 改写后的检索词；任何异常都返回原问题（兜底，保证检索不会因此中断）
    """
    question=question.strip()
    if not question:
        return question
    try:
        rewritten=await chat_once(
            [{"role": "user", "content": PROMPT + "\n\n问题：" + question}],
            temperature=0.0,  # 改写要稳定，温度拉到 0
        )
    except Exception as e:
        # 改写失败不是致命错误：退回原问题，后面的向量召回照样能跑
        logger.warning(f"查询改写失败，改用原问题：{type(e).__name__}: {e}")
        return question
    rewritten = rewritten.strip().strip('"').strip("`").replace("\n", " ").strip()
    if not rewritten:
        logger.warning("查询改写返回空串，改用原问题")
        return question

    return rewritten