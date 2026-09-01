"""FIX-1 验证脚本（一次性）：测 run_stream_async 的首字时延 + 事件节奏"""
import asyncio
import time

from app.agents.react_agent import ReactAgent


async def go():
    agent = ReactAgent(verbose=True)
    t0 = time.time()
    first = None          # 首个 content 事件到达时间（TTFT）
    first_tool = None     # 首个 tool_call 事件到达时间
    events = []
    async for ev in agent.run_stream_async("Mamba的选择性机制是什么"):
        ts = round(time.time() - t0, 2)
        if first is None and ev["type"] == "content":
            first = ts
        if first_tool is None and ev["type"] == "tool_call":
            first_tool = ts
        preview = (ev.get("text") or ev.get("name") or "")[:40]
        events.append((ts, ev["type"], preview))
    print("FIRST_TOOL_AT:", first_tool)
    print("FIRST_CONTENT_AT:", first, "(TTFT，旧版要等整段生成完才吐)")
    print("TOTAL_EVENTS:", len(events))
    print("--- 前 15 个事件 ---")
    for e in events[:15]:
        print(e)
    # 统计 content 事件的时间跨度：真流式应该分散在一段时间内，而非同一瞬间
    content_ts = [e[0] for e in events if e[1] == "content"]
    if content_ts:
        print(f"CONTENT_SPAN: {content_ts[0]}s ~ {content_ts[-1]}s（跨度 {(content_ts[-1]-content_ts[0]):.1f}s，>2s = 真流式）")

asyncio.run(go())
