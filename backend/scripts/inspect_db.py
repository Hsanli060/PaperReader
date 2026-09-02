# 一次性检查脚本：查看数据库与向量库现状（迁移前体检）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/

from app.config import settings
from sqlalchemy import create_engine, text

e = create_engine(settings.DATABASE_URL)
with e.connect() as c:
    print("== papers ==")
    for r in c.execute(text("SELECT id,user_id,arxiv_id,status,created_at FROM papers ORDER BY id")):
        print(dict(r._mapping))
    print("== conversations ==")
    for r in c.execute(text("SELECT id,user_id,paper_id,title,created_at FROM conversations ORDER BY id")):
        print(dict(r._mapping))
    print("alembic version:", c.execute(text("SELECT version_num FROM alembic_version")).scalar())

print("== vector store ==")
from app.rag.vector_store import get_stats
print(get_stats())
