"""
论文问答路由：/api/chat（SSE 流式）+ /api/history（对话历史）
谁调用它：main.py 挂载

SSE 事件协议（前端唯一要认的格式）：
    每条 SSE 消息的 data 都是一行 JSON，形状 {"type": "...", ...}：
      {"type":"content",     "text": "..."}          回答文本片段（打字机）
      {"type":"tool_call",   "name":..., "arguments":"{...json}"}  模型申请调工具
      {"type":"tool_result", "name":..., "summary": "..."}     工具执行完成摘要
    自定义头 X-Conversation-Id：新会话时把后端分配的会话 ID 告诉前端

FIX-4 注：
    1. 路由 async def——事件循环托管，不占线程池；真正的慢活（LLM 流式等待）
       由 AsyncOpenAI 在事件循环上非阻塞等待
    2. 同步 DB 操作（SQLAlchemy Session）在 async 上下文里一律 asyncio.to_thread
       包住——同步 Session 直接在事件循环里跑会阻塞循环
    3. 客户端中途断连：async 生成器被 close → GeneratorExit 会被 sse_starlette
       正常处理；finally 里把"已生成部分"落库，数据库无脏数据
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select, func

from app.api.deps import get_current_user, get_db
from app.middleware.error_handler import AppException
from app.models.orm import Paper, Conversation, Message

router = APIRouter(tags=["问答"])

class ChatIn(BaseModel):
    """提问入参（FIX-3' scope 版）。
    conversation_id=None 表示"新会话"（后端建）；
    paper_ids=None 表示全库检索；传列表 = 会话 scope 圈定这几篇论文（NotebookLM 式源选择）"""
    question: str = Field(min_length=1, max_length=2000)
    paper_ids: list[int] | None = None
    conversation_id: int | None = None


@router.post("/chat")
async def chat(
        data: ChatIn,
        response: Response,                          # FastAPI 自动注入：往响应里写自定义头
        user: dict = Depends(get_current_user),
        db=Depends(get_db),
):
    """SSE 流式问答（持久化 + 异步化版）。

    流程：会话就位（新建或续用，to_thread）→ 载入历史 → 查缓存 →
          命中：缓存流 + 问答落库
          未命中：agent.run_stream_async 流式回答 → 攒完整答案 → 落库 + 缓存
    """

    # ---- 同步 DB 前置段：整段包进 to_thread（一个函数一次切换，开销可忽略） ----
    def _prepare():
        """会话就位（含 scope 写库）+ 历史灌入。返回 (conversation_id, history_rows, scope_ids)"""
        # 0. scope 校验：传了的 paper_ids 必须都真实存在（全局库，无归属概念）
        if data.paper_ids:
            found = db.scalars(select(Paper.id).where(Paper.id.in_(data.paper_ids))).all()
            missing = set(data.paper_ids) - set(found)
            if missing:
                raise AppException(f"论文不存在: {sorted(missing)}", 404)

        # 1. 会话就位：新建 or 复用（都确认归属当前用户——会话历史仍是用户隔离的）
        if data.conversation_id is None:
            conversation = Conversation(
                user_id=user["user_id"],
                title=data.question[:20] + ("..." if len(data.question) > 20 else ""),
            )
            if data.paper_ids:
                scope_papers = db.scalars(
                    select(Paper).where(Paper.id.in_(data.paper_ids))
                ).all()
                conversation.papers = scope_papers       # 多对多：直接赋值关联
            db.add(conversation)
            db.commit()                    # commit 后 id 回填
        else:
            conversation = db.get(Conversation, data.conversation_id)
            # 不是自己的会话 = 不存在（安全思路：404 而非 403，防探测）
            if conversation is None or conversation.user_id != user["user_id"]:
                raise AppException("会话不存在", 404)

        # 2. 从 DB 载入这个会话的历史
        history_rows = db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id.asc())    # 按时间正序（老消息在前）
        ).all()

        # 3. 会话 scope：续用已有会话时以库里的关联为准（笔记本保留源）
        scope_ids = [p.id for p in conversation.papers] or None
        return conversation.id, history_rows, scope_ids

    conversation_id, history_rows, scope_ids = await asyncio.to_thread(_prepare)

    # X-Conversation-Id 必须在流开始前写进响应头——流开始后 header 就定型了。
    # 注意：EventSourceResponse 内部新建响应对象，headers= 显式传才生效（踩过的坑），
    # 这里 response 上写一份、EventSourceResponse 再传一份，双保险。
    conv_id_str = str(conversation_id)
    response.headers["X-Conversation-Id"] = conv_id_str

    # ---- Agent 就位：从 DB 历史灌进记忆（纯内存操作，快） ----
    from app.agents.react_agent import ReactAgent
    agent = ReactAgent()
    agent.load_history(
        {"role": m.role, "content": m.content} for m in history_rows
    )

    # ---- 真跑 Agent（真流式：delta 到手当场转发） ----
    # 注：对话 Prompt 级缓存已按设计决策移除——多轮语义依赖上下文键不可判定、
    #     命中回放丢失工具轨迹、真实命中率趋近 0；Redis 保留在 papers 的
    #     summary/citations 派生对象缓存（papers.py）
    async def agen():
        answer_parts = []
        persisted = False

        def _persist(full_answer: str) -> None:
            """问答落库（user 问题 + assistant 回答各一行）"""
            db.add(Message(conversation_id=conversation_id, role="user", content=data.question))
            db.add(Message(conversation_id=conversation_id, role="assistant", content=full_answer))
            db.commit()

        try:
            # FIX-1 的心脏：run_stream_async 的 delta.content 到手当场 yield，
            # 这里逐个转发给浏览器——打字机节奏来自模型本身，不再人工 sleep。
            # FIX-3'：scope 由服务端注入（allowed_paper_ids），LLM 的工具 schema 里看不到它
            async for event in agent.run_stream_async(data.question, allowed_paper_ids=scope_ids):
                if event["type"] == "content":
                    answer_parts.append(event["text"])
                # 每个事件转成一行 JSON 推给浏览器：SSE 约定 "data: <一行文本>\n\n"
                yield {"event": "message",
                       "data": json.dumps(event, ensure_ascii=False)}

            # 5. 流正常结束：落库（to_thread 包同步操作）
            full_answer = "".join(answer_parts)
            await asyncio.to_thread(_persist, full_answer)
            persisted = True
        finally:
            # 客户端中途断连（点停止生成/关页面）时，async 生成器被 close，
            # GeneratorExit 在这里被接住——把"已生成部分"落库，数据库无脏数据。
            # 缓存只在完整回答时写（断连的部分答案不值得缓存）
            if not persisted:
                partial = "".join(answer_parts)
                try:
                    await asyncio.to_thread(_persist, partial)
                except Exception:
                    pass  # 断连收尾尽力而为，别再抛错打扰日志

    return EventSourceResponse(agen(), headers={"X-Conversation-Id": conv_id_str}, sep="\n")


@router.get("/history")
async def list_conversations(
        user: dict = Depends(get_current_user),
        db=Depends(get_db),
) -> dict:
    """我的会话列表（新→旧）。每条带消息数，前端侧边栏用"""
    def _query():
        return db.execute(
            select(Conversation, func.count(Message.id))
            .outerjoin(Message, Message.conversation_id == Conversation.id)  # 左连接：0 条消息的会话也保留
            .where(Conversation.user_id == user["user_id"])
            .group_by(Conversation.id)
            .order_by(Conversation.id.desc())
        ).all()
    rows = await asyncio.to_thread(_query)
    return {
        "items": [
            {
                "id": c.id,
                "title": c.title,
                # FIX-3'：scope 以列表返回（多对多）；lazy="selectin" 已随对象加载
                "paper_ids": [p.id for p in c.papers],
                "message_count": cnt,
                "created_at": str(c.created_at),
            }
            for c, cnt in rows
        ]
    }

@router.get("/history/{conversation_id}")
async def get_history(
        conversation_id: int,
        user: dict = Depends(get_current_user),
        db=Depends(get_db),
) -> dict:
    def _query():
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user["user_id"]:
            raise AppException("会话不存在", 404)
        msgs = db.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
        ).all()
        return conversation, msgs
    conversation, msgs = await asyncio.to_thread(_query)
    return {
        "id": conversation.id,
        "title": conversation.title,
        # FIX-3'：详情返回 scope 列表（前端恢复"问答范围"勾选状态用）
        "paper_ids": [p.id for p in conversation.papers],
        "messages": [{"role": m.role, "content": m.content} for m in msgs],
    }

@router.delete("/history/{conversation_id}")
async def delete_history(
        conversation_id: int,
        user: dict = Depends(get_current_user),
        db=Depends(get_db),
) -> dict:
    """删除会话：先删子表 messages，再删父表 conversations（外键约束的方向）"""
    def _delete():
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user["user_id"]:
            raise AppException("会话不存在", 404)
        db.query(Message).filter(Message.conversation_id == conversation_id).delete()
        db.delete(conversation)
        db.commit()
        return conversation_id
    deleted = await asyncio.to_thread(_delete)
    return {"deleted": deleted}
