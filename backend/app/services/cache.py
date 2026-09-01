"""
Redis 缓存：LLM 问答响应缓存（读时查，写时存，挂了降级不报错）

三条铁律：
    1. Redis 挂了 ≠ 系统挂：每个方法里所有 Redis 操作都包 try/except，失败静默降级
    2. 只缓存 Agent 的最终回答（不缓存工具中间结果——它们每轮都不同）
    3. key 精确到 (论文范围, 问题本身)——同问题不同论文是不同缓存

谁调用它：4.5 的 chat 路由（agent 跑之前先查，跑完之后写）
"""
import json

import  redis as redis_lib

from app.config import settings
from loguru import logger

class LLMCache:
    """LLM 响应缓存。全局只建一个实例"""
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
                logger.warning(f"Redis 连不上，缓存降级为直连模式：{e}")
                self._r=None

    def _conn(self)->redis_lib.Redis|None:
        return self._r

    @staticmethod
    def _key(paper_id:int|None,question:str)->str:
        """构造缓存键"""
        pid=paper_id if paper_id is not None else "all"
        return f"llm:{pid}:{question.strip()}"

    def get(self,paper_id:int|None,question:str)->str|None:
        """查缓存"""
        r=self._conn()
        if r is None:
            return None
        try:
            raw=r.get(self._key(paper_id,question))
            return raw
        except Exception as e:
            logger.warning(f"缓存读取失败（降级直连）：{e}")
            return None

    #写入缓存
    def set(self,paper_id:int|None,question:str,answer:str,ttl_seconds:int=3600)->None:
        """写入Redis缓存

        :param paper_id: 哪篇论文的回答
        :param question: 用户的问题
        :param answer: LLM的回答
        :param ttl_seconds: 多久后删除缓存
        :return: None
        """
        r=self._conn()
        if r is None:
            return
        try:
            #写入  （论文ID，问题）-唯一键   LLM的回答   多久过期
            r.set(self._key(paper_id, question), answer, ex=ttl_seconds)
        except Exception as e:
            logger.warning(f"缓存写入失败（忽略）：{e}")

llm_cache=LLMCache()