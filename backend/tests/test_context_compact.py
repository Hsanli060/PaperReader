"""
上下文压缩管线单测：纯函数，不联网不调模型，断言精确。

覆盖（对照 s08 借鉴清单）：
    - 新鲜度规则：未消费批永不压；已消费保留最近 K 条原文
    - 幂等守卫：重复跑不二次压缩；截断不重复
    - 协议完整性：只改 content，不删消息、不动 tool_call_id
    - 单条硬限 / 批量预算 / 反应式 emergency / 压缩收益报告

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_context_compact.py -v -s
"""
import json

from app.agents.context import COMPACT_MARK, ContextCompactor


def _search_result(paper_id: int = 3, blocks: int = 5, text_len: int = 600) -> str:
    """仿 search_paper 的真实产出（实测单条 ≈3.4K 字符）"""
    return json.dumps(
        [{"paper_id": paper_id, "section": f"Section {i}", "distance": 0.3,
          "rerank_score": 0.9, "text": "x" * text_len} for i in range(blocks)],
        ensure_ascii=False,
    )


def _assistant(call_id: str = "c1") -> dict:
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": "search_paper", "arguments": "{}"}}]}


def _tool(content: str, call_id: str = "c1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# ---------- 新鲜度规则 ----------

def test_under_limit_no_change():
    """体积没超软线：什么都不动"""
    comp = ContextCompactor(context_char_limit=100000, result_char_limit=6000, batch_char_limit=15000)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        _assistant(), _tool(_search_result()),
    ]
    before = json.dumps(msgs, ensure_ascii=False)
    info = comp.prepare(msgs)
    assert info["changed"] is False
    assert json.dumps(msgs, ensure_ascii=False) == before


def test_current_batch_never_aged():
    """未消费批（最后一条 assistant 之后）绝不被老化压缩"""
    comp = ContextCompactor(context_char_limit=500, keep_recent=1, min_compact_len=50)
    big = _search_result()
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(big, "c1"),      # 第 1 轮 → 已消费
        _assistant("c2"), _tool(big, "c2"),      # 第 2 轮 → 已消费
        _assistant("c3"), _tool(big, "c3"),      # 第 3 轮 = 当前批（未消费）
    ]
    comp.prepare(msgs)
    assert msgs[-1]["content"] == big                   # 当前批保持全文
    assert msgs[5]["content"] == big                    # keep_recent=1 保护最近一条已消费
    assert msgs[3]["content"].startswith(COMPACT_MARK)  # 更老的已消费 → 压成指针


def test_consumed_aged_keeps_recent():
    """已消费保留最近 K 条：keep_recent=2 时只压 3 条里最老的 1 条"""
    comp = ContextCompactor(context_char_limit=500, keep_recent=2, min_compact_len=50)
    big = _search_result()
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(big, "c1"),
        _assistant("c2"), _tool(big, "c2"),
        _assistant("c3"), _tool(big, "c3"),
        _assistant("c4"), _tool(big, "c4"),      # 当前批
    ]
    comp.prepare(msgs)
    assert msgs[3]["content"].startswith(COMPACT_MARK)  # 最老的 → 压
    assert msgs[5]["content"] == big                    # 最近 2 条已消费 → 保
    assert msgs[7]["content"] == big
    assert msgs[9]["content"] == big                    # 当前批 → 保


def test_short_consumed_results_skipped():
    """短于 min_compact_len 的结果不压（压了也没收益）"""
    comp = ContextCompactor(context_char_limit=500, keep_recent=0, min_compact_len=400)
    short = _search_result(blocks=1, text_len=50)
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(short, "c1"),    # 已消费但很短
        _assistant("c2"), _tool(_search_result(), "c2"),
    ]
    info = comp.prepare(msgs)
    assert msgs[3]["content"] == short
    assert info["aged"] == 0


def test_todo_list_exempt_from_aging():
    """todo 清单是状态数据：超过最小压缩长且已消费，也不被老化（防模型丢计划）"""
    comp = ContextCompactor(context_char_limit=500, keep_recent=0, min_compact_len=400)
    todo_render = ("[ ] 检索 Mamba 论文的核心机制并记录要点\n"
                   "[>] 检索 Transformer 论文的注意力机制并记录要点\n"
                   "[ ] 对比两者在长序列建模上的差异，输出对比表并总结\n"
                   "[ ] 汇总引用与章节来源，生成最终回答\n"
                   "[ ] " + "补充验证" * 80 + "\n\n（已完成 0/5）")
    assert len(todo_render) > 400                      # 超过最小压缩长（否则本来也不压，测不出豁免）
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(todo_render, "c1"),        # todo 清单（已消费）
        _assistant("c2"), _tool(_search_result(), "c2"),   # 普通检索结果（对照组）
        _assistant("c3"), _tool(_search_result(), "c3"),
    ]
    info = comp.prepare(msgs)
    assert msgs[3]["content"] == todo_render               # todo 清单原样保留
    assert msgs[5]["content"].startswith(COMPACT_MARK)     # 普通结果照常压成指针
    assert info["aged"] == 1


# ---------- 幂等守卫 ----------

