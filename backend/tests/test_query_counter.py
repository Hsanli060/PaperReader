# -*- coding: utf-8 -*-
"""
SQL 查询计数器回归测试（批次②·观测）。

覆盖：
    - 响应头 X-DB-Queries 暴露实际 SQL 条数（health=0；论文列表=2；单篇详情=1）
    - 每请求独立：连续两次请求不累计（各自重置而不是叠加）
    - 无请求上下文（脚本/后台任务直连 DB）：监听器静默跳过，不计数、不报错

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_query_counter.py -v
前提：本地 PG / Redis 已启动；不调 LLM。
说明：数字断言锁的是"当前实现的查询形状"——端点改查询时同步改这里。
"""
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.main import app
from app.models.database import SessionLocal, _query_counter
from app.models.orm import Paper
from app.security import create_access_token

client = TestClient(app)
HEADERS = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}


def test_health_zero_queries():
    """health 不碰库：sql=0（0 值也应正常上报）"""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers.get("X-DB-Queries") == "0"


def test_papers_list_two_queries():
    """论文列表 = count(*) + 分页查询 = 2"""
    r = client.get("/api/papers", headers=HEADERS)
    assert r.status_code == 200
    assert r.headers.get("X-DB-Queries") == "2"


def test_paper_detail_one_query():
    """单篇详情 = 主键 get = 1"""
    db = SessionLocal()
    try:
        pid = db.scalar(select(Paper.id).order_by(Paper.id).limit(1))
    finally:
        db.close()
    r = client.get(f"/api/papers/{pid}", headers=HEADERS)
    assert r.status_code == 200
    assert r.headers.get("X-DB-Queries") == "1"


def test_counter_resets_per_request():
    """两次请求各自从 0 数起（第二次不累计成 4）"""
    r1 = client.get("/api/papers", headers=HEADERS)
    r2 = client.get("/api/papers", headers=HEADERS)
    assert r1.headers.get("X-DB-Queries") == "2"
    assert r2.headers.get("X-DB-Queries") == "2"


def test_noop_without_request_context():
    """无上下文直连 DB：静默跳过（脚本/后台任务不会炸）"""
    assert _query_counter.get() is None
    db = SessionLocal()
    try:
        db.scalar(select(func.count()).select_from(Paper))
    finally:
        db.close()
    assert _query_counter.get() is None
