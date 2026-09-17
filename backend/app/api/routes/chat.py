"""
论文问答路由：/api/chat（SSE 流式）+ /api/history（对话历史）
谁调用它：main.py 挂载

SSE 事件协议（前端唯一要认的格式）：
    每条 SSE 消息的 data 都是一行 JSON，形状 {"type": "...", ...}：
      {"type":"content",     "text": "..."}          回答文本片段（打字机）
      {"type":"tool_call",   "name":..., "arguments":"{...json}"}  模型申请调工具
      {"type":"tool_result", "name":..., "summary": "..."}     工具执行完成摘要
      {"type":"todo",         "todos":[{"content":"...","status":"..."}]}  任务清单更新（多步任务的计划/进度）
      {"type":"error",       "message":"..."}        生成中断/空回答（前端显示 ⚠️ 进气泡）
    自定义头 X-Conversation-Id：新会话时把后端分配的会话 ID 告诉前端

FIX-4 注：
    1. 路由 async def——事件循环托管，不占线程池；真正的慢活（LLM 流式等待）
       由 AsyncOpenAI 在事件循环上非阻塞等待
    2. 同步 DB 操作（SQLAlchemy Session）在 async 上下文里一律 asyncio.to_thread
       包住——同步 Session 直接在事件循环里跑会阻塞循环
    3. 客户端中途断连：sse_starlette 检测到 http.disconnect 后取消任务组，
       生成器收到 CancelledError/GeneratorExit；finally 里把"已生成部分"落库（防脏数据）。
       ⚠️ 取消态下 finally 里普通 await 会被立即再次打断（探针实测）——落库必须包
       anyio.CancelScope(shield=True) 才能确定写完，否则写没写上看竞跑
"""
import asyncio
import json

import anyio
from fastapi import APIRouter, Depends, Response
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select, func

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.middleware.error_handler import AppException
from app.models.orm import Paper, Conversation, Message, user_papers
from app.services.user_keys import ensure_keys, set_current_keys

router = APIRouter(tags=["问答"])

