# -*- coding: utf-8 -*-
"""
缓存观测（hits / misses / builds）回归测试：口径是【请求级】。

背景（2026-09-16）：缓存上线后要能量化回答"命中率多少、省了几次生成"。
计数定在 get_or_build 的快路径，不放 get_raw——
    - get_raw 跑在 to_thread 工作线程里，同一请求还可能读多次（见锁内双检）
    - "命中率"的业务口径 = 请求被缓存直接服务的比例，请求级才对

覆盖：
    - 命中：hits +1；misses/builds 不动；builder 不跑
    - 未命中→生成→再访问：misses +1、builds +1；第二次是纯命中 hits +1
    - 并发未命中：misses 按请求数 +5；builds 只 +1（单飞合并）；hits 不动
    - Redis 挂：照记 miss；stats() 照常可读；redis=False 标志位
    - 零除保护：total=0 → hit_rate 为 None；比率保留 3 位
    - /api/health 暴露 cache 观测键（只加键、不动 status）

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_cache_stats.py -v
前提：本地 Redis 已启动；不调 LLM；health 用例只读 /api/health。
"""
import asyncio

import redis as redis_lib

from app.config import settings
from app.services.cache import llm_cache

_r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)


def _snap() -> tuple[int, int, int]:
    """计数快照（hits, misses, builds）。llm_cache 是全局单例，别的用例也在动它，
    所以一律比对【增量】，不比绝对值。"""
    s = llm_cache.stats()
    return s["hits"], s["misses"], s["builds"]


def _delta(before, after):
    return tuple(a - b for a, b in zip(after, before))


# ==================== 命中 / 未命中 ====================

def test_fast_path_hit_counts_hit_once():
    """命中：hits +1；builder 一次不跑；misses/builds 不动"""
    key = "test:stats:hit"
    _r.set(key, "缓存值", ex=60)
    calls = []

    async def builder():
        calls.append(1)
        return "不该被调"

    before = _snap()
    try:
        assert asyncio.run(llm_cache.get_or_build(key, builder, 60)) == "缓存值"
        assert _delta(before, _snap()) == (1, 0, 0), "命中增量应为 hits+1"
        assert calls == []
    finally:
        _r.delete(key)


def test_miss_counts_and_second_access_is_pure_hit():
    """未命中：misses +1、builds +1；同键再来一次 → 纯命中 hits +1"""
    key = "test:stats:miss"
    _r.delete(key)
    calls = []

    async def builder():
        calls.append(1)
        return "生成值"

    try:
        before = _snap()
        assert asyncio.run(llm_cache.get_or_build(key, builder, 60)) == "生成值"
        assert _delta(before, _snap()) == (0, 1, 1), "首访应为 misses+1、builds+1"
        assert len(calls) == 1

        mid = _snap()
        assert asyncio.run(llm_cache.get_or_build(key, builder, 60)) == "生成值"
        assert _delta(mid, _snap()) == (1, 0, 0), "第二次应是纯命中"
        assert len(calls) == 1
    finally:
        _r.delete(key)


# ==================== 并发：misses 按请求算，builds 被单飞合并 ====================

def test_concurrent_misses_count_per_request_but_build_once():
    """5 个并发打空键：misses +5（请求级），builds 只 +1（单飞），hits 不动"""
    key = "test:stats:concurrent"
    _r.delete(key)

    async def main():
        async def builder():
            await asyncio.sleep(0.2)   # 冒充长生成
            return "共享值"

        return await asyncio.gather(
            *[llm_cache.get_or_build(key, builder, 60) for _ in range(5)]
        )

    try:
        before = _snap()
        assert asyncio.run(main()) == ["共享值"] * 5
        assert _delta(before, _snap()) == (0, 5, 1), "5 个请求 = 5 个 miss、1 次生成"
    finally:
        _r.delete(key)


# ==================== 降级与口径 ====================

def test_redis_down_still_counts_and_reports(monkeypatch):
    """Redis 挂：全算 miss、stats() 照常可读、redis=False 提示降级（铁律 1）"""
    key = "test:stats:degraded"
    monkeypatch.setattr(llm_cache, "_r", None)

    async def builder():
        return "直通值"

    before = _snap()
    assert asyncio.run(llm_cache.get_or_build(key, builder, 60)) == "直通值"
    after = llm_cache.stats()

    assert _delta(before, (after["hits"], after["misses"], after["builds"])) == (0, 1, 1)
    assert after["redis"] is False


def test_stats_zero_division_and_rate_shape(monkeypatch):
    """total=0 → hit_rate 给 None（0 会被误读成"全没命中"）；比率保留 3 位"""
    monkeypatch.setattr(llm_cache, "_hits", 0)
    monkeypatch.setattr(llm_cache, "_misses", 0)
    assert llm_cache.stats()["hit_rate"] is None

    monkeypatch.setattr(llm_cache, "_hits", 3)
    monkeypatch.setattr(llm_cache, "_misses", 1)
    s = llm_cache.stats()
    assert s["hit_rate"] == 0.75
    assert {"hits", "misses", "builds", "hit_rate", "redis"} <= set(s)


# ==================== 对外暴露：/api/health ====================

def test_health_exposes_cache_stats():
    """GET /api/health 带 cache 观测键（只加键、不动 status）"""
    from fastapi.testclient import TestClient

    from app.api.main import app

    resp = TestClient(app).get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert {"hits", "misses", "builds", "hit_rate", "redis"} <= set(body["cache"])
