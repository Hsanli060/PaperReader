"""
删除论文时缓存也要一起清：DELETE /papers/{id} 必须把 summary:{id} / citations:{id} 一并删掉。

背景（2026-09-16）：删除接口原先只删 PG 行 + 向量块，Redis 里的派生缓存留在原地。
PG 的 id 正常不复用，所以平时看不出来；但库重置 / 备份恢复到旧 ID 时，
新论文会读到旧论文的摘要——删了等于没删干净。

用例自己插一篇临时论文再删掉，不碰开发用的 paper 1/3。

运行：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_papers_delete_api.py -v
前提：本地 PG / Redis 已启动；不调 LLM（缓存是预先塞进去的）
"""
import json

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.main import app
from app.config import settings
from app.models.database import SessionLocal
from app.models.orm import Paper, User, user_papers
from app.security import create_access_token

client = TestClient(app)

# get_current_user 只解 token 不查库，任意 user_id 都能通过鉴权
HEADERS = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}

_r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)


def _db_get(paper_id: int):
    """独立开会话查一行（请求用的 session 已经关了）"""
    db = SessionLocal()
    try:
        return db.get(Paper, paper_id)
    finally:
        db.close()


@pytest.fixture
def temp_paper():
    """临时插一篇论文专供删除（避开 paper 1/3），测完确保不残留。

    added_by 必须给真实 user id：模型（Mapped[int]）与库表（NOT NULL）一致，
    插 null 会吃 IntegrityError。
    批②：删除接口要求"我在论文的归属里"——给 token 用户(1)补一行归属。
    """
    db = SessionLocal()
    try:
        user_id = db.scalar(select(User.id).limit(1))
        if user_id is None:
            pytest.skip("库里还没有用户，跳过（需要至少一条 users 记录）")
        if db.get(User, 1) is None:
            pytest.skip("库里没有 user 1（token 用户），跳过")
        paper = Paper(added_by=user_id, title="DELETE-CACHE-TEST",
                      authors="[]", status="pending")
        db.add(paper)
        db.flush()
        db.execute(pg_insert(user_papers).values(user_id=1, paper_id=paper.id)
                   .on_conflict_do_nothing())
        db.commit()
        pid = paper.id
    finally:
        db.close()

    yield pid

    # 兜底：用例若在删除前就失败，这里把残留收拾掉（先清归属再删论文，避开外键）
    db = SessionLocal()
    try:
        db.execute(delete(user_papers).where(user_papers.c.paper_id == pid))
        leftover = db.get(Paper, pid)
        if leftover is not None:
            db.delete(leftover)
        db.commit()
    finally:
        db.close()
    _r.delete(f"summary:{pid}", f"citations:{pid}")


# ==================== 核心：三个地方一起删 ====================

def test_delete_paper_clears_derived_cache(temp_paper):
    """删论文 = PG 行 + 向量块 + 派生缓存，一个都不能剩。

    回归：曾漏掉缓存，PG 行没了但 summary:3 还在 Redis 里躺 24 小时。
    """
    pid = temp_paper
    _r.set(f"summary:{pid}", json.dumps({"problem": "P"}, ensure_ascii=False), ex=600)
    _r.set(f"citations:{pid}", json.dumps({"items": []}, ensure_ascii=False), ex=600)

    resp = client.delete(f"/api/papers/{pid}", headers=HEADERS)
    assert resp.status_code == 200, f"删除应 200，实际 {resp.status_code}: {resp.text}"
    assert resp.json() == {"deleted": pid}

    assert _r.get(f"summary:{pid}") is None, "PG 删了 summary 缓存还在——没删干净"
    assert _r.get(f"citations:{pid}") is None, "citations 缓存也要一起清"
    assert _db_get(pid) is None, "PG 行本身也该没了"
    # 批②：唯一成员退会 → 归属行也清干净
    db = SessionLocal()
    try:
        n = db.scalar(select(func.count()).select_from(user_papers)
                      .where(user_papers.c.paper_id == pid))
    finally:
        db.close()
    assert n == 0, "最后一个成员退会后归属行也该清掉"


def test_delete_only_touches_its_own_keys(temp_paper):
    """只清自己那两个键，不能顺手把别人的缓存也扫了"""
    pid = temp_paper
    neighbor = f"summary:{pid + 1}"
    original, original_ttl = _r.get(neighbor), _r.ttl(neighbor)
    _r.set(neighbor, "邻居的摘要", ex=600)
    _r.set(f"summary:{pid}", json.dumps({"problem": "P"}, ensure_ascii=False), ex=600)
    try:
        client.delete(f"/api/papers/{pid}", headers=HEADERS)
        assert _r.get(neighbor) == "邻居的摘要", "删 A 不该动到 B 的缓存"
    finally:
        if original is not None:
            _r.set(neighbor, original, ex=max(original_ttl, 1))
        else:
            _r.delete(neighbor)


# ==================== 边界 ====================

def test_delete_survives_redis_down(temp_paper, monkeypatch):
    """Redis 挂掉时删除照样成功（铁律 1：缓存出问题不连累主流程）"""
    from app.services.cache import llm_cache

    pid = temp_paper
    _r.set(f"summary:{pid}", json.dumps({"problem": "P"}, ensure_ascii=False), ex=600)
    monkeypatch.setattr(llm_cache, "_r", None)

    resp = client.delete(f"/api/papers/{pid}", headers=HEADERS)
    assert resp.status_code == 200, f"Redis 挂了不该影响删除，实际 {resp.status_code}: {resp.text}"
    assert _db_get(pid) is None, "PG 行必须删掉"


def test_delete_missing_paper_still_404():
    """删不存在的论文仍然 404（新逻辑不该动到存在性校验）"""
    resp = client.delete("/api/papers/999999", headers=HEADERS)
    assert resp.status_code == 404
