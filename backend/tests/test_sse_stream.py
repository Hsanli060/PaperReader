# -*- coding: utf-8 -*-
"""
SSE 流式可靠性回归测试（心跳保活 + 断连感知，本轮新增功能）。

覆盖：
    - 心跳：配置间隔调小后，空闲流上持续收到 ": ping" 注释帧（端到端真栈实测间隔）
    - 断连：客户端硬断开 → 生成器被取消、部分回答确定性落库（shield 保证）且只写一次
    - 断连日志：服务端输出"客户端断连：...部分回答已落库"（观测性）
    - 正常路径：完整回答落库一次（不留回归）
    - 空断连：断开时还没生成任何内容 → 不落库（防幽灵空泡）

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_sse_stream.py -v
前提：本地 PG 已启动；不调 LLM（用假流替换 ReactAgent.run_stream_async）。
说明：TestClient 对 SSE 是整体缓冲的（探针实测读不到增量、也模拟不了断连），
      所以这里用线程内真 uvicorn + requests 直连做端到端验证。
"""
import asyncio
import json
import socket
import threading
import time

import pytest
import requests
import uvicorn
from loguru import logger as loguru_logger
from sqlalchemy import delete, select

from app.agents.react_agent import ReactAgent
from app.api.main import app
from app.config import settings
from app.models.database import SessionLocal
from app.models.orm import Conversation, Message
from app.security import create_access_token

HEADERS = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}


# ---------------- 基础设施 ----------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_server():
    """线程内起真 uvicorn（同生产 app/中间件），yield base_url"""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    probe = requests.Session()
    probe.trust_env = False
    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        try:
            if probe.get(f"{base}/api/health", timeout=1).status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.15)
    assert ready, "uvicorn 未在 20s 内就绪"

    yield base

    server.should_exit = True


@pytest.fixture(scope="module")
def http():
    """本地 HTTP 会话（trust_env=False：不走系统代理）"""
    sess = requests.Session()
    sess.trust_env = False
    yield sess
    sess.close()


def _fake_agent(monkeypatch, events, hang_at_end=False):
    """替换 ReactAgent.run_stream_async 为假流：逐个吐事件；
    hang_at_end=True 时吐完挂住（等客户端断连）"""
    async def fake_run_stream_async(self, user_input, allowed_paper_ids=None):
        for ev in events:
            yield ev
            await asyncio.sleep(0.12)
        if hang_at_end:
            await asyncio.sleep(60)
    monkeypatch.setattr(ReactAgent, "run_stream_async", fake_run_stream_async)


def _open_chat(sess, base, question, timeout=30):
    """POST /api/chat（stream=True）→ (response, conversation_id)"""
    r = sess.post(f"{base}/api/chat", json={"question": question},
                  headers=HEADERS, stream=True, timeout=timeout)
    assert r.status_code == 200, f"chat 请求失败：HTTP {r.status_code}"
    conv_id_raw = r.headers.get("X-Conversation-Id")
    assert conv_id_raw, "响应缺少 X-Conversation-Id 头"
    return r, int(conv_id_raw)


def _messages_of(conv_id):
    db = SessionLocal()
    try:
        return list(db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ))
    finally:
        db.close()


def _wait_messages(conv_id, expect, timeout=6.0):
    deadline = time.time() + timeout
    rows = []
    while time.time() < deadline:
        rows = _messages_of(conv_id)
        if len(rows) >= expect:
            break
        time.sleep(0.06)
    return rows


def _cleanup_conversation(conv_id):
    db = SessionLocal()
    try:
        db.execute(delete(Message).where(Message.conversation_id == conv_id))
        conv = db.get(Conversation, conv_id)
        if conv is not None:
            db.delete(conv)
        db.commit()
    finally:
        db.close()


# ---------------- 用例 ----------------

def test_ping_heartbeat_frames(live_server, http, monkeypatch):
    """心跳保活：间隔调到 1s——空闲流（无任何数据事件）上应持续收到 ': ping' 注释帧"""
    monkeypatch.setattr(settings, "SSE_PING_INTERVAL", 1)
    _fake_agent(monkeypatch, events=[], hang_at_end=True)   # 一个事件都不吐：流保持空闲

    r, conv_id = _open_chat(http, live_server, "心跳测试", timeout=10)
    pings = []
    t0 = time.monotonic()
    try:
        for line in r.iter_lines(chunk_size=1, decode_unicode=True):
            if line.startswith(": ping"):
                pings.append(time.monotonic() - t0)
                if len(pings) >= 3:
                    break
    finally:
        r.close()
        time.sleep(0.4)
        _cleanup_conversation(conv_id)

    assert len(pings) >= 2, f"空闲流未按间隔收到 ping 帧（收到 {pings}）"
    assert pings[0] <= 2.0, f"首个 ping 过慢：{pings[0]:.2f}s（配置间隔 1s）"
    gaps = [round(b - a, 2) for a, b in zip(pings, pings[1:])]
    assert all(0.5 <= g <= 2.0 for g in gaps), f"ping 间隔异常：{gaps}（配置间隔 1s）"
    print(f"\n[evidence] ping 到达时刻：{[round(p, 2) for p in pings]}s（配置间隔 1s）")


