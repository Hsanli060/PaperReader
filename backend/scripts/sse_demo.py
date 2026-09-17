# -*- coding: utf-8 -*-
"""
SSE 可靠性演示（心跳保活 + 断连感知）——一条命令跑完，输出实测数字。

背景（为什么要做这两件事）：
    SSE 是长连接：一次问答开几十秒，链路 = 浏览器 ← nginx/反代 ← FastAPI ← LLM。
      ① 空闲掐线：反代按"多久没数据"判死连接（nginx 默认 60s）——LLM 思考期
         可能长时间无输出 → 长连接被拦腰掐断。对策：周期心跳（": ping" 注释帧）。
      ② 白烧 token：用户关页面/点"停止生成"后，浏览器断开，但服务端 Agent 还在跑
         → 继续烧 LLM token。对策：断开感知（任务组取消 + 已生成部分落库）。

本脚本干什么（线程内起真 uvicorn 全栈：真中间件/auth/DB/sse_starlette）：
    幕1 心跳：连接后按 SSE_PING_INTERVAL 周期收 ": ping" 帧，实测到达间隔；
    幕2 断连：生成进行中硬断开连接，实测 断开→Agent 取消、断开→部分回答落库 的延迟；
    幕3 对账：核对"落库内容 = 断开前客户端已见内容（前缀）"、落库行数（无重复）、
             服务端出现断连日志；最后清理演示数据。

用法（在 backend 目录下执行）：
    ../.venv/Scripts/python.exe scripts/sse_demo.py           # 默认：假流（不调 LLM，秒级，可复跑）
    ../.venv/Scripts/python.exe scripts/sse_demo.py --real    # 真 LLM 端到端（会真实调用一次模型）
    ../.venv/Scripts/python.exe scripts/sse_demo.py --ping-interval 3 --question "..."
前提：本地 PG 已启动；脚本自选随机空闲端口起服务，不会和本机其它 uvicorn 冲突。
"""
import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

# 本地演示，不走系统代理（requests 会读这些环境变量）
for _k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests                              # noqa: E402
import uvicorn                               # noqa: E402
from loguru import logger as loguru_logger   # noqa: E402
from sqlalchemy import delete, select        # noqa: E402

from app.agents.react_agent import ReactAgent      # noqa: E402
from app.api.main import app                       # noqa: E402
from app.config import settings                    # noqa: E402
from app.models.database import SessionLocal       # noqa: E402
from app.models.orm import Conversation, Message   # noqa: E402
from app.security import create_access_token       # noqa: E402

QUESTION = "请用中文分 5 个部分详细讲解 Transformer 的自注意力机制，每部分至少两句话。"
HEADERS = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}

MARK = {}   # 服务端视角时间戳（取消到达 / 自然结束）
LOGS = []   # 收集 loguru 输出（用于抓"客户端断连"日志行）


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wrap_with_recorder(gen_func):
    """包一层生成器：不改行为，只记录"取消到达 Agent 层 / 自然结束"的时刻"""
    async def wrapped(self, *args, **kwargs):
        try:
            async for ev in gen_func(self, *args, **kwargs):
                yield ev
            MARK["finished_at"] = time.time()
        except (asyncio.CancelledError, GeneratorExit):
            MARK["cancel_at"] = time.time()
            raise
    return wrapped


async def _fake_gen(self, user_input, allowed_paper_ids=None):
    """假流：模拟"LLM 持续输出"，吐 6 段后挂住等断连（默认模式用，不调模型）"""
    for i in range(1, 7):
        yield {"type": "content", "text": f"（演示假流）第 {i} 段模型输出。"}
        await asyncio.sleep(0.15)
    await asyncio.sleep(60)


def _messages_of(conv_id: int):
    db = SessionLocal()
    try:
        return list(db.scalars(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        ))
    finally:
        db.close()


def _wait_messages(conv_id: int, expect: int, timeout: float = 8.0):
    deadline = time.time() + timeout
    rows = []
    while time.time() < deadline:
        rows = _messages_of(conv_id)
        if len(rows) >= expect:
            break
        time.sleep(0.05)
    return rows


