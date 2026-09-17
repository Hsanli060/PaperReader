"""批次②验收对齐：修平 papers 表"模型↔库"历史偏差（FIX-3' 遗留，非本批次引入）

跑 alembic check 暴露 6 处偏差，全部落在 papers（FIX-3' 迁移时留下），逐条磨平：

    1. 列早已更名 added_by，但索引名还是旧名 ix_papers_user_id
       → 更名对齐为 ix_papers_added_by（纯元数据）
    2. arxiv_id 的唯一性有"两条腿"：uq_papers_arxiv 约束 + 非唯一索引 ix_papers_arxiv_id；
       模型用"唯一索引"表达（index=True, unique=True）
       → 删约束、换唯一索引（唯一性照旧强制，去掉重复表达）
    3. added_by 可空性：库为 NOT NULL（保留——每篇论文必有添加者），
       模型端同步收敛为 Mapped[int]（orm.py 同批修改，不在此迁移）

纯索引/命名对齐，无数据变更；downgrade 完整还原为 FIX-3' 后的状态。
"""
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 索引跟随列名：ix_papers_user_id → ix_papers_added_by
    op.drop_index("ix_papers_user_id", table_name="papers")
    op.create_index("ix_papers_added_by", "papers", ["added_by"])
    # 2) arxiv_id 唯一性表达统一：约束 + 非唯一索引 → 唯一索引
    op.drop_constraint("uq_papers_arxiv", "papers", type_="unique")
    op.drop_index("ix_papers_arxiv_id", table_name="papers")
    op.create_index("ix_papers_arxiv_id", "papers", ["arxiv_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_papers_arxiv_id", table_name="papers")
    op.create_index("ix_papers_arxiv_id", "papers", ["arxiv_id"], unique=False)
    op.create_unique_constraint("uq_papers_arxiv", "papers", ["arxiv_id"])
    op.drop_index("ix_papers_added_by", table_name="papers")
    op.create_index("ix_papers_user_id", "papers", ["added_by"])
