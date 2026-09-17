# -*- coding: utf-8 -*-
"""批②：论文归属（user_papers）——可见性 / 去重加入 / 退会删除 / scope 与 Agent 注入

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_user_papers.py -v
前提：本地 PG 已启动；不调 LLM / 嵌入（chat 用假 agent；重复添加走"加入归属"路径，
不触发后台流水线）。用例自建用户与论文、测后清干净，不依赖存量数据。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.main import app
from app.models.database import SessionLocal
from app.models.orm import Conversation, Message, Paper, User, conversation_papers, user_papers
from app.rag.retriever import _build_where
from app.security import create_access_token

client = TestClient(app)


# ---------- 造数据 / 清理 ----------

def _mk_user() -> int:
    db = SessionLocal()
    try:
        u = User(username=f"member_{uuid.uuid4().hex[:8]}", password_hash="x")
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _mk_paper(added_by: int, arxiv: str | None = None) -> int:
    db = SessionLocal()
    try:
        p = Paper(added_by=added_by, arxiv_id=arxiv, title="MEMBER-TEST",
                  authors="[]", status="indexed")
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


def _add_member(uid: int, pid: int) -> None:
    db = SessionLocal()
    try:
        db.execute(pg_insert(user_papers).values(user_id=uid, paper_id=pid).on_conflict_do_nothing())
        db.commit()
    finally:
        db.close()


def _member_count(pid: int) -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.count()).select_from(user_papers)
                         .where(user_papers.c.paper_id == pid))
    finally:
        db.close()


def _headers(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(uid, 'member')}"}


@pytest.fixture
def world():
    """两个用户 + 两篇论文：
        A 的库=[pa]；B 的库=[pb]；pa 不属于 B（用来验证"不可见"）。
    """
    ua, ub = _mk_user(), _mk_user()
    arxiv = f"0000.{uuid.uuid4().int % 90000 + 10000:05d}"
    pa = _mk_paper(added_by=ua, arxiv=arxiv)
    pb = _mk_paper(added_by=ub)
    _add_member(ua, pa)
    _add_member(ub, pb)

    yield {"ua": ua, "ub": ub, "pa": pa, "pb": pb, "arxiv": arxiv}

    # 清理：消息→会话→scope 引用→归属→论文→用户（顺序避开外键）
    db = SessionLocal()
    try:
        conv_ids = list(db.scalars(select(Conversation.id).where(Conversation.user_id.in_([ua, ub]))))
        if conv_ids:
            db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
            db.execute(delete(conversation_papers).where(conversation_papers.c.conversation_id.in_(conv_ids)))
            db.execute(delete(Conversation).where(Conversation.id.in_(conv_ids)))
        db.execute(delete(user_papers).where(user_papers.c.user_id.in_([ua, ub])))
        db.execute(delete(user_papers).where(user_papers.c.paper_id.in_([pa, pb])))
        db.execute(delete(conversation_papers).where(conversation_papers.c.paper_id.in_([pa, pb])))
        db.execute(delete(Paper).where(Paper.id.in_([pa, pb])))
        db.execute(delete(User).where(User.id.in_([ua, ub])))
        db.commit()
    finally:
        db.close()


def _fake_agent_capture(monkeypatch, box: dict):
    """把 agent 换成假流，捕获服务端注入的 allowed_paper_ids"""
    from app.agents.react_agent import ReactAgent

    async def fake(self, user_input, allowed_paper_ids=None):
        box["allowed"] = allowed_paper_ids
        yield {"type": "content", "text": "ok"}

    monkeypatch.setattr(ReactAgent, "run_stream_async", fake)


# ---------- 1. 可见性：只有成员看得见 ----------

def test_visibility_only_members(world):
    ids_a = {p["id"] for p in client.get("/api/papers", headers=_headers(world["ua"])).json()["items"]}
    assert world["pa"] in ids_a and world["pb"] not in ids_a

    ids_b = {p["id"] for p in client.get("/api/papers", headers=_headers(world["ub"])).json()["items"]}
    assert world["pb"] in ids_b and world["pa"] not in ids_b

    # 非成员直达详情/摘要 → 404（不泄露存在性）
    assert client.get(f"/api/papers/{world['pa']}", headers=_headers(world["ub"])).status_code == 404
    assert client.get(f"/api/papers/{world['pa']}/summary", headers=_headers(world["ub"])).status_code == 404


# ---------- 2. 重复添加 = 加入归属，不重下不重插 ----------

def test_duplicate_add_joins_not_duplicates(world):
    resp = client.post("/api/papers/arxiv", headers=_headers(world["ub"]),
                       json={"arxiv": world["arxiv"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["duplicated"] is True
    assert body["id"] == world["pa"]
    assert _member_count(world["pa"]) == 2, "重复添加应只在归属表加一行"

    db = SessionLocal()
    try:
        n = db.scalar(select(func.count()).select_from(Paper).where(Paper.arxiv_id == world["arxiv"]))
    finally:
        db.close()
    assert n == 1, "全局仍然只有一篇论文行"

    ids_b = {p["id"] for p in client.get("/api/papers", headers=_headers(world["ub"])).json()["items"]}
    assert world["pa"] in ids_b, "加入后 B 的库里应能看到 pa"


# ---------- 3. 删除 = 退会；最后一人退会才全局清 ----------

def test_delete_is_leave_until_last(world):
    # B 先加入 pa（这样 A 退会时论文还有成员）
    client.post("/api/papers/arxiv", headers=_headers(world["ub"]), json={"arxiv": world["arxiv"]})

    # A 退会：论文行还在、向量不清、成员 -1
    assert client.delete(f"/api/papers/{world['pa']}", headers=_headers(world["ua"])).status_code == 200
    db = SessionLocal()
    try:
        assert db.get(Paper, world["pa"]) is not None, "还有成员时不该删论文行"
    finally:
        db.close()
    assert _member_count(world["pa"]) == 1

    # 最后一人（B）退会：论文行清掉
    assert client.delete(f"/api/papers/{world['pa']}", headers=_headers(world["ub"])).status_code == 200
    db = SessionLocal()
    try:
        assert db.get(Paper, world["pa"]) is None, "最后一个成员退会应清掉论文行"
    finally:
        db.close()
    assert _member_count(world["pa"]) == 0


# ---------- 4. scope 校验：只能圈"我的库" ----------

def test_scope_must_be_my_library(world, monkeypatch):
    # B 想圈 A 的论文（B 不是成员）→ 404
    resp = client.post("/api/chat", headers=_headers(world["ub"]),
                       json={"question": "q", "paper_ids": [world["pa"]]})
    assert resp.status_code == 404, resp.text

    # 把 pa 加进 B 的库后可圈；服务端注入的 scope 应恰好是 [pa]
    _add_member(world["ub"], world["pa"])
    box: dict = {}
    _fake_agent_capture(monkeypatch, box)
    resp = client.post("/api/chat", headers=_headers(world["ub"]),
                       json={"question": "q", "paper_ids": [world["pa"]]})
    assert resp.status_code == 200, resp.text
    assert box["allowed"] == [world["pa"]]


# ---------- 5. 空 scope = 我的整个论文库（绝不横扫全局） ----------

def test_empty_scope_equals_my_library(world, monkeypatch):
    box: dict = {}
    _fake_agent_capture(monkeypatch, box)
    resp = client.post("/api/chat", headers=_headers(world["ua"]), json={"question": "q"})
    assert resp.status_code == 200, resp.text
    assert box["allowed"] == [world["pa"]], "全库检索=我的库（pb 属于 B，绝不能进来）"


# ---------- 6. 检索安全阀：空列表匹配零条 ----------

def test_build_where_empty_list_matches_nothing():
    assert _build_where(paper_ids=[], section=None) == {"paper_id": {"$in": [-1]}}
    assert _build_where(paper_ids=None) is None
    assert _build_where(paper_ids=[1, 2]) == {"paper_id": {"$in": [1, 2]}}


def test_chroma_empty_in_returns_nothing():
    """真查一次向量库（本地小探针向量，不调嵌入 API）：空归属必须零命中"""
    from app.rag.vector_store import papers_col

    probe = [0.01] * 1024
    r = papers_col.query(query_embeddings=[probe], n_results=5,
                         where=_build_where(paper_ids=[]), include=["metadatas"])
    assert r["ids"][0] == [], "空归属列表必须匹配零条——否则会越权扫到别人的论文"
