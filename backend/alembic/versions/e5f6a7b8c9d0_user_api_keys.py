"""批①：users 表加"自带 API Key"两列（Fernet 密文，实现见 services/user_keys.py）

add column 即可（NULL=未配置）。
语义：配置后该用户的 LLM/嵌入调用优先用它自己的 key；
ALLOW_DEFAULT_KEY=false（默认/部署态）时，未配置的用户会被 409 拒绝。

downgrade 删两列——key 属于个人配置，无回填价值，丢失可重填。
"""
import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("llm_api_key", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("embedding_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "embedding_api_key")
    op.drop_column("users", "llm_api_key")
