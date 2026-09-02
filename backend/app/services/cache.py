"""
Redis 缓存：内容派生对象缓存（读时查，写时存，挂了降级不报错）

两条铁律：
    1. Redis 挂了 ≠ 系统挂：每个方法里所有 Redis 操作都包 try/except，失败静默降级
    2. 只缓存"确定可复现"的数据（同输入必同输出的派生对象），不缓存依赖上下文的对话

谁调用它：papers.py 的 summary / citations 接口（get_raw/set_raw）
"""
import redis as redis_lib

from app.config import settings
from loguru import logger

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

llm_cache=LLMCache()
