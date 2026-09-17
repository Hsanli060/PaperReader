"""
TTL 抖动（防雪崩）回归测试。

背景（2026-09-16）：summary:{id} / citations:{id} 的 TTL 全是固定 86400。
用户一口气浏览 N 篇论文时，这 N 个键在同一秒写入 → 24 小时后同一秒集体失效
→ N 个请求同时未命中、同时打 LLM。抖动把过期时刻摊开，尖峰变缓坡。

别和防击穿搞混：同一时刻【单键】的并发由 get_or_build 的航班机制兜住
（见 tests/test_cache_singleflight.py）；这里测的是【一批键】的到期时刻分布。

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_cache_ttl_jitter.py -v
前提：本地 Redis 已启动；不调 LLM。
"""
import asyncio

import redis as redis_lib

from app.config import settings
from app.services.cache import TTL_JITTER_RATIO, _jittered_ttl, llm_cache

KEY = "test:ttl-jitter:1"
BASE = 1000                 # 测试用的基准 TTL，抖动上限 = 1000 + 100
_r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)


# ==================== 纯函数：边界 ====================

def test_jitter_within_bounds():
    """500 次抽样全部落在 [base, base*(1+ratio)] 内，一次都不越界"""
    hi = BASE + int(BASE * TTL_JITTER_RATIO)
    values = [_jittered_ttl(BASE) for _ in range(500)]
    assert min(values) >= BASE, "抖动只能让 TTL 变长，不能变短（变短会提前失效）"
    assert max(values) <= hi, "抖动不能超过比例上限，否则 TTL 不可预期"


def test_jitter_actually_spreads():
    """500 次抽样要产生大量不同取值——返回常量的话抖动等于没做"""
    values = [_jittered_ttl(BASE) for _ in range(500)]
    assert len(set(values)) >= 50, f"500 次只产生了 {len(set(values))} 种取值，抖动量太小"


def test_small_ttl_jitter_rounds_down_to_zero():
    """小 TTL 不能越界：int(1*0.1)=0 → randint(0,0) → 只能原样返回 1"""
    assert _jittered_ttl(1) == 1


def test_nonpositive_ttl_passthrough():
    """ttl<=0 原样返回：调用方可能故意要"立即过期"，不该被抖动改成有效 TTL"""
    for ttl in (0, -1, -3600):
        assert _jittered_ttl(ttl) == ttl


def test_real_ttl_spread_covers_hours():
    """路由真实用的 86400 秒：抖动量必须是小时级，才躲得开"同一秒"的尖峰"""
    values = [_jittered_ttl(86400) for _ in range(200)]
    spread = max(values) - min(values)
    assert spread >= 3600, f"86400 秒的 TTL 只摊开了 {spread} 秒，不足以错开集体过期"


# ==================== 写入路径：抖动真的落地了吗 ====================

def test_written_ttl_is_jittered():
    """同一批写 12 个键：TTL 不能全一样，且都在 [base, base+10%] 内。

    这正是防雪崩要的效果——12 个键的过期时刻必须散开，
    否则它们会在同一秒集体失效，把下游打成一波尖峰。
    """
    async def builder():
        return "v"

    keys = [f"{KEY}:{i}" for i in range(12)]
    for k in keys:
        _r.delete(k)
    try:
        seen = set()
        for k in keys:
            asyncio.run(llm_cache.get_or_build(k, builder, BASE))
            seen.add(_r.ttl(k))

        hi = BASE + int(BASE * TTL_JITTER_RATIO)
        assert all(BASE <= t <= hi for t in seen), f"写入的 TTL 越界: {sorted(seen)}"
        assert len(seen) > 1, f"12 个键的 TTL 全是 {seen}——它们会在同一秒集体过期"
    finally:
        for k in keys:
            _r.delete(k)


def test_jitter_applies_only_when_writing_not_when_reading():
    """抖动只影响"写进去的 TTL"，不影响已有键——命中路径不碰 TTL，老键照常按原时间过期"""
    async def builder():
        return "v"

    _r.delete(KEY)
    try:
        asyncio.run(llm_cache.get_or_build(KEY, builder, BASE))
        ttl_first = _r.ttl(KEY)
        asyncio.run(llm_cache.get_or_build(KEY, builder, BASE))   # 第二次是命中
        assert _r.ttl(KEY) <= ttl_first, "命中不该续期（否则热键永不过期，缓存白做）"
    finally:
        _r.delete(KEY)
