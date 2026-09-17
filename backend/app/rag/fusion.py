"""
RRF（Reciprocal Rank Fusion，倒数排名融合）：把多路召回的结果合并成一份去重列表。

为什么用"排名"而不是"分数"：
    不同召回路径的分数根本不可比。向量路给的是余弦距离（越小越好，0~2），
    假如以后加了 BM25 路，给的是关键词得分（越大越好，可能到几十）。
    量纲不同、方向相反，直接相加毫无意义。
    RRF 只用"这个块在这路里排第几"这一个信息，天然绕开了量纲问题。

公式：score(块) = Σ 1/(k + rank)，rank 从 1 开始数。
    k 默认 60（RRF 原论文取值）。k 越大，头部名次之间的差距被压得越平。
    效果上：一个"两路都排第 3"的块，得分 2/63 ≈ 0.0317，
    而一个"一路排第 1、另一路完全没命中"的块，得分 1/61 ≈ 0.0164。
    也就是说 RRF 更信任"被多条路径共同认可"的块——这正是融合的目的。
"""

# RRF 平滑常数，原论文推荐 60。改小会让"某一路的第一名"更占优势
RRF_K = 60

def rrf_fuse(ranked_lists:list[list[dict]],k:int=RRF_K)->list[dict]:
    """把多路召回结果按 RRF 分数融合成一个去重列表。

    :param ranked_lists: 每路一个列表，每个列表内部已按相关性从好到坏排好。
        元素必须有 "id" 键（块主键，用来给不同路的同一块认亲）。
    :param k: RRF 平滑常数，默认 60
    :return: 合并去重后按 rrf_score 从高到低排的列表。
        每个元素是原字典的副本 + 两个新键：
            "rrf_score": float   融合得分
            "sources": list[int] 被第几路命中（从 0 数，如 [0,1] = 两路都命中）
        注意 "distance" 保留的是"第一次遇到这个块时那一路"的距离值——
        它只是个参考量，排序已经不再看它了。
    """
    scores: dict[str, float] = {}  # 块 id → 累计 RRF 得分
    sources: dict[str, list[int]] = {}  # 块 id → 命中它的路径下标
    store: dict[str, dict] = {}  # 块 id → 第一次见到的原始字典

    for path_i,hits in enumerate(ranked_lists):
        for rank,hit in enumerate(hits,start=1):
            cid=hit["id"]
            scores[cid]=scores.get(cid,0.0)+1.0/(k+rank)
            sources.setdefault(cid,[]).append(path_i)
            store.setdefault(cid,hit)

    merged=[]
    for cid,score in scores.items():
        item=dict(store[cid])
        item["rrf_score"]=score
        item["sources"]=sources[cid]
        merged.append(item)

    merged.sort(key=lambda x: x["rrf_score"], reverse=True)
    return merged