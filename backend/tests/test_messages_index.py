# -*- coding: utf-8 -*-
"""
迁移回归测试（批次②）：messages 索引形态。

锁 "单列索引已删、复合 (conversation_id, id) 已在" —— 防止迁移被回退或误删。
跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_messages_index.py -v
"""
from sqlalchemy import text

from app.models.database import SessionLocal


def test_messages_index_shape():
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename='messages' AND indexname LIKE 'ix_messages%'"
        )).fetchall()
    finally:
        db.close()

    names = {r[0] for r in rows}
    assert "ix_messages_conversation_id_id" in names, f"复合索引缺失: {names}"
    assert "ix_messages_conversation_id" not in names, f"单列索引应已被迁移删除: {names}"

    # 复合索引列序：conversation_id 在前（前缀可单独用）+ id 在后（排序用）
    ddl = next(r[1] for r in rows if r[0] == "ix_messages_conversation_id_id")
    assert "(conversation_id, id)" in ddl, ddl