def test_idempotent_second_pass():
    """prepare 跑两遍：第二遍不再动手（[已压缩] 标记守卫）"""
    comp = ContextCompactor(context_char_limit=500, keep_recent=1, min_compact_len=50)
    big = _search_result()
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(big, "c1"),
        _assistant("c2"), _tool(big, "c2"),
        _assistant("c3"), _tool(big, "c3"),
        _assistant("c4"), _tool(big, "c4"),      # 当前批
    ]
    comp.prepare(msgs)
    snapshot = json.dumps(msgs, ensure_ascii=False)
    info2 = comp.prepare(msgs)
    assert info2["aged"] == 0
    assert json.dumps(msgs, ensure_ascii=False) == snapshot


# ---------- 压缩动作 ----------

def test_stub_keeps_structure_info():
    """压成指针时保留结构信息（论文 ID / 章节），不是裸截断"""
    comp = ContextCompactor(context_char_limit=500, keep_recent=0, min_compact_len=50)
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(_search_result(paper_id=7), "c1"),
        _assistant("c2"), _tool(_search_result(paper_id=11), "c2"),
    ]
    comp.prepare(msgs)
    stub = msgs[3]["content"]
    assert stub.startswith(COMPACT_MARK)
    assert "涉及论文 [7]" in stub
    assert "Section 0" in stub


def test_oversized_fresh_result_truncated():
    """单条超硬限：截断留预览，且不重复截断"""
    comp = ContextCompactor(result_char_limit=1000, batch_char_limit=15000, context_char_limit=100000)
    big = _search_result()
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant(), _tool(big),
    ]
    info = comp.prepare(msgs)
    assert info["clipped"] == 1
    assert len(msgs[3]["content"]) < len(big)
    assert "[已截断" in msgs[3]["content"]
    info2 = comp.prepare(msgs)
    assert info2["clipped"] == 0


def test_batch_budget_divides_among_results():
    """并行多结果同轮到达：批量超预算时按份数均分收紧"""
    comp = ContextCompactor(result_char_limit=6000, batch_char_limit=6000, context_char_limit=100000)
    big = _search_result()
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(big, "c1"), _tool(big, "c2"), _tool(big, "c3"),
    ]
    info = comp.prepare(msgs)
    assert info["clipped"] == 3                        # 3 × ≈3.4K > 6K → 均分预算 2000/条
    for i in (3, 4, 5):
        assert len(msgs[i]["content"]) < 2200
        assert "[已截断" in msgs[i]["content"]


def test_emergency_compacts_all_consumed_keeps_fresh():
    """反应式 emergency：全部已消费都压（不保留最近 K），当前批保持"""
    comp = ContextCompactor()
    big = _search_result()
    msgs = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "q"},
        _assistant("c1"), _tool(big, "c1"),
        _assistant("c2"), _tool(big, "c2"),
        _assistant("c3"), _tool(big, "c3"),
    ]
    info = comp.emergency(msgs)
    assert info["aged"] == 2
    assert msgs[3]["content"].startswith(COMPACT_MARK)
    assert msgs[5]["content"].startswith(COMPACT_MARK)
    assert msgs[7]["content"] == big


# ---------- 协议完整性 ----------

def test_pairing_untouched():
    """压缩只改 content：消息条数/role/tool_call_id 全部原样（配对完整性）"""
    comp = ContextCompactor(context_char_limit=500, keep_recent=1, min_compact_len=50)
    msgs = [
        _assistant("c1"), _tool(_search_result(), "c1"),
        _assistant("c2"), _tool(_search_result(), "c2"),
        _assistant("c3"), _tool(_search_result(), "c3"),
    ]
    roles_before = [m["role"] for m in msgs]
    ids_before = [m.get("tool_call_id") for m in msgs if m["role"] == "tool"]
    comp.prepare(msgs)
    assert [m["role"] for m in msgs] == roles_before
    assert [m.get("tool_call_id") for m in msgs if m["role"] == "tool"] == ids_before
    assert len(msgs) == 6


# ---------- 收益报告（数字供素材表引用） ----------

def test_heavy_session_savings_report():
    """满 10 轮重会话：压缩后消息体积显著下降（打印实际数字）"""
    comp = ContextCompactor()  # 生产默认阈值（6000/15000/16000/keep 3）
    msgs = [{"role": "system", "content": "你是论文研读助手。"},
            {"role": "user", "content": "请对比这些论文"}]
    for i in range(10):
        msgs.append(_assistant(f"c{i}"))
        msgs.append(_tool(_search_result(paper_id=i % 6 + 1), f"c{i}"))
        if i % 2 == 0:
            msgs.append(_tool(_search_result(paper_id=11, text_len=400), f"c{i}b"))
    before = ContextCompactor.estimate_chars(msgs)
    info = comp.prepare(msgs)
    after = ContextCompactor.estimate_chars(msgs)
    print(f"\n[重会话报告] 10 轮：{before:,} → {after:,} 字符"
          f"（-{100 * (before - after) / before:.1f}%），老化 {info['aged']} 条")
    assert info["aged"] > 0
    assert after < before * 0.6
