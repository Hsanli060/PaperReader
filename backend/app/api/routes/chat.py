"""
论文问答路由：/api/chat（SSE 流式）+ /api/history（对话历史，下半课写）
谁调用它：main.py 挂载

SSE 事件协议（前端唯一要认的格式）：
    每条 SSE 消息的 data 都是一行 JSON，形状 {"type": "...", ...}：
      {"type":"content",     "text": "..."}          回答文本片段（打字机）
      {"type":"tool_call",   "name":..., "arguments":"{...json}"}  模型申请调工具
      {"type":"tool_result", "name":..., "summary": "..."}     工具执行完成摘要
    自定义头 X-Conversation-Id：新会话时把后端分配的会话 ID 告诉前端
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Query,Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select, func

from app import agents
from app.api.deps import get_current_user, get_db
from app.middleware.error_handler import AppException
from app.models.orm import Paper, Conversation, Message
from app.services.cache import llm_cache

router=APIRouter(tags=["问答"])

class ChatIn(BaseModel):
    """提问入参。conversation_id=None 表示"新会话"（后端建），
    paper_id=None 表示通用问答（不限定某篇论文）"""
    question:str=Field(min_length=1,max_length=2000)
    paper_id:int|None=None
    conversation_id:int|None=None

@router.post("/chat")
def chat(
        data:ChatIn,
        response:Response,                          # FastAPI 自动注入：往响应里写自定义头
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
):
    """SSE 流式问答（持久化版）。

    流程：会话就位（新建或续用）→ 载入历史 → 查缓存 →
          命中：缓存流 + 问答落库
          未命中：agent 流式回答 → 攒完整答案 → 缓存 + 落库
    """
    # 0. 论文校验（不变）
    if data.paper_id is not None:
        paper=db.get(Paper,data.paper_id)
        if paper is None:
            raise AppException("论文不存在",404)

    # 1. 会话就位：新建 or 复用（都确认归属当前用户）
    if data.conversation_id is None:
        conversation=Conversation(
            user_id=user["user_id"],
            paper_id=data.paper_id,
            title=data.question[:20]+("..." if len(data.question)>20 else ""),
        )
        db.add(conversation)
        db.commit()                    # commit 后 id 回填
    else:
        conversation=db.get(Conversation,data.conversation_id)
        # 不是自己的会话 = 不存在（和 papers 的 _get_own_paper 同一个安全思路）
        if conversation is None or conversation.user_id!=user["user_id"]:
            raise AppException("会话不存在",404)

    # conversation_id 必须在流开始前写进响应头——流开始后 header 就定型了
    response.headers["X-Conversation-Id"]=str(conversation.id)

    # 2. 从 DB 载入这个会话的历史，灌进 Agent 记忆
    history_rows=db.scalars(
        select(Message)
        .where(Message.conversation_id==conversation.id)
        .order_by(Message.id.asc())    # 按时间正序（老消息在前）
    ).all()
    from app.agents.react_agent import ReactAgent
    agent=ReactAgent()
    agent._memory.load_history(     # 走公开方法灌历史（不直接捅私有 _msgs）
        {"role":m.role,"content":m.content} for m in history_rows
    )

    # 3. 缓存查询（不变）
    cached=llm_cache.get(data.paper_id,data.question)
    if cached:
        # 命中缓存也要落库——这段问答同样属于会话历史
        db.add(Message(conversation_id=conversation.id,role="user",content=data.question))
        db.add(Message(conversation_id=conversation.id,role="assistant",content=cached))
        db.commit()
        def cached_gen():
            for i in range(0,len(cached),24):
                # 缓存回放也走事件协议（type=content），前端只认一种格式。
                # 同 gen()：sse_starlette 收到 dict 会当 ServerSentEvent(**data) 解析，
                # 必须包成 {"event":"message","data":<json 字符串>} 的形状
                yield {"event":"message","data":json.dumps({"type":"content","text":cached[i:i+24]},ensure_ascii=False)}
        # 注意：自定义头必须通过 EventSourceResponse(headers=...) 传——
        # 它内部新建响应对象，路由参数 response 上写的头会被丢弃
        return EventSourceResponse(cached_gen(),headers={"X-Conversation-Id":str(conversation.id)})

    # 4. 未命中：真跑 Agent
    async def gen():
        answer_parts=[]
        for event in agent.run_stream(data.question):
            if event["type"]=="content":
                answer_parts.append(event["text"])
            # 每个事件转成一行 JSON 推给浏览器：SSE 的约定是
            # "data: <一行文本>\n\n"，一行文本 = json.dumps(事件字典)
            yield {"event":"message","data":json.dumps(event,ensure_ascii=False)}
            # 打字机节奏：24 字符的片段几乎同时到达，视觉上挤成一坨。
            # content 事件之间插 20ms 间隔，人眼才能看出"字在往外蹦"
            if event["type"]=="content":
                await asyncio.sleep(0.02)
        full_answer="".join(answer_parts)

        # 5. 流结束才执行到这：问答落库（user 问题 + assistant 回答各一行）
        db.add(Message(conversation_id=conversation.id,role="user",content=data.question))
        db.add(Message(conversation_id=conversation.id,role="assistant",content=full_answer))
        db.commit()
        llm_cache.set(data.paper_id,data.question,full_answer)

    # headers 显式传给 EventSourceResponse（往参数 response 写的头会被它内部新建的响应对象丢弃）
    return EventSourceResponse(gen(),headers={"X-Conversation-Id":str(conversation.id)})

@router.get("/history")
def list_conversations(
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """我的会话列表（新→旧）。每条带消息数，前端侧边栏用"""
    rows=db.execute(
        select(Conversation,func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)  # 左连接：0 条消息的会话也保留
        .where(Conversation.user_id == user["user_id"])
        .group_by(Conversation.id)
        .order_by(Conversation.id.desc())
    ).all()
    return{
        "items":[
            {
                "id": c.id,
                "title": c.title,
                "paper_id": c.paper_id,
                "message_count": cnt,
                "created_at": str(c.created_at),
            }
            for c,cnt in rows
        ]
    }

@router.get("/history/{conversation_id}")
def get_history(
        conversation_id: int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user["user_id"]:
        raise AppException("会话不存在", 404)
    msgs=db.scalars(
        select(Message).where(Message.conversation_id==conversation_id)
        .order_by(Message.id.asc())
    ).all()
    return{
        "id": conversation.id,
        "title": conversation.title,
        "paper_id": conversation.paper_id,
        "messages": [{"role": m.role, "content": m.content} for m in msgs],
    }

@router.delete("/history/{conversation_id}")
def delete_history(
        conversation_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """删除会话：先删子表 messages，再删父表 conversations（外键约束的方向）"""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user["user_id"]:
        raise AppException("会话不存在", 404)
    db.query(Message).filter(Message.conversation_id==conversation_id).delete()
    db.delete(conversation)
    db.commit()
    return{"deleted":conversation_id}