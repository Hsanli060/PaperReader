"""批次②迁移：messages 复合索引 (conversation_id, id)

背景：会话详情/聊天回放的标准查询是 "WHERE conversation_id=? ORDER BY id"。
    单列 ix_messages_conversation_id 只能吃过滤，排序要额外 Sort；
    换成 (conversation_id, id) 复合索引后，过滤+排序一条索引全包。
    单列索引是复合索引的前缀、被完全覆盖 → 删除。

影响：纯索引变更，不动数据；downgrade 对称还原为单列索引。
"""
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_id_id", table_name="messages")
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
