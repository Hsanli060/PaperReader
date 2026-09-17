"""
Redis 缓存：内容派生对象缓存（读时查，写时存，挂了降级不报错）

四条铁律：
    1. Redis 挂了 ≠ 系统挂：每个方法里所有 Redis 操作都包 try/except，失败静默降级
    2. 只缓存"确定可复现"的数据（同输入必同输出的派生对象），不缓存依赖上下文的对话
    3. 【防击穿】热键过期不许"惊群"：同键的并发请求只让一个人真去生成（LLM 要 10–30s），
       其余人在门口排队等这一趟的结果 —— 见 get_or_build
    4. 【防雪崩】不许集体过期：TTL 一律加随机抖动，别让同一批写入的键在同一秒一起
       失效、把下游打成一波尖峰 —— 见 _jittered_ttl

铁律 3 和 4 的分工：3 管【一个键】的并发（同一时刻），4 管【一批键】的到期（同一时刻），
别混——前者靠锁，后者靠随机数。

谁调用它：papers.py 的 summary / citations 接口（get_or_build 查+生成），
         papers.py 的删除论文接口（delete_raw 清掉该论文的派生缓存）
观测：hits / misses / builds 三个计数（请求级口径）+ stats()，GET /api/health 的 cache 键可见
"""
import asyncio
import random
from collections.abc import Awaitable, Callable

import redis as redis_lib

from app.config import settings
from loguru import logger


# 防雪崩：TTL 末尾加一段随机抖动，避免同一批写入的键在同一秒集体失效。
# 0.1 = 最多多活 10%（86400 → 86400~95040，把过期时刻摊开约 2.4 小时）
TTL_JITTER_RATIO = 0.1


def _jittered_ttl(ttl_seconds:int)->int:
    """基准 TTL + 一段随机抖动（防雪崩）。

    :param ttl_seconds: (int) 基准 TTL；<=0 原样返回（调用方可能故意要立即过期）
    :return: (int) ttl_seconds + [0, ttl_seconds*TTL_JITTER_RATIO] 的随机偏移
    """
    if ttl_seconds<=0:
        return ttl_seconds
    return ttl_seconds+random.randint(0,int(ttl_seconds*TTL_JITTER_RATIO))


class _Flight:
    __slots__ = ("lock", "done", "value", "error", "users")

    def __init__(self):
        self.lock = asyncio.Lock()          # 排队栏杆：一个键同时只有一个人能进来生成
        self.done = False                   # 这一趟有没有结论（成功、失败都算有）
        self.value: str | None = None       # 生成结果（None = 生成了但没东西可缓存）
        self.error: Exception | None = None # 生成失败的原因（排队的人原样抛出，不重跑）
        self.users = 0                      # 在创建缓存的人数


