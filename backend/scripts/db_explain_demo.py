# -*- coding: utf-8 -*-
"""
EXPLAIN 实验（批次②）：复合索引 (conversation_id, id) 优化"会话消息回放"的前后对照。

用法（在 backend 目录下执行；只操作 [demo] 前缀的临时数据）：
    ../.venv/Scripts/python.exe -m alembic downgrade b2c3d4e5f6a7      # 前：单列索引
    ../.venv/Scripts/python.exe scripts/db_explain_demo.py --seed       # 造种子
    ../.venv/Scripts/python.exe scripts/db_explain_demo.py --explain    # 抓"前"计划（含 Sort）
    ../.venv/Scripts/python.exe -m alembic upgrade head                # 后：复合索引
    ../.venv/Scripts/python.exe scripts/db_explain_demo.py --explain    # 抓"后"计划（Sort 消失）
    ../.venv/Scripts/python.exe scripts/db_explain_demo.py --cleanup

两条演示查询（都在大会话上执行）：
    [A] 会话消息回放（现网真实查询形态）：WHERE conversation_id=? ORDER BY id
        前：单列索引只吃过滤，排序要额外 Sort；
        后：复合索引过滤+排序一条索引全包 → Sort 消失。
    [B] 取最近 20 条（演进形态）：ORDER BY id DESC LIMIT 20
        前：全量读+top-N 排序（或主键回扫逐行过滤）；
        后：索引反向回扫，读完 20 行即停。

为什么用 2 万条的大会话：小会话排序成本可忽略、计划器不会切换用有序索引扫描；
大会话才体现"过滤+排序一条索引全包"的价值。填充会话让大会话只占表 ~1/3，
贴近真实"多会话并存"形态。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.models.database import SessionLocal

DEMO_PREFIX = "[demo] explain"
BIG = 20000       # 大会话消息数
FILL_CONVS = 40   # 填充会话数
FILL_EACH = 1000  # 每个填充会话的消息数


def _get_or_create_user_id(db) -> int:
    """demo 会话也需要 user_id（NOT NULL）：借现成的第一个用户，没有就造一个"""
    uid = db.scalar(text("SELECT id FROM users ORDER BY id LIMIT 1"))
    if uid is None:
        db.execute(text("INSERT INTO users (username, password_hash) VALUES ('explain_demo', 'x')"))
        db.commit()
        uid = db.scalar(text("SELECT id FROM users ORDER BY id LIMIT 1"))
    return uid


def _cleanup_db(db) -> None:
    db.execute(text(
        "DELETE FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE title LIKE :p)"
    ), {"p": f"{DEMO_PREFIX}%"})
    db.execute(text("DELETE FROM conversations WHERE title LIKE :p"), {"p": f"{DEMO_PREFIX}%"})


def seed() -> None:
    db = SessionLocal()
    try:
        uid = _get_or_create_user_id(db)
        _cleanup_db(db)   # 幂等：重复 --seed 不会累积
        # 1) 大会话先建（其消息 id 最小 = 最"老"，[B] 演示的反向回扫要跨过填充数据）
        big = db.execute(
            text("INSERT INTO conversations (user_id, title) VALUES (:uid, :t) RETURNING id"),
            {"uid": uid, "t": f"{DEMO_PREFIX} big-{BIG}"},
        ).scalar()
        db.execute(text(
            f"INSERT INTO messages (conversation_id, role, content) "
            f"SELECT {big}, 'user', 'big-' || g FROM generate_series(1, {BIG}) AS g"
        ))
        # 2) 填充会话 + 各自消息（一条 SQL 交叉生成）
        for i in range(FILL_CONVS):
            db.execute(
                text("INSERT INTO conversations (user_id, title) VALUES (:uid, :t)"),
                {"uid": uid, "t": f"{DEMO_PREFIX} fill-{i:02d}"},
            )
        db.commit()
        db.execute(text(
            f"INSERT INTO messages (conversation_id, role, content) "
            f"SELECT c.id, 'user', 'fill-' || c.id || '-' || g "
            f"FROM conversations c, generate_series(1, {FILL_EACH}) AS g "
            "WHERE c.title LIKE :p"
        ), {"p": f"{DEMO_PREFIX} fill-%"})
        db.commit()
        db.execute(text("ANALYZE messages"))
        db.commit()
        total = db.scalar(text("SELECT count(*) FROM messages"))
        print(f"[seed] 完成：messages 总量={total}（大会话 {BIG} 条 + 填充 {FILL_CONVS}×{FILL_EACH}）")
    finally:
        db.close()


def _show_plan(db, label: str, sql: str, cid: int) -> None:
    print(f"---- {label} ----")
    lines = [r[0] for r in db.execute(
        text(f"EXPLAIN (ANALYZE, BUFFERS) {sql}"), {"cid": cid}
    ).fetchall()]
    for l in lines:
        print("   ", l)
    print()


def explain() -> None:
    db = SessionLocal()
    try:
        idx = [r[0] for r in db.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='messages' AND indexname LIKE 'ix_messages%'"
        )).fetchall()]
        cid = db.scalar(text("SELECT id FROM conversations WHERE title = :t"),
                        {"t": f"{DEMO_PREFIX} big-{BIG}"})
        if cid is None:
            print("[explain] 没找到 demo 数据，先跑 --seed")
            return
        print(f"[explain] 当前索引: {idx}；大会话 cid={cid}（{BIG} 条消息）\n")
        _show_plan(db, "[A] 会话消息回放：WHERE conversation_id=? ORDER BY id",
                   "SELECT * FROM messages WHERE conversation_id = :cid ORDER BY id", cid)
        _show_plan(db, "[B] 取最近 20 条：ORDER BY id DESC LIMIT 20",
                   "SELECT * FROM messages WHERE conversation_id = :cid ORDER BY id DESC LIMIT 20", cid)
    finally:
        db.close()


def cleanup() -> None:
    db = SessionLocal()
    try:
        _cleanup_db(db)
        db.commit()
        print("[cleanup] demo 数据已清")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="EXPLAIN 前后对比（复合索引实验）")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    if args.seed:
        seed()
    if args.explain:
        explain()
    if args.cleanup:
        cleanup()
    if not (args.seed or args.explain or args.cleanup):
        ap.print_help()


if __name__ == "__main__":
    main()
