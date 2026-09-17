"""批①补强：users 加"自带服务地址"两列（OpenAI 兼容端点）

不同用户的 key 可能来自不同运营商（DeepSeek / Moonshot / OpenAI / SiliconFlow /
自建中转……），服务地址自然也不同。两列可空：留空 = 用服务器 .env 的默认地址；
非机密信息，明文存储（与 Key 的密文列不同——URL 要能在设置页回显）。

downgrade 删两列。
"""
import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("llm_base_url", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("embedding_base_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "embedding_base_url")
    op.drop_column("users", "llm_base_url")
