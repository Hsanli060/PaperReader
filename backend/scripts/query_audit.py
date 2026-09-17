# -*- coding: utf-8 -*-
"""
SQL 净条数审计（批次②·观测）：直连 ORM 复现端点的查询形状，数每个操作发了几条 SQL。

用法（在 backend 目录下执行）：
    ../.venv/Scripts/python.exe scripts/query_audit.py

口径：
    对每项操作：新建一个 QueryCounter 放进 contextvar → 执行 → 读计数
    （与请求中间件同一套量具；每个审计项用【全新 Session】，避免 identity map 缓存干扰）。

N+1 对照实验（本脚本的重点）：
    同一"取会话列表 + 逐个拼 paper_ids"的操作，两种加载策略对比——
        A. 默认（papers 关系 lazy="selectin"）：主查询 + 1 条批量 IN = 2 条
        B. per-query 覆盖为 lazyload：主查询 + 每个会话 1 条 = 1 + N 条
    结论：N+1 不是"必须重写代码才能避免"——loader option 是 per-query 的，随时可切换。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.orm import lazyload

from app.models.database import QueryCounter, SessionLocal, _query_counter
from app.models.orm import Conversation, Message, Paper


def count_sql(fn):
    """执行 fn，返回 (SQL 条数, fn 返回值)。计数器与请求中间件同源。"""
    counter = QueryCounter()
    token = _query_counter.set(counter)
    try:
        result = fn()
    finally:
        _query_counter.reset(token)
    return counter.n, result


# ---- 审计项（各自开新 Session，复现端点的查询形状） ----

def item_papers_list():
    """GET /api/papers：总数 count + 本页查询"""
    db = SessionLocal()
    try:
        base = select(Paper)
        total = db.scalar(select(func.count()).select_from(base.subquery()))
        rows = db.scalars(base.order_by(Paper.id.desc()).offset(0).limit(10)).all()
        return total, len(rows)
    finally:
        db.close()


def item_conv_list_selectin():
    """GET /api/chat/history：会话列表 + paper_ids（默认 selectin 加载）"""
    db = SessionLocal()
    try:
        rows = db.scalars(select(Conversation).order_by(Conversation.id.desc())).all()
        return [[p.id for p in c.papers] for c in rows]
    finally:
        db.close()


def item_conv_list_lazyload():
    """同上，但 per-query 覆盖为 lazyload —— N+1 反面教材现场"""
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(Conversation).order_by(Conversation.id.desc())
            .options(lazyload(Conversation.papers))
        ).all()
        return [[p.id for p in c.papers] for c in rows]
    finally:
        db.close()


def item_history_detail(cid):
    """GET /api/chat/history/{id}：会话 get + 消息全量"""
    db = SessionLocal()
    try:
        conv = db.get(Conversation, cid)
        msgs = db.scalars(
            select(Message).where(Message.conversation_id == cid).order_by(Message.id.asc())
        ).all()
        return conv, msgs
    finally:
        db.close()


def main():
    # 数据现状
    db = SessionLocal()
    try:
        n_convs = db.scalar(select(func.count()).select_from(Conversation))
        n_msgs = db.scalar(select(func.count()).select_from(Message))
        n_papers = db.scalar(select(func.count()).select_from(Paper))
        any_conv = db.scalar(select(Conversation.id).order_by(Conversation.id).limit(1))
    finally:
        db.close()
    print(f"== 数据现状 == conversations={n_convs}  messages={n_msgs}  papers={n_papers}\n")

    n1, r1 = count_sql(item_papers_list)
    print(f"[1] 论文列表（count + 分页）                  sql = {n1}")

    n2a, r2a = count_sql(item_conv_list_selectin)
    print(f"[2A] 会话列表 + paper_ids（默认 selectin）    sql = {n2a}   （会话数 N={len(r2a)}）")

    n2b, r2b = count_sql(item_conv_list_lazyload)
    print(f"[2B] 同上、覆盖为 lazyload（N+1 现场）       sql = {n2b}   （1 + N 条）")

    if any_conv is not None:
        n3, _ = count_sql(lambda: item_history_detail(any_conv))
        print(f"[3] 会话详情（get + 消息全量）                sql = {n3}   （conv_id={any_conv}）")

    print("\n== 对照小结 ==")
    print(f"· selectin：{n2a} 条（固定 +1 批量）  ｜  lazyload：{n2b} 条（随 N 线性涨）")
    print(f"· 差值 {n2b - n2a} 条 = N+1 的现场数量（N={len(r2a)}）")


if __name__ == "__main__":
    main()
