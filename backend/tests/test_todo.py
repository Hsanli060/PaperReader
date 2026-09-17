"""
任务清单（TodoWrite 机制）单测：纯本地逻辑，不联网不调模型。

覆盖：更新/渲染、整体替换语义、字符串输入解析（JSON / Python 字面量）、
      校验规则（≤20 项 / 单 in_progress / 状态枚举 / 非空 content）、快照拷贝。

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_todo.py -v
"""
import pytest

from app.agents.todo import TodoManager


def _item(content: str, status: str = "pending") -> dict:
    return {"content": content, "status": status}


def test_update_and_render():
    """更新+渲染：三种状态标记、完成计数"""
    tm = TodoManager()
    out = tm.update([_item("检索 Mamba", "in_progress"), _item("检索 Transformer"), _item("对比总结")])
    assert "[>] 检索 Mamba" in out
    assert "[ ] 检索 Transformer" in out
    assert "已完成 0/3" in out
    assert tm.snapshot()[0] == {"content": "检索 Mamba", "status": "in_progress"}


def test_update_replaces_not_merges():
    """更新是整体替换（不是增量合并）：第二次提交只剩新清单"""
    tm = TodoManager()
    tm.update([_item("a"), _item("b")])
    tm.update([_item("a", "completed")])
    assert tm.snapshot() == [{"content": "a", "status": "completed"}]


def test_string_input_accepted():
    """字符串输入：JSON 数组 / Python 列表字面量都能解析（不用 eval）"""
    out = TodoManager().update('[{"content": "步骤一", "status": "pending"}]')
    assert "[ ] 步骤一" in out
    out2 = TodoManager().update("[{'content': '步骤二', 'status': 'in_progress'}]")
    assert "[>] 步骤二" in out2


def test_empty_render():
    """空清单渲染"""
    assert TodoManager().render() == "（任务清单为空）"


def test_validation_errors():
    """校验规则：空内容 / 双 in_progress / 非法状态 / 超 20 项 / 解析失败——且失败不动旧清单"""
    tm = TodoManager()
    tm.update([_item("保留项")])                                    # 先放一份合法清单
    with pytest.raises(ValueError):
        tm.update([_item("")])                                      # 空 content
    with pytest.raises(ValueError):
        tm.update([_item("a", "in_progress"), _item("b", "in_progress")])  # 两个 in_progress
    with pytest.raises(ValueError):
        tm.update([_item("a", "done")])                             # 非法状态
    with pytest.raises(ValueError):
        tm.update([_item(f"步骤{i}") for i in range(21)])           # 超 20 项
    with pytest.raises(ValueError):
        tm.update("不是列表")                                       # 解析失败
    assert tm.snapshot() == [{"content": "保留项", "status": "pending"}]  # 校验失败不动旧清单


def test_snapshot_is_copy():
    """snapshot 返回拷贝：外部篡改不了内部状态"""
    tm = TodoManager()
    tm.update([_item("a")])
    snap = tm.snapshot()
    snap[0]["content"] = "被篡改"
    assert tm.snapshot()[0]["content"] == "a"
