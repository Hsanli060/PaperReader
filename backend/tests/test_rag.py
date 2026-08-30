"""
RAG 检索集成测试：跑真实 embedding API + 真 Chroma 库
运行：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_rag.py -v
前提：向量库里已索引过论文（paper_id=1，Mamba 论文 2312.00752）
"""
from app.rag.retriever import search

DISCUSSION = "A Discussion: Selection Mechanism"

def test_search_returns_results():
    """跨论文检索：正常问题应返回 top_k 条、字段齐全、按距离升序"""
    hits=search("注意力机制的公式是怎么算的？")

    assert len(hits)==5,f"应返回默认 5 块，实际 {len(hits)}"
    for h in hits:
        assert h["text"],"块原文不该是空字符串"
        assert h["section"], "章节名不该是空字符串"
        assert isinstance(h["paper_id"],int)
        # 余弦距离 0~2；超出这个范围说明库坏了（比如空间配置错了）
        assert 0<=h["distance"]<=2,f"距离越界: {h['distance']}"

    distances=[h["distance"] for h in hits]
    assert distances ==sorted(distances), f"结果没按距离升序: {distances}"


def test_search_filters_by_paper():
    """限定论文：返回的每条都属于该论文，一条不许混入"""
    hits=search("selective state space model", paper_id=3,top_k=3)

    assert len(hits)==3,f"应返回 3 块，实际 {len(hits)}"
    assert all(h["paper_id"]==3 for h in hits), \
        f"混入了别的论文: {[h['paper_id'] for h in hits]}"


def test_search_filters_by_section():
    """论文+章节双条件：走 $and 分支，所有结果必须来自指定章节"""
    hits=search("attention", paper_id=1, section=DISCUSSION, top_k=3)

    assert len(hits)==3
    assert all(h["section"]==DISCUSSION for h in hits), \
        f"混入了别的章节: {sorted({h['section'] for h in hits})}"

def test_search_top1_is_relevant():
    """相关性锚点：强相关问题，top1 距离必须落在强相关区间

    这是整套 RAG 的'准确率'代理指标：库没坏 + 模型没换 + 检索没写错，
    这个中文问题对英文论文的 top1 距离就应该 < 0.5（基准实测 0.37）。
    """
    hits=search("注意力机制的公式是怎么算的？")

    assert hits[0]["distance"]<0.5, \
        f"top1 距离 {hits[0]['distance']:.3f} 超过强相关阈值 0.5，检索质量可能退化"