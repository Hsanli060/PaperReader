"""
免费联网搜索（DuckDuckGo，ddgs 库，无需 API key）
知识库里查不到的东西（最新进展、代码仓库、作者主页）靠它
"""
from ddgs import DDGS

def web_search(query:str,max_results:int=5)->list[dict]:
    """联网搜索：搜索词 → DuckDuckGo → 结果列表。

    :param query: (str) 搜索词，如 "Mamba 后续改进工作"
    :param max_results: (int) 要几条结果，默认 5
    :return: list[dict]，每条键固定为 title / url / snippet：
        [
            {"title": 标题,
             "url": 完整URL,
             "snippet":  网页文本摘要
        ]
    """
    # ddgs 原生返回字段是 title/href/body，这里统一改成项目用的
    # title/url/snippet —— 换搜索源时只改这一个函数，用它的代码无感知
    # 用 .get 兜底：搜索结果偶尔缺字段，缺就给空串，别让整个调用炸掉
    raw = DDGS().text(query, max_results=max_results)
    return [
        {"title":r.get("title",""),
         "url":r.get("href",""),
         "snippet":r.get("body","")
         }
        for r in raw
    ]