def test_disconnect_cancels_and_persists_partial(live_server, http, monkeypatch):
    """断连感知：生成中硬断开 → 部分回答落库（shield）且只写一次 + 断连日志"""
    _fake_agent(monkeypatch, events=[
        {"type": "content", "text": "第一段。"},
        {"type": "content", "text": "第二段。"},
    ], hang_at_end=True)

    log_messages = []
    sink_id = loguru_logger.add(log_messages.append, level="INFO", format="{message}")

    r, conv_id = _open_chat(http, live_server, "断连测试")
    try:
        seen = 0
        for line in r.iter_lines(chunk_size=1, decode_unicode=True):
            if line.startswith("data:"):
                ev = json.loads(line[5:].strip())
                if ev.get("type") == "content":
                    seen += 1
                    if seen >= 2:
                        break
        assert seen == 2, f"客户端只读到 {seen} 个内容事件"

        t_close = time.monotonic()
        r.close()   # 硬断开（不发任何"结束"语义）

        rows = _wait_messages(conv_id, expect=2)
        persist_ms = (time.monotonic() - t_close) * 1000

        time.sleep(0.8)   # 再等一拍：确认没有"二次补齐"式重复落库
        rows_after = _messages_of(conv_id)
    finally:
        r.close()
        loguru_logger.remove(sink_id)
        _cleanup_conversation(conv_id)

    assert len(rows) == 2, f"期望 2 行（user+assistant），实际 {len(rows)}：{[(m.role, m.content) for m in rows]}"
    assert rows[0].role == "user" and rows[0].content == "断连测试"
    assert rows[1].role == "assistant" and rows[1].content == "第一段。第二段。"
    assert len(rows_after) == 2, f"断连收尾疑似重复落库：{len(rows_after)} 行"
    assert any("客户端断连" in s for s in log_messages), \
        f"未捕捉到断连日志（最近日志：{log_messages[-5:]})"
    print(f"\n[evidence] 断开→部分回答落库完成：{persist_ms:.0f} ms（{len(rows[1].content)} 字符，1 次写入）")


def test_normal_completion_persists_once(live_server, http, monkeypatch):
    """正常完成：流自然结束 → 完整回答落库一次（主路径不回归）"""
    _fake_agent(monkeypatch, events=[{"type": "content", "text": "完整回答。"}], hang_at_end=False)

    r, conv_id = _open_chat(http, live_server, "正常测试")
    got = []
    try:
        for line in r.iter_lines(chunk_size=1, decode_unicode=True):
            if line.startswith("data:"):
                got.append(json.loads(line[5:].strip()))
        # iter_lines 读尽 = 服务端正常关闭响应（流自然结束）

        rows = _wait_messages(conv_id, expect=2)
        time.sleep(0.5)
        rows_after = _messages_of(conv_id)
    finally:
        r.close()
        _cleanup_conversation(conv_id)

    assert any(e.get("type") == "content" for e in got), f"未收到内容事件：{got}"
    assert len(rows) == 2, f"期望 2 行，实际 {len(rows)}"
    assert rows[1].role == "assistant" and rows[1].content == "完整回答。"
    assert len(rows_after) == 2, f"疑似重复落库：{len(rows_after)} 行"


def test_disconnect_without_content_no_ghost(live_server, http, monkeypatch):
    """断连且尚无内容：不落库（防幽灵空泡）"""
    _fake_agent(monkeypatch, events=[], hang_at_end=True)

    r, conv_id = _open_chat(http, live_server, "空断连测试")
    try:
        time.sleep(0.6)   # 连接已建立、请求已进生成器（一个事件都不吐）
        r.close()         # 硬断开
        time.sleep(1.0)   # 给服务端取消+收尾留时间
        rows = _messages_of(conv_id)
    finally:
        r.close()
        _cleanup_conversation(conv_id)

    assert rows == [], f"不应落库，实际 {[(m.role, m.content) for m in rows]}"
