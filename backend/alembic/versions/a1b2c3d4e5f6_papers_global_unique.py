"""FIX-3' 迁移 A：papers 全局唯一化

- user_id 列 → added_by（保留署名，去掉隔离语义）
- (user_id, arxiv_id) 联合唯一 → arxiv_id 单列唯一
- 迁移前数据已由 dedupe 脚本前置检查；若有重复 arxiv_id 会建唯一索引失败，
  所以这里先在迁移内做安全合并（同 arxiv_id 保留最小 id，重映射 conversations.paper_id）
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "1d3f24c256be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 合并重复 arxiv_id：同 arxiv_id 保留最小 id，conversations.paper_id 重映射
    conn = op.get_bind()
    dup_rows = conn.execute(sa.text(
        "SELECT arxiv_id FROM papers GROUP BY arxiv_id HAVING COUNT(*) > 1"
    )).fetchall()
    for (arxiv_id,) in dup_rows:
        rows = conn.execute(sa.text(
            "SELECT id FROM papers WHERE arxiv_id = :a ORDER BY id ASC"
        ), {"a": arxiv_id}).fetchall()
        keep_id = rows[0][0]
        for (victim_id,) in rows[1:]:
            conn.execute(sa.text(
                "UPDATE conversations SET paper_id = :k WHERE paper_id = :v"
            ), {"k": keep_id, "v": victim_id})
            conn.execute(sa.text("DELETE FROM papers WHERE id = :i"), {"i": victim_id})

    # 2. 联合唯一约束 → 列改名 → 单列唯一
    op.drop_constraint("uq_papers_user_arxiv", "papers", type_="unique")
    op.alter_column("papers", "user_id", new_column_name="added_by")
    op.create_unique_constraint("uq_papers_arxiv", "papers", ["arxiv_id"])


def downgrade() -> None:
    # 回滚：恢复联合唯一（added_by → user_id）；若已有重复 arxiv_id 回滚会失败——设计如此，全局唯一不可逆推
    op.drop_constraint("uq_papers_arxiv", "papers", type_="unique")
    op.alter_column("papers", "added_by", new_column_name="user_id")
    op.create_unique_constraint("uq_papers_user_arxiv", "papers", ["user_id", "arxiv_id"])