class LLMCache:
    """Redis 通用缓存。全局只建一个实例"""
    def __init__(self):
        self._r:redis_lib.Redis|None=None   # Redis 连接变量
        if settings.REDIS_URL:
            try:
                self._r=redis_lib.from_url(     #创建连接
                    settings.REDIS_URL,
                    decode_responses=True,  # 自动转成 str
                    socket_connect_timeout=2,   # 建连超时 2 秒
                    socket_timeout=2,   # 读写超时 2 秒
                )
                self._r.ping()      # 探活
                logger.info("Redis 缓存已连接")
            except Exception as e:
                logger.warning(f"Redis 连不上，缓存降级为直通模式：{e}")
                self._r=None
        self._flights:dict[str,_Flight]={}  # 并发读取/创建缓存的键
        self._builds:int=0                  # 真跑了几次 builder（验收用：并发 5 个请求，它只该 +1）
        self._hits:int=0                    # 快路径命中次数（请求级；stats() 观测用）
        self._misses:int=0                  # 快路径未命中次数（请求级；stats() 观测用）

    def _conn(self)->redis_lib.Redis|None:
        return self._r

    def get_raw(self,key:str)->str|None:
        """自由键读缓存：键随便起（如 summary:3），命中返回字符串，没命中 None"""
        r=self._conn()
        if r is None:
            return None
        try:
            return r.get(key)
        except Exception as e:
            logger.warning(f"缓存读取失败（降级直通）：{e}")
            return None

    def set_raw(self,key:str,value:str,ttl_seconds:int=3600)->None:
        """自由键写缓存：同样挂了不报错，静默降级"""
        r=self._conn()
        if r is None:
            return
        try:
            r.set(key,value,ex=ttl_seconds)
        except Exception as e:
            logger.warning(f"缓存写入失败（忽略）：{e}")

    def delete_raw(self,*keys:str)->int:
        """删缓存键：论文被删时清掉它的派生缓存（summary:3 / citations:3 这类）。

        :param keys: (str) 要删的键，可传多个——Redis DEL 原生支持多键，一次往返
        :return: (int) 实际删掉的键数；Redis 挂了或没传键返回 0
            （降级：清理失败不连累主流程，那些键最迟也会被 TTL 带走）
        """
        r=self._conn()
        if r is None or not keys:
            return 0
        try:
            return r.delete(*keys)
        except Exception as e:
            logger.warning(f"缓存删除失败（忽略）：{e}")
            return 0

    def stats(self)->dict:
        """观测快照：请求级命中/未命中 + 真实生成次数。

        计数自进程启动累计、重启清零（观测工具，不是精确账本）；
        redis=False 时下面数字不能当数——全都没读上缓存（降级直通）。
        """
        total=self._hits+self._misses
        return {
            "hits":self._hits,
            "misses":self._misses,
            "builds":self._builds,
            # 无请求时给 None：给 0 会被误读成"全没命中"
            "hit_rate":round(self._hits/total,3) if total else None,
            "redis":self._r is not None,
        }

    async def get_or_build(
            self,
            key:str,
            builder:Callable[[],Awaitable[str|None]],
            ttl_seconds:int=3600,
    )->str|None:
        """查缓存；没命中时【同一个键只让一个人】真去生成，其余人排队等同一份结果。

        :param key: (str) 缓存键，如 summary:3
        :param builder: (async 无参函数) 真去生成的地方，如 _summarize_paper_tool；
            返回字符串 → 写进缓存并返回；返回 None → "生成了但没东西可缓存"，不写缓存
        :param ttl_seconds: (int) 写缓存的过期秒数
        :return: (str|None) 缓存命中或生成成功的字符串；builder 返回 None 时也返回 None
        :raises: builder 抛什么就抛什么。同一波排队的人拿到的是同一个异常实例，
            不会各自再跑一遍（否则失败路径会退化成 N 次串行重跑）
        """

        # 1) 快路径：缓存命中，直接返回（绝大多数请求走这里，一次 LLM 都不用调）
        cached=await asyncio.to_thread(self.get_raw,key)
        if cached is not None:
            self._hits+=1           # 命中次数+1
            return cached
        self._misses+=1             # 未命中次数+1

        # 2) 慢路径：没命中。看这个键有没有"正在生成中"，没有就开一趟
        flight=self._flights.get(key)
        if flight is None:
            flight=_Flight()
            self._flights[key]=flight
        flight.users+=1     # 创建缓存人数+1

        try:
            async with flight.lock:
                # 3) 先读结论：如果我是排队等到的，说明这一趟已经有人生成过了
                if flight.done:
                    if flight.error is not None:
                        raise flight.error      # 先到的人失败了 → 不重跑
                    return flight.value         # 先到的人有结果（含"没东西可缓存"）→ 直接拿走

                # 3.5) 锁内双检：快路径那次读可能赶在"上一趟写缓存"之前拿到旧视图
                cached = await asyncio.to_thread(self.get_raw, key)
                if cached is not None:
                    flight.value = cached
                    flight.done = True
                    return cached

                # 4) 这一趟还没结论 → 由我来生成（此刻锁在我手上，别人都在外面排队）
                self._builds+=1
                try:
                    value=await builder()
                except asyncio.CancelledError:
                    # 生成者被取消（典型场景：客户端断开连接，FastAPI 会取消处理协程）：
                    # 不给这一趟下定论 —— 让下一个拿到锁的人接着生成
                    raise
                except Exception as error:
                    flight.error=error
                    flight.done=True
                    raise
                # 生成成功：先落缓存（让之后的请求走快路径，连锁都不用进），再下结论。
                # TTL 走 _jittered_ttl：防雪崩
                if value is not None:
                    await asyncio.to_thread(self.set_raw,key,value,_jittered_ttl(ttl_seconds))
                flight.value=value
                flight.done=True
                return value
        finally:
            flight.users-=1
            if flight.users==0:
                self._flights.pop(key,None)

llm_cache=LLMCache()
