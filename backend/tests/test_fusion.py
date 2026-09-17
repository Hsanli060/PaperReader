"""
RRF 融合的单测：纯函数，不联网不调模型，所以可以断言精确顺序。

跑：pytest tests/test_fusion.py -v
"""

from app.rag.fusion import rrf_fuse

def _hit(cid:str)->dict:
    return {"id": cid, "text": f"text-{cid}", "section": "s", "paper_id": 1, "distance": 0.5}

def test_common_hit_wins():
    """两路都命中的块，应该排在"只有一路命中且排第一"的块前面"""
    path_a=[_hit("only_a"),_hit("common")]
    path_b=[_hit("only_b"), _hit("common")]
    fused=rrf_fuse([path_a,path_b])

    assert fused[0]["id"]=="common"
    assert fused[0]["sources"]==[0,1]
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]

def test_dedup_by_id():
    """同一个块在两路里都出现，融合后只能有一条"""
    fused=rrf_fuse([[_hit("a")], [_hit("a")]])
    assert len(fused) == 1
    assert fused[0]["id"] == "a"

def test_rank1_beats_rank2_within_one_path():
    """只有一路时，融合后的顺序必须还原成召回顺序"""
    fused = rrf_fuse([[_hit("first"), _hit("second"), _hit("third")]])
    assert [h["id"] for h in fused] == ["first", "second", "third"]

def test_sources_records_all_paths():
    fused = rrf_fuse([[_hit("x")], [_hit("y"), _hit("x")]])
    by_id = {h["id"]: h for h in fused}
    assert by_id["x"]["sources"] == [0, 1]
    assert by_id["y"]["sources"] == [1]


def test_empty_input():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []