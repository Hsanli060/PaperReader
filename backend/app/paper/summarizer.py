"""
结构化论文摘要：论文文本 → LLM → 固定字段的 JSON
在这里解决"模型不肯老实输出 JSON"的问题：解析时把常见自由发挥逐层剥掉
"""
import json
import re

from app.config import settings
from app.services.llm import client

_SUMMARY_PROMPT="""请阅读以下论文内容，输出 JSON 总结，严格遵守：
1. 只输出 JSON 本身，不要 markdown 代码块标记（```），不要任何解释文字
2. 四个字段全部为字符串，缺内容就用 "未提及"，禁止编造

示例：
{{"problem": "这篇论文要解决的问题", "method": "核心方法", "experiment": "关键实验结果", "conclusion": "主要结论"}}

论文内容：
{content}"""

def summarize_paper(text:str)->dict:
    """论文全文 → LLM → 结构化摘要。

    :param text: (str) 论文文本（建议用摘要或核心章节，太长要截断）
    :return: dict，固定四键 problem / method / experiment / conclusion：
        {"problem": "…", "method": "…", "experiment": "…", "conclusion": "…"}
    """
    prompt=_SUMMARY_PROMPT.format(content=text[:8000])
    resp=client.chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        messages=[
            {"role":"system","content":"你是论文分析助手，只输出 JSON。"},
            {"role":"user","content":prompt}
        ],
    )
    return _parse_json_block(resp.choices[0].message.content or "")

def _parse_json_block(raw:str)->dict:
    """把模型输出剥成干净 JSON 并解析，失败抛 ValueError（由 tools.dispatch 接住当结果端回去）。

    :param raw: (str) 模型的原始输出，可能带 ```json 前缀、前后废话
    :return: (dict) 解析成功的结果
    """
    s=raw.strip()
    # 第 1 层：剥 ```json ... ``` 代码块标记
    m=re.search(r"```(?:json)?\s*(.*?)```",s,re.S)
    if m:
        s=m.group(1).strip()        #获取里面的JSON格式内容
    start,end=s.find("{"),s.rfind("}")
    if start!=-1 and end>start:
        s=s[start:end+1]
    # loads：字符串 → Python dict（dumps 是反方向，别搞混）
    return json.loads(s)