class ChatIn(BaseModel):
    """提问入参。
    conversation_id=None 表示"新会话"（后端建）；
    paper_ids=None 表示全库检索；传列表 = 会话 scope 圈定这几篇论文"""
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
        """会话就位（含 scope 写库）+ 历史灌入。返回 (conversation_id, history_rows, scope_ids, keys)"""
        # 批①前置：先解析"当前用户生效的 key"（严格模式没配 → 409；此时还没落任何数据）。
        #    SSE 一旦起流，HTTP 状态码就定型改不了了——必须赶在起流前拦
        keys = ensure_keys(db, user["user_id"], need_llm=True, need_embedding=True)

        # 0. scope 校验（批②）：传了的 paper_ids 必须都在"我的论文库"里
        if data.paper_ids:
            found = db.scalars(
                select(user_papers.c.paper_id)
                .where(user_papers.c.user_id == user["user_id"],
                       user_papers.c.paper_id.in_(data.paper_ids))
            ).all()
            missing = set(data.paper_ids) - set(found)
            if missing:
                raise AppException(f"论文不在你的论文库: {sorted(missing)}", 404)

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

        # 3. 会话 scope（批②）：以库里的关联为准，并与"我的论文库"求交集
        #    （会话可能建得早，期间我的库删过论文——已退会的论文绝不进检索范围）
        my_ids = set(db.scalars(
            select(user_papers.c.paper_id).where(user_papers.c.user_id == user["user_id"])
        ))
        if conversation.papers:
            scope_ids = [p.id for p in conversation.papers if p.id in my_ids]
        else:
            # "全库检索" = 我的整个论文库（批②：绝不再传 None——None 在工具/检索层
            # 是"不过滤"，会横扫全向量库=别人的论文）
            scope_ids = sorted(my_ids)
        return conversation.id, history_rows, scope_ids, keys

    conversation_id, history_rows, scope_ids, user_keys = await asyncio.to_thread(_prepare)
    # key 存进请求上下文（必须在起流前设置：streaming 子任务创建时会拷贝当前上下文，
    # agent / 工具 / 检索链路里 get_async_client() / embed() 不传参也能取到正确的 key）
    set_current_keys(user_keys)

    # X-Conversation-Id 必须在流开始前写进响应头——流开始后 header 就定型了。
    # 注意：EventSourceResponse 内部新建响应对象，headers= 显式传才生效
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
        cancelled = False   # 客户端断连标记（只用于收尾日志）

        def _persist(full_answer: str) -> None:
            """问答落库（user 问题 + assistant 回答各一行）"""
            db.add(Message(conversation_id=conversation_id, role="user", content=data.question))
            db.add(Message(conversation_id=conversation_id, role="assistant", content=full_answer))
            db.commit()

        async def _persist_shielded(full_answer: str) -> None:
            """shield 版落库：客户端断连时任务组被取消——探针实测取消态下 finally 里的
            裸 await 会被立即再次打断，写没写上看竞跑；shield 包住才能保证写完"""
            nonlocal persisted
            with anyio.CancelScope(shield=True):
                await asyncio.to_thread(_persist, full_answer)
                persisted = True

        try:
            # run_stream_async 的 delta.content 到手当场 yield，
            # scope 由服务端注入（allowed_paper_ids），LLM 的工具 schema 里看不到它
            async for event in agent.run_stream_async(data.question, allowed_paper_ids=scope_ids):
                if event["type"] == "content":
                    answer_parts.append(event["text"])
                # 每个事件转成一行 JSON 推给浏览器：SSE 约定 "data: <一行文本>\n\n"
                yield {"event": "message",
                       "data": json.dumps(event, ensure_ascii=False)}

            #    空回答不落库——否则历史里留下"幽灵空泡"，刷新永远回放空白
            full_answer = "".join(answer_parts)
            if full_answer.strip():
                await _persist_shielded(full_answer)
            else:
                # 空流（大概率 LLM API 抽风）：SSE 头早已 200，没法改状态码，
                # 只能在流里显式发 error 事件——不吭声的话前端就是个无声的空白气泡
                logger.warning(f"chat 空回答 conversation_id={conversation_id}")
                yield {"event": "message",
                       "data": json.dumps({"type": "error",
                                           "message": "模型没有返回内容（LLM 服务可能抖了一下），请重发一次"},
                                          ensure_ascii=False)}
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断连（点停止生成/关页面）或服务端停止：sse_starlette 取消任务组，
            # 取消异常打进生成器。标记后原样放行——落库收尾统一由 finally 兜底
            cancelled = True
            raise
        except Exception as e:
            # 流中途炸了（LLM 网络错误等）：同样只能在流里报错，让前端气泡显示 ⚠️
            logger.exception(f"chat 流中断 conversation_id={conversation_id}")
            try:
                yield {"event": "message",
                       "data": json.dumps({"type": "error",
                                           "message": f"生成中断：{e}"},
                                          ensure_ascii=False)}
            except Exception:
                pass  # 客户端已经断开，error 事件也发不出去，算了
        finally:
            # 客户端中途断连/异常收尾：把"已生成部分"落库，数据库无脏数据。
            # ⚠️ 探针实测：取消态下普通 await 会被立即再次打断——落库必须走
            #    _persist_shielded（shield 包住）才能确定写完，否则写没写上看竞跑
            if not persisted:
                partial = "".join(answer_parts)
                if partial.strip():   # 空串不落库（防幽灵空泡）
                    try:
                        await _persist_shielded(partial)
                        if cancelled:
                            logger.info(f"chat 客户端断连：生成已取消，部分回答已落库"
                                        f"（{len(partial)} 字符，conversation_id={conversation_id}）")
                    except Exception:
                        pass  # 断连收尾尽力而为，别再抛错打扰日志

    # ping=心跳保活：sse_starlette 每 N 秒发 ": ping" 注释帧（前端解析器自动跳过），
    # 防反代按"空闲"掐断长连接（nginx proxy_read_timeout 默认 60s，15s 心跳留足余量）
    return EventSourceResponse(agen(), headers={"X-Conversation-Id": conv_id_str},
                               sep="\n", ping=settings.SSE_PING_INTERVAL)


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
