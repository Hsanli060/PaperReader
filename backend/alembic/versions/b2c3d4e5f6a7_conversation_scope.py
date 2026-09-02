"""FIX-3' 迁移 B：会话 scope 多对多

- conversations.paper_id 单列 → conversation_papers 连接表
- 历史数据搬移：老会话的单篇 paper_id INSERT 进连接表（scope 不许丢）
- 顺带补 papers.last_error 列（FIX-2 后台流水线的失败信息落点）
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 连接表
    op.create_table(
        "conversation_papers",
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), primary_key=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), primary_key=True),
    )

    # 2. 历史数据搬移：老会话的单 paper_id → 连接表一行
    op.execute(
        "INSERT INTO conversation_papers (conversation_id, paper_id) "
        "SELECT id, paper_id FROM conversations WHERE paper_id IS NOT NULL"
    )

    # 3. 删老列
    op.drop_column("conversations", "paper_id")

    # 4. FIX-2：流水线失败信息落点
    op.add_column("papers", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    # 回滚：把连接表数据压回单列（多 scope 的会话只保留第一条——有损回滚，设计如此）
    op.add_column("conversations", sa.Column("paper_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE conversations c SET paper_id = ("
        "  SELECT cp.paper_id FROM conversation_papers cp"
        "  WHERE cp.conversation_id = c.id ORDER BY cp.paper_id LIMIT 1)"
    )
    op.drop_column("papers", "last_error")
    op.drop_table("conversation_papers")
