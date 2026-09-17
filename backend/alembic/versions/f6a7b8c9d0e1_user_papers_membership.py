"""批②：论文归属表 user_papers（谁把这篇加进了自己的库）+ 存量回填

背景：全局共享库（FIX-3'）让"一人添加、所有人可见"。批②保持"全局只存一份"
（省下载/省向量），但把"可见性"改为成员制：
    - 列表 / 会话 scope / Agent 注入的 allowed_paper_ids / 删除 全按成员过滤
    - 重复添加 = 只往 user_papers 加一行（不重跑下载），新论文创建时同事务落归属

回填：每篇现存论文归给它的 added_by（"谁添加的"署名列早就存在），
added_at 取论文创建时间，保持顺序感。

downgrade 删表——归属信息丢弃可重建（重新加一遍即可），无不可再生数据。
"""
import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_papers",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), primary_key=True),
        sa.Column("added_at", sa.DateTime(), server_default=sa.text("now()")),
    )
    # 查询形状：①"我的库"= WHERE user_id=?（PK 前缀已覆盖）；
    #            ②"这篇的成员"= WHERE paper_id=?（单独索引，顺带给 FK 检查加速）
    op.create_index("ix_user_papers_paper_id", "user_papers", ["paper_id"])
    # 回填：每篇论文归给添加人（added_at 取论文创建时间）
    op.execute(
        "INSERT INTO user_papers (user_id, paper_id, added_at) "
        "SELECT added_by, id, created_at FROM papers ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_index("ix_user_papers_paper_id", table_name="user_papers")
    op.drop_table("user_papers")
