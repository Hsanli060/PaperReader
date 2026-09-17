"""
ORM 表定义
表关系（批②"用户库归属"改造后）：
    User 1 ──── n Conversation   一个用户有多个会话（会话历史仍按用户隔离）
    Paper：全局只存一份（一行 + 一份向量；added_by 记录谁先添加的）
    User n ── m Paper            归属关系（user_papers）：谁把这篇加进了自己的库，谁可见；
                                 重复添加 = 只加一行归属，不重跑下载/向量化
    Conversation n ── m Paper    会话的问答范围（scope）由 conversation_papers 连接表圈定
    Conversation 1 ── n Message  一个会话包含多条消息
"""

from datetime import datetime
from sqlalchemy import String,Text,Integer,ForeignKey,DateTime,UniqueConstraint,Table,Column,func,Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Base 定义在 database.py（基建层），这里只做"用基建的人"，避免出现两份 Base
from app.models.database import Base


class User(Base):
    __tablename__ = "users"

    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    username:Mapped[str]=mapped_column(String(50),unique=True,nullable=False)
    password_hash:Mapped[str]=mapped_column(String(255),nullable=False)
    # 用户自带 API Key：Fernet 密文存储、永不落明文（实现见 services/user_keys.py）
    llm_api_key:Mapped[str|None]=mapped_column(Text)
    embedding_api_key:Mapped[str|None]=mapped_column(Text)
    # 自带服务地址（OpenAI 兼容端点，非机密、明文存储）：留空=用服务器默认
    llm_base_url:Mapped[str|None]=mapped_column(Text)
    embedding_base_url:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())

    conversations:Mapped[list["Conversation"]]=relationship(back_populates="user")


# 用户 × 论文 多对多连接表：论文的"归属关系"（批②：谁把这篇加进了自己的库）
# 全局仍只存一份论文一份向量；可见性（列表/scope/Agent 检索/删除）全按成员过滤。
user_papers = Table(
    "user_papers",
    Base.metadata,
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("paper_id", ForeignKey("papers.id"), primary_key=True),
    Column("added_at", DateTime, server_default=func.now()),
)


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    arxiv_id:Mapped[str | None]=mapped_column(String(64),index=True,unique=True)
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # 署名：谁添加的
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[str] = mapped_column(Text)  # 存 JSON 字符串，如 '["Alice","Bob"]'
    abstract: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(512))  # PDF 落盘位置
    #一篇论文的生命周期是pending（刚登记）→ downloaded（PDF 下载好）→ parsed（解析成文本）→ indexed（切好块、进了向量库）
    status: Mapped[str] = mapped_column(String(20), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)  # 流水线失败原因（前端徽章显示用）
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())

    added_by_user: Mapped["User"] = relationship(foreign_keys=[added_by])


# 会话 × 论文 多对多连接表：会话的问答范围（scope）
# 标准连接表解法（比 JSON 列正规：有外键约束、可 join、可索引）
conversation_papers = Table(
    "conversation_papers",
    Base.metadata,
    Column("conversation_id", ForeignKey("conversations.id"), primary_key=True),
    Column("paper_id", ForeignKey("papers.id"), primary_key=True),
)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")
    # 会话的问答范围：空列表 = 全库检索
    papers: Mapped[list["Paper"]] = relationship(secondary=conversation_papers, lazy="selectin")

class Message(Base):
    __tablename__ = "messages"
    # 复合索引：会话详情/聊天回放的查询形态是"WHERE conversation_id=? ORDER BY id"，
    # (conversation_id, id) 让过滤+排序一条索引全包（消掉 Sort 节点）；
    # 原单列索引是复合索引的前缀、被完全覆盖，由 migration 删除
    __table_args__ = (Index("ix_messages_conversation_id_id", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(String(20))  # "user" / "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
