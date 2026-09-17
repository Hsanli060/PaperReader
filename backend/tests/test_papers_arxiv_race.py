# -*- coding: utf-8 -*-
"""
arXiv 去重竞态修复回归（批次③）："查重-插入"窗口的兜底路径。

真实竞态（两个请求同时通过查重、一起插）窗口很窄，单进程测试里复现不了；
这里用"确定性模拟"覆盖兜底分支：
    1. 预置一行（模拟"另一个请求已抢先插入"）；
    2. monkeypatch Session.scalar：让本请求的第一次查重返回 None
       （模拟它查的时候对方还没提交——正是竞态窗口里发生的事）；
    3. 本请求插入 → 撞唯一索引 → 走 except 兜底 → rollback 重查 → 返回 duplicated。

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_papers_arxiv_race.py -v
前提：本地 PG 已启动；不调 LLM、不碰 arxiv 网络（重复路径在调度后台任务之前就返回了）。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.main import app
from app.models.database import SessionLocal
from app.models.orm import Paper, User, user_papers
from app.security import create_access_token

client = TestClient(app)
HEADERS = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}

RACE_ARXIV = "0000.77777"


def _cleanup():
    db = SessionLocal()
    try:
        rows = list(db.scalars(select(Paper).where(Paper.arxiv_id == RACE_ARXIV)))
        for row in rows:
            # 批②：先清归属行再删论文（外键顺序）
            db.execute(delete(user_papers).where(user_papers.c.paper_id == row.id))
        for row in rows:
            db.delete(row)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seeded_winner():
    """预置"抢先插入"的那一行，测后清理"""
    _cleanup()
    db = SessionLocal()
    try:
        user_id = db.scalar(select(User.id).limit(1))
        if user_id is None:
            pytest.skip("库里还没有用户，跳过（需要至少一条 users 记录）")
        if db.get(User, 1) is None:
            pytest.skip("库里没有 user 1（token 用户），跳过")
        winner = Paper(added_by=user_id, arxiv_id=RACE_ARXIV,
                       title="RACE-WINNER", authors="[]", status="pending")
        db.add(winner)
        db.commit()
        winner_id = winner.id
    finally:
        db.close()
    yield winner_id
    _cleanup()


def test_check_then_insert_race_falls_back(monkeypatch, seeded_winner):
    """模拟竞态窗口：本请求查重错过 → 插入撞唯一索引 → 兜底返回已有行（不再 500）"""
    real_scalar = Session.scalar
    state = {"missed_once": False}

    def fake_scalar(self, statement, *args, **kwargs):
        # 只拦截"本请求的第一次论文查重"，其余调用（含兜底后的重查）走原逻辑
        if not state["missed_once"] and "papers.arxiv_id" in str(statement):
            state["missed_once"] = True
            return None
        return real_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", fake_scalar)

    resp = client.post("/api/papers/arxiv", json={"arxiv": RACE_ARXIV}, headers=HEADERS)
    assert resp.status_code == 200, f"兜底后应 200，实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["duplicated"] is True, "应报告为重复添加"
    assert body["id"] == seeded_winner, "应返回已存在那一行的 id"

    # 库里仍然只有一行（没有插出第二行）
    db = SessionLocal()
    try:
        n = db.scalar(select(func.count()).select_from(Paper).where(Paper.arxiv_id == RACE_ARXIV))
        assert n == 1, f"应恰好 1 行，实际 {n}"
        # 批②：同时把"我"写进归属（竞态兜底路径也保归属）
        m = db.scalar(select(func.count()).select_from(user_papers)
                      .where(user_papers.c.paper_id == seeded_winner, user_papers.c.user_id == 1))
        assert m == 1, "重复添加（竞态兜底）应写下归属行"
    finally:
        db.close()
