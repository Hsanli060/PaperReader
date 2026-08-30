"""
Agent 层测试：dispatch 错误分支 / 工具真实执行 / ChatMemory 滑动窗口
策略：dispatch 和 memory 是纯本地逻辑不调 LLM，快且稳；
     工具执行走真实 RAG（和 test_rag.py 同一前提：库里已有 paper_id=1）
运行：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_agent.py -v
"""
import json
import pytest
from app.agents.tools import dispatch, TOOLS_IMPLS,TOOLS_SCHEMA
from app.agents.memory import ChatMemory

# ---------- dispatch：错误分支（不调 LLM） ----------
def test_dispatch_unknown_tool_returns_error_text():
    """调不存在的工具：返回错误说明文字而不是抛异常（模型下轮能自我修正的前提）"""
    r=dispatch("no_such_tool","{}")
    assert r.startswith("错误"), f"应返回错误说明，实际: {r[:100]}"
    assert "search_paper" in r  # 错误消息里应列出可用工具

def test_dispatch_bad_json_returns_error_text():
    """坏 JSON 参数：不炸，返回错误说明"""
    r=dispatch("search_paper",'{"query": bad json}')
    assert r.startswith("工具执行失败"), f"应返回错误说明，实际: {r[:100]}"

# ---------- 工具真实执行（走真 RAG，不调 LLM） ----------
def test_search_paper_tool_returns_hits():
    """search_paper 工具：真实检索，字段齐全、distance 保留 3 位"""
    r=dispatch("search_paper",'{"query": "selective state space"}')
    hits=json.loads(r)
    assert len(hits)==5
    for h in hits:
        assert {"paper_id", "section", "distance", "text"} <= set(h)  #确保这4个key h都有
        assert h["distance"] == round(h["distance"], 3)  # 是否已经按 3 位小数四舍五入处理过


def test_all_tools_registered():
    """Schema 和实现表必须一一对应"""
    schema_names={t["function"]["name"] for t in TOOLS_SCHEMA}
    assert schema_names==set(TOOLS_IMPLS),\
        f"工具与工具列表有出入：{schema_names} vs {set(TOOLS_IMPLS)}"

# ---------- ChatMemory：滑动窗口（纯本地） ----------
def test_memory_sliding_window():
    """容量 4 塞 6 条：最老的 2 条被挤出，只剩最近 4 条"""
    m = ChatMemory(max_messages=4)
    for i in range(6):
        m.add("user", f"问题{i}")
    h = m.history()
    assert [x["content"] for x in h] == ["问题2", "问题3", "问题4", "问题5"]

def test_memory_rejects_tool_role():
    """工具中间消息不许进记忆（草稿纸不进记忆本的约定）"""
    m=ChatMemory()
    with pytest.raises(ValueError):
        m.add("tool","{'query':'…' } 的执行结果")

def test_history_returns_copy():
    """history 返回拷贝：外部改了列表也影响不到窗口本身"""
    m=ChatMemory()
    m.add("user","你好")
    h=m.history()
    h.append({"role":"user","content":"篡改"})
    assert len(m.history())==1