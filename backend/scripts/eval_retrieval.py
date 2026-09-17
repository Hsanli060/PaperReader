# -*- coding: utf-8 -*-
"""
检索评测（L1）：同一套题、两个模式，对比 hit@k / MRR / 耗时。

模式：
    基线 base —— search()：单路向量召回（改造前）
    进阶 adv  —— advanced_search()：改写 + 双路召回 + RRF + qwen3-rerank 精排（改造后）

判定规则（冻结，改规则必须重跑基线并说明）：
    一条结果命中某条 expected ⇔ paper_id 相同 且（section 含任意章节词 或 text 含任意关键词）
    大小写不敏感、子串匹配。多 expected 任一命中即算。
    rank = 第一个命中结果的排名（1 起）；没命中 = None。
    D 类为观察题（不进主指标）：base 看最小向量距离（越小越像），adv 看最高精排分（越低=越"知道自己不知道"）。

用法（在 backend 目录下执行）：
    ../.venv/Scripts/python.exe scripts/eval_retrieval.py            # 两个模式都跑
    ../.venv/Scripts/python.exe scripts/eval_retrieval.py --mode base

可比性冻结：评测期间不要往向量库加论文、不要改判定规则/模型。
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from app.rag.retriever import search
from app.rag.pipeline import advanced_search

EVAL_SET = Path(__file__).parent / "eval_set.json"


def load_cases() -> list[dict]:
    with open(EVAL_SET, encoding="utf-8") as f:
        return json.load(f)


def _hit_expected(hit: dict, e: dict) -> bool:
    """单条结果是否命中某一条 expected 标注。"""
    if hit.get("paper_id") != e["paper_id"]:
        return False
    section = (hit.get("section") or "").lower()
    if any(s.lower() in section for s in e.get("sections", [])):
        return True
    text = (hit.get("text") or "").lower()
    if any(k.lower() in text for k in e.get("keywords", [])):
        return True
    return False


def first_hit_rank(hits: list[dict], expected: list[dict]) -> int | None:
    """第一个命中结果的排名（1 起）；没命中返回 None。"""
    for rank, h in enumerate(hits, start=1):
        if any(_hit_expected(h, e) for e in expected):
            return rank
    return None


async def run_mode(mode: str, cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        t0 = time.perf_counter()
        if mode == "base":
            hits = await asyncio.to_thread(search, case["q"])
        else:
            hits = await advanced_search(case["q"])
        dt = (time.perf_counter() - t0) * 1000  # ms

        row = {"id": case["id"], "type": case["type"], "ms": dt, "rank": None, "obs": None}
        if case["type"] == "D":
            if mode == "base":
                row["obs"] = min(h["distance"] for h in hits) if hits else None
            else:
                row["obs"] = max(h.get("rerank_score") or 0.0 for h in hits) if hits else None
        else:
            row["rank"] = first_hit_rank(hits, case["expected"])
        rows.append(row)
    return rows


def summarize(rows: list[dict], label: str) -> None:
    main = [r for r in rows if r["type"] != "D"]
    n = len(main)

    def rate(k: int) -> float:
        return sum(1 for r in main if r["rank"] is not None and r["rank"] <= k) / n

    mrr = sum((1.0 / r["rank"]) if r["rank"] else 0.0 for r in main) / n
    avg_ms = sum(r["ms"] for r in rows) / len(rows)
    print(f"[{label:<26}] hit@1 {rate(1)*100:5.1f}%  hit@3 {rate(3)*100:5.1f}%  "
          f"hit@5 {rate(5)*100:5.1f}%  MRR {mrr:.2f}  | 平均 {avg_ms:6.0f}ms")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["both", "base", "adv"], default="both")
    args = ap.parse_args()

    cases = load_cases()
    n_main = sum(1 for c in cases if c["type"] != "D")
    n_d = sum(1 for c in cases if c["type"] == "D")
    print(f"题库：{len(cases)} 题（主指标 A/B/C = {n_main} 题，观察 D = {n_d} 题）")
    print("=" * 92)

    results: dict[str, list[dict]] = {}
    if args.mode in ("both", "base"):
        results["base"] = asyncio.run(run_mode("base", cases))
    if args.mode in ("both", "adv"):
        results["adv"] = asyncio.run(run_mode("adv", cases))

    for i, case in enumerate(cases):
        line = f"#{case['id']:>2} [{case['type']}] {case['q'][:32]:<34}"
        for m in ("base", "adv"):
            if m in results:
                r = results[m][i]
                cell = (f"obs={r['obs']:.3f}" if r["obs"] is not None else "obs=  -  ") \
                    if case["type"] == "D" else f"rank={r['rank'] if r['rank'] else '×'}"
                line += f" | {m}:{cell:<10}({r['ms']:5.0f}ms)"
        print(line)

    print("=" * 92)
    for m, label in (("base", "基线·单路向量"), ("adv", "进阶·改写+双路+RRF+精排")):
        if m in results:
            summarize(results[m], label)
    print()
    print("注：D 类为观察题，不进主指标——base 列是最小向量距离（越小越像），adv 列是最高精排分（越低越说明系统'知道自己不知道'）。")


if __name__ == "__main__":
    main()
