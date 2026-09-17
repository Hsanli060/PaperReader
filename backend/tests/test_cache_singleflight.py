"""
single-flight 回归测试：热键过期瞬间 N 个并发只生成一次。

背景问题（2026-09-16）：summary:3 / citations:3 这类键 TTL 到期的那一瞬间，
同时打进来的 N 个请求全都未命中，各自跑一遍 10–30s 的 LLM 生成 ——
上游被打 N 次，用户也没更快。get_or_build 让第一个人真生成，
其余人排队等同一份结果（实现见 app/services/cache.py 的 _Flight）。

覆盖：
    - 缓存命中：直接返回，builder 一次都不跑
    - 并发未命中：builder 只跑一次，所有人拿到同一份，结果落 Redis
    - 失败共享：领跑者抛异常，同一波的人跟着抛，不退化成 N 次串行重跑
    - 失败只属于那一波：散场后新请求照常重新生成（键不会被钉死）
    - builder 返回 None（空结果）：不写缓存，但同一波共享这个 None
    - 领跑者被取消（客户端断连）：等待者接管生成，不会留下假失败
    - 散场后航班条目回收（_flights 不会随论文数越攒越多）
    - 散场夹缝：快路径拿到旧视图的晚到者，锁内双检兜住不重建
    - Redis 挂掉：降级直通，builder 照跑（铁律 1）

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_cache_singleflight.py -v
前提：本地 Redis 已启动（用真 Redis，键名带 test: 前缀，测完删除）；不调 LLM、不连 PG。
"""
import asyncio

import redis as redis_lib

from app.config import settings
from app.services.cache import llm_cache

KEY = "test:singleflight:1"

# 测试要直连 Redis 检查"到底写没写"（llm_cache 只有 get_or_build，没有 delete 接口）
_r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)


