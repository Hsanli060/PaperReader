"""
ORM 表定义
表关系：
    User 1 ──── n Paper          一个用户收藏多篇论文
    User 1 ──── n Conversation   一个用户有多个会话
    Conversation 1 ── n Message  一个会话包含多条消息
    Conversation.paper_id → Paper 会话可针对某篇论文（可为空=通用问答）
"""

from datetime import datetime
from sqlalchemy import String,Text,Integer,ForeignKey,DateTime,UniqueConstraint,func
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Base 定义在 database.py（基建层），这里只做"用基建的人"，避免出现两份 Base
from app.models.database import Base

class User(Base):
    __tablename__ = "users"

    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    username:Mapped[str]=mapped_column(String(50),unique=True,nullable=False)
    password_hash:Mapped[str]=mapped_column(String(255),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())

    # relationship：对象层面的"快捷通道"，不产生新列
    papers:Mapped[list["Paper"]]=relationship(back_populates="user")
    conversations:Mapped[list["Conversation"]]=relationship(back_populates="user")


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    arxiv_id:Mapped[str | None]=mapped_column(String(64),index=True)
    # 同一篇 arXiv 论文允许每个用户各收藏一份（用户隔离的库，去重只在"自己"范围内比较）
    __table_args__ = (
        UniqueConstraint("user_id","arxiv_id",name="uq_papers_user_arxiv"),
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[str] = mapped_column(Text)  # 存 JSON 字符串，如 '["Alice","Bob"]'
    abstract: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(512))  # PDF 落盘位置
    #一篇论文的生命周期是pending（刚登记）→ downloaded（PDF 下载好）→ parsed（解析成文本）→ indexed（切好块、进了向量库）
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="papers")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("papers.id"), index=True)  # 可为空=通用问答
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # "user" / "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")