def _cleanup(conv_id: int):
    db = SessionLocal()
    try:
        db.execute(delete(Message).where(Message.conversation_id == conv_id))
        conv = db.get(Conversation, conv_id)
        if conv is not None:
            db.delete(conv)
        db.commit()
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="SSE 心跳保活 + 断连感知 实测演示")
    ap.add_argument("--real", action="store_true", help="用真 LLM 跑（默认用假流，不调模型）")
    ap.add_argument("--ping-interval", type=int, default=2, help="心跳间隔秒（默认 2 便于观察；生产默认 15）")
    ap.add_argument("--question", default=QUESTION, help="提问内容（--real 时生效）")
    args = ap.parse_args()

    # ---- 配置与打点（进程内直改，演示用）----
    settings.SSE_PING_INTERVAL = args.ping_interval
    if args.real:
        ReactAgent.run_stream_async = _wrap_with_recorder(ReactAgent.run_stream_async)
        mode = "真 LLM 端到端"
    else:
        ReactAgent.run_stream_async = _wrap_with_recorder(_fake_gen)
        mode = "假流（隔离 SSE 层行为，不调 LLM）"
    loguru_logger.add(lambda m: LOGS.append(str(m)), level="INFO", format="{message}")

    # ---- 起服务 ----
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    sess = requests.Session()
    sess.trust_env = False
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if sess.get(f"{base}/api/health", timeout=1).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.15)
    else:
        print("服务未就绪，退出", flush=True)
        return 2

    print(f"\n== SSE 可靠性演示 | 模式：{mode} | 心跳间隔 {args.ping_interval}s | :{port} ==", flush=True)
    print("\n[幕1] 发起流式问答，边收边观察心跳……", flush=True)

    r = sess.post(f"{base}/api/chat", json={"question": args.question}, headers=HEADERS,
                  stream=True, timeout=60)
    if r.status_code != 200:
        print(f"  chat 请求失败：HTTP {r.status_code} {r.text[:200]}", flush=True)
        return 2
    conv_id = int(r.headers["X-Conversation-Id"])

    t_start = time.time()
    pings, client_text = [], ""
    first_content_at = None
    read_status = "ended"      # ended / disconnect / timeout / error:xxx

    try:
        for line in r.iter_lines(chunk_size=1, decode_unicode=True):
            now = time.time()
            if line.startswith(": ping"):
                pings.append(now - t_start)
                gap = f"，距上一帧 {pings[-1] - pings[-2]:.2f}s" if len(pings) > 1 else ""
                print(f"  心跳 ping 到达 @ {pings[-1]:5.2f}s{gap}", flush=True)
            elif line.startswith("data:"):
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                if ev.get("type") == "content":
                    if first_content_at is None:
                        first_content_at = now - t_start
                    client_text += ev["text"]
                elif ev.get("type") == "error":
                    print(f"  流内错误事件：{ev.get('message')}", flush=True)
            # 断开时机：心跳已证明（≥2 帧）且已有内容在手 → 模拟"用户中途关页"
            if len(pings) >= 2 and first_content_at is not None:
                read_status = "disconnect"
                break
            if now - t_start > 30:
                read_status = "timeout"
                break
    except Exception as e:
        read_status = f"error: {type(e).__name__}"
        print(f"  读取流异常：{read_status}: {e}", flush=True)

    result_ok = False
    try:
        if read_status == "disconnect":
            t_close = time.time()
            r.close()   # 硬断开（不发任何"结束"语义）
            print(f"\n[幕2] 生成进行中硬断开（客户端已见 {len(client_text)} 字符）……", flush=True)

            rows = _wait_messages(conv_id, expect=2)
            t_db = time.time()
            dt_cancel = max(0.0, (MARK.get("cancel_at", 0) - t_close) * 1000) if MARK.get("cancel_at") else None
            dt_db = max(0.0, (t_db - t_close) * 1000)
            partial = rows[-1].content if rows else ""

            time.sleep(0.6)   # 再等一拍：确认无"二次补齐"式重复落库
            rows_after = _messages_of(conv_id)

            print("\n[幕3] 实测对账：", flush=True)
            if len(pings) >= 2:
                gaps = [round(pings[i + 1] - pings[i], 2) for i in range(len(pings) - 1)]
                print(f"  ① 心跳间隔（配置 {args.ping_interval}s）：{gaps} s", flush=True)
            else:
                print(f"  ① 心跳：仅捕捉到 {len(pings)} 帧", flush=True)
            if dt_cancel is not None:
                print(f"  ② 断开 → Agent 生成器取消：{dt_cancel:.0f} ms", flush=True)
            else:
                print("  ② 断开 → Agent 生成器取消：未捕捉到取消时刻", flush=True)
            print(f"  ③ 断开 → 部分回答落库完成：{dt_db:.0f} ms（轮询粒度 ~50ms）", flush=True)
            print(f"  ④ 部分回答 {len(partial)} 字符；与断开前客户端所见一致（前缀）：{partial.startswith(client_text)}", flush=True)
            print(f"  ⑤ 落库行数：{len(rows_after)}（应为 2：user + assistant 部分回答，无重复）", flush=True)
            hit = next((s for s in LOGS if "客户端断连" in s), None)
            print(f"  ⑥ 服务端断连日志：{hit if hit else '（未捕捉到）'}", flush=True)
            result_ok = (len(rows_after) == 2 and partial.startswith(client_text)
                         and MARK.get("cancel_at") is not None)
        else:
            print(f"\n[幕2] 流在断连条件达成前已结束（status={read_status}，"
                  f"心跳 {len(pings)} 帧，内容 {len(client_text)} 字符）", flush=True)
            rows = _wait_messages(conv_id, expect=1, timeout=5)
            if rows:
                print(f"  自然完成：落库 {len(rows)} 行，回答 {len(rows[-1].content)} 字符", flush=True)
    finally:
        r.close()
        _cleanup(conv_id)

    print(f"\n[收尾] 演示数据已清理（conversation_id={conv_id}）", flush=True)
    return 0 if result_ok else 1


if __name__ == "__main__":
    sys.exit(main())