def _reset(key: str = KEY) -> None:
    """用例开始前清场：删缓存键 + 清空航班表和生成计数。

    llm_cache 是全局单例，_flights / _builds 是跨用例共享的状态，
    不清的话上个用例的航班会漏进来。
    """
    _r.delete(key)
    llm_cache._flights.clear()
    llm_cache._builds = 0


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    """轮询等条件成立——不用固定 sleep 赌时序，真慢了会明确报超时而不是随机挂"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "等待条件超时（时序和预期不符）"
        await asyncio.sleep(0.005)


def _flight_users(key: str = KEY) -> int:
    """这一趟航班上还剩几个人（0 = 已经散场）"""
    flight = llm_cache._flights.get(key)
    return flight.users if flight is not None else 0


# ==================== 快路径：命中 ====================

def test_hit_returns_cached_without_building():
    """缓存命中：直接返回缓存值，builder 一次都不跑"""
    _reset()
    _r.set(KEY, "已缓存的内容", ex=60)
    calls = []

    async def builder():
        calls.append(1)
        return "新生成的内容"

    assert asyncio.run(llm_cache.get_or_build(KEY, builder, 60)) == "已缓存的内容"
    assert calls == [], "命中时不该调用 builder"
    assert llm_cache._flights == {}, "命中走的是快路径，连锁都不该进"
    _r.delete(KEY)


# ==================== 核心：并发未命中只生成一次 ====================

def test_concurrent_miss_builds_once():
    """热键过期瞬间 5 个并发：builder 只跑一次，5 个人拿到同一份，结果落 Redis"""
    _reset()

    async def main():
        async def builder():
            await asyncio.sleep(0.2)     # 冒充 10–30s 的 LLM 生成
            return "摘要内容"

        return await asyncio.gather(
            *[llm_cache.get_or_build(KEY, builder, 60) for _ in range(5)]
        )

    assert asyncio.run(main()) == ["摘要内容"] * 5
    assert llm_cache._builds == 1, "并发未命中必须只生成一次"
    assert _r.get(KEY) == "摘要内容", "生成结果要落缓存，让后来的请求走快路径"
    assert llm_cache._flights == {}, "散场后航班条目要回收"
    _r.delete(KEY)


# ==================== 失败路径：共享结论，不退化成串行重跑 ====================

def test_shared_failure_does_not_retry():
    """领跑者失败：同一波的人跟着抛同一个异常，builder 仍然只跑一次。

    若退化成各自重试，_builds 会是 5（而且是一个接一个跑，总耗时 5 倍）。
    """
    _reset()

    async def main():
        async def builder():
            await asyncio.sleep(0.2)
            raise RuntimeError("upstream 429")

        return await asyncio.gather(
            *[llm_cache.get_or_build(KEY, builder, 60) for _ in range(5)],
            return_exceptions=True,
        )

    results = asyncio.run(main())
    assert all(isinstance(r, RuntimeError) for r in results), "5 个人都该拿到那个异常"
    assert llm_cache._builds == 1, "失败必须共享，不能退化成 5 次串行重跑"
    assert _r.get(KEY) is None, "失败的结论不写缓存"
    assert llm_cache._flights == {}


def test_next_wave_retries_after_failure():
    """失败只属于那一波：散场后的新请求照常重新生成（键不会被钉死）"""
    _reset()
    attempts = []

    async def main():
        async def failing():
            attempts.append("fail")
            raise RuntimeError("upstream 429")

        try:
            await llm_cache.get_or_build(KEY, failing, 60)
        except RuntimeError:
            pass

        async def ok():
            attempts.append("ok")
            return "重试成功"

        return await llm_cache.get_or_build(KEY, ok, 60)

    assert asyncio.run(main()) == "重试成功"
    assert attempts == ["fail", "ok"], "第二波必须重新跑 builder，而不是复用上一波的失败"


# ==================== builder 返回 None（citations 空结果那种） ====================

def test_none_result_not_cached_but_shared_in_wave():
    """builder 返回 None：不写缓存（下次还能重试），但同一波共享这个 None"""
    _reset()
    calls = []

    async def main():
        async def builder():
            calls.append(1)
            await asyncio.sleep(0.1)
            return None

        return await asyncio.gather(
            *[llm_cache.get_or_build(KEY, builder, 60) for _ in range(3)]
        )

    assert asyncio.run(main()) == [None, None, None]
    assert len(calls) == 1, "None 也要在同一波里共享，不能各自重跑"
    assert _r.get(KEY) is None, "None 不写缓存"


# ==================== 取消路径：领跑者断连，等待者接管 ====================

def test_leader_cancelled_waiter_takes_over():
    """领跑者被取消（客户端断连）：不下假结论，等待者接着生成。

    这正是 cancel 分支必须和 except Exception 分开写的原因 ——
    页面关了不代表上游有问题，不该让还在等的人吃一个假错误。
    """
    _reset()
    calls = []

    async def main():
        started = asyncio.Event()

        async def builder():
            calls.append(1)
            if len(calls) == 1:
                started.set()
                await asyncio.sleep(10)      # 领跑者：挂住，等着被取消
                return "never"
            return "接管成功"

        leader = asyncio.create_task(llm_cache.get_or_build(KEY, builder, 60))
        await asyncio.wait_for(started.wait(), 2)          # 领跑者已开跑
        follower = asyncio.create_task(llm_cache.get_or_build(KEY, builder, 60))
        await _wait_until(lambda: _flight_users() == 2)    # 等 follower 排进这一趟航班

        leader.cancel()
        try:
            await leader
            raise AssertionError("leader 应该被取消")
        except asyncio.CancelledError:
            pass

        return await asyncio.wait_for(follower, 2)

    assert asyncio.run(main()) == "接管成功"
    assert llm_cache._builds == 2, "第一次被取消、第二次接管，共跑两次"
    assert _r.get(KEY) == "接管成功"
    assert llm_cache._flights == {}
    _r.delete(KEY)


# ==================== 降级：Redis 挂了不能连累业务 ====================

def test_redis_down_degrades_to_direct_build(monkeypatch):
    """Redis 挂掉：builder 照跑，只是不缓存（铁律 1：Redis 挂了 ≠ 系统挂）"""
    _reset()
    monkeypatch.setattr(llm_cache, "_r", None)

    async def builder():
        return "直通结果"

    assert asyncio.run(llm_cache.get_or_build(KEY, builder, 60)) == "直通结果"
    assert llm_cache._flights == {}


# ==================== 散场夹缝：锁内双检兜住晚到者 ====================

def test_straggler_double_check_prevents_rebuild(monkeypatch):
    """散场夹缝：快路径读到的是"上一趟写缓存之前"的旧视图（毫秒级时序），
    锁内双检必须把它兜住——缓存里已有值就绝不重建。

    注入式复现：把第一次 get_raw 强制返回 None（模拟旧视图），之后恢复真实读。
    缺双检时这里会白跑一遍 builder（一次 10–30s 的 LLM 生成就这么浪费了）。
    """
    _reset()
    _r.set(KEY, "已有缓存值", ex=60)   # 模拟：上一趟航班刚把值写好

    real_get_raw = llm_cache.get_raw
    state = {"stale": True}

    def stale_once(k):
        if state["stale"]:
            state["stale"] = False
            return None             # 这一次读发生在上一趟写缓存之前
        return real_get_raw(k)

    monkeypatch.setattr(llm_cache, "get_raw", stale_once)
    calls = []

    async def builder():
        calls.append(1)
        return "重复生成"

    assert asyncio.run(llm_cache.get_or_build(KEY, builder, 60)) == "已有缓存值"
    assert calls == [], "缓存里已有值，锁内双检应直接收工"
    assert llm_cache._builds == 0
    assert llm_cache._flights == {}
    _r.delete(KEY)
