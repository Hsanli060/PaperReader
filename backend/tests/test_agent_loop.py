"""
Agent 循环进阶测试（纯本地：fake LLM 流 + fake dispatch，不联网不调模型）

覆盖：并行工具调用（并发重叠、保序回填、事件顺序）+ 任务清单
      （todo_write 真实 roundtrip、3 轮提醒计数器）

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -v
"""
import asyncio
import time
from types import SimpleNamespace

import app.agents.react_agent as ra
from app.agents.react_agent import ReactAgent


def _chunk_text(text: str):
    """一只只带 content 的流式 chunk"""
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text, tool_calls=None))])


def _chunk_tool(index: int, call_id: str, name: str, args: str = "{}"):
    """一只带 tool_calls 的流式 chunk（index/id/name/args 齐备）"""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(index=index, id=call_id,
                                    function=SimpleNamespace(name=name, arguments=args))]))])


def _install_fake_streams(monkeypatch, rounds: list[list], captured: list) -> None:
    """把 _create_stream 换成按轮播放的假流；captured 收每轮拿到的 messages 快照"""
    state = {"i": 0}

    async def fake_create(messages):
        captured.append([dict(m) for m in messages])
        chunks = rounds[state["i"]]
        state["i"] += 1

        async def gen():
            for ch in chunks:
                yield ch

        return gen()

    monkeypatch.setattr(ReactAgent, "_create_stream", staticmethod(fake_create))


def _collect(agen) -> list[dict]:
    async def _run():
        return [ev async for ev in agen]
    return asyncio.run(_run())


def test_parallel_tools_overlap_and_ordered_backfill(monkeypatch):
    """一轮两工具：并发执行（时间重叠）+ 结果按申请顺序回填（先完成的不插队）"""
    spans = {}

    async def fake_dispatch(name, arguments, allowed_paper_ids=None, todo_state=None):
        t0 = time.perf_counter()
        await asyncio.sleep(0.3 if name == "search_paper" else 0.08)
        spans[name] = (t0, time.perf_counter())
        return f"result-of-{name}"

    monkeypatch.setattr(ra, "dispatch", fake_dispatch)

    rounds = [
        [_chunk_tool(0, "c1", "search_paper", '{"query":"a"}'),
         _chunk_tool(1, "c2", "web_search", '{"query":"b"}')],   # 第 1 轮：申请两个工具
        [_chunk_text("最终回答")],                                 # 第 2 轮：收尾
    ]
    captured = []
    _install_fake_streams(monkeypatch, rounds, captured)

    agent = ReactAgent()
    events = _collect(agent.run_stream_async("q", allowed_paper_ids=[1]))

    # ① 并发重叠：快的工具在慢的工具结束之前就已开始（串行绝不可能）
    assert spans["web_search"][0] < spans["search_paper"][1]
    assert spans["web_search"][1] < spans["search_paper"][1]
    # ② 结果按申请顺序回填：c1 慢但排前、c2 快但排后
    tool_msgs = [m for m in captured[1] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert tool_msgs[0]["content"] == "result-of-search_paper"
    assert tool_msgs[1]["content"] == "result-of-web_search"
    # ③ 事件顺序：先广播全部申请，再逐个给结果
    types = [e["type"] for e in events]
    assert types[:2] == ["tool_call", "tool_call"]
    assert types[2:4] == ["tool_result", "tool_result"]
    assert types[4] == "content"
    assert [e["name"] for e in events[2:4]] == ["search_paper", "web_search"]
    # ④ 埋点：批次记录（2 个工具）
    assert agent.stats["tool_batches"][0]["count"] == 2


def test_todo_write_roundtrip(monkeypatch):
    """todo_write 走真实 dispatch：清单渲染文本透传进消息 + todo 事件 + 埋点"""
    rounds = [
        # 第 1 轮：模型列计划（不 mock dispatch——真实项目里这个工具靠注入工作，要一起验）
        [_chunk_tool(0, "t1", "todo_write", '{"todos": ['
                     '{"content": "检索 Mamba", "status": "in_progress"}, '
                     '{"content": "对比总结", "status": "pending"}]}')],
        # 第 2 轮：收尾
        [_chunk_text("按计划完成")],
    ]
    captured = []
    _install_fake_streams(monkeypatch, rounds, captured)

    agent = ReactAgent()
    events = _collect(agent.run_stream_async("对比两篇论文", allowed_paper_ids=[1]))

    # ① 清单渲染文本（字符串透传）直接进 tool 消息，没被再包一层 JSON
    tool_msgs = [m for m in captured[1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"].startswith("[>] 检索 Mamba")
    assert "[ ] 对比总结" in tool_msgs[0]["content"]
    assert "已完成 0/2" in tool_msgs[0]["content"]
    # ② 事件序列：申请 → 结果 → todo 直播 → 回答
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "todo", "content"]
    assert events[2]["todos"][0] == {"content": "检索 Mamba", "status": "in_progress"}
    # ③ 埋点 + 提醒计数归零（用过清单的轮不提醒）
    assert len(agent.stats["todos"]) == 1
    assert agent.stats["todo_reminders"] == 0


def test_todo_reminder_counter(monkeypatch):
    """连续 3 轮工具调用没碰清单 → 第 3 轮结果尾附提醒；未满 3 轮不提醒"""
    async def fake_dispatch(name, arguments, allowed_paper_ids=None, todo_state=None):
        return "ok"

    monkeypatch.setattr(ra, "dispatch", fake_dispatch)

    rounds = [
        [_chunk_tool(0, "c1", "web_search", '{"query":"a"}')],
        [_chunk_tool(0, "c2", "web_search", '{"query":"b"}')],
        [_chunk_tool(0, "c3", "web_search", '{"query":"c"}')],
        [_chunk_text("回答")],
    ]
    captured = []
    _install_fake_streams(monkeypatch, rounds, captured)

    agent = ReactAgent()
    _collect(agent.run_stream_async("q", allowed_paper_ids=[1]))

    def _tools(idx):
        return [m["content"] for m in captured[idx] if m.get("role") == "tool"]

    assert "<reminder>" not in _tools(2)[-1]     # 第 2 轮后：才累计到 2，不提醒
    assert "<reminder>" in _tools(3)[-1]         # 第 3 轮后：触发提醒（附在最后一条结果尾部）
    assert agent.stats["todo_reminders"] == 1
