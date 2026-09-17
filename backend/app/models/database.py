"""
数据库引擎 + 会话工厂
Engine = 电话公司（全局一份，管理连接池）
Session = 一通电话（每个请求开一个，用完关闭）
"""
from contextvars import ContextVar

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings   # 全局唯一配置实例（config.py 里已实例化）

# 所有 ORM 类的基类放在这里（基建层），orm.py 里的表类都继承它。
# env.py / API 层需要"表图纸"时统一从 database.py 导入，保证全局只有一份 Base
class Base(DeclarativeBase):
    pass# echo=True 会打印每条 SQL 到控制台，开发期开着方便观察 ORM 到底发了什么 SQL
engine=create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,     # 每次借连接前先"ping"一下，连接断了自动重连
    # ---- 连接池调优：默认 5+10=15，对"单进程+后台任务+脚本"偏小 ----
    pool_size=10,           # 常驻连接数
    max_overflow=20,        # 峰值再借 20 条（合计 30，PG 上限 100 内）
    pool_timeout=30,        # 借不到最多等 30s（快速失败，不无限等）
    pool_recycle=1800,      # 30 分钟回收，防僵死连接（与 pre_ping 双保险）
    pool_use_lifo=True,     # LIFO：热点连接优先复用，空闲连接更快被回收
)

# ---- SQL 查询计数器：把"每个请求发了多少条 SQL"变成可上报的数字 ----
# 为什么用 ContextVar + 可变对象：中间件在请求入口 set 一个 QueryCounter；上下文会随
# 任务/线程复制，但复制的是【同一个对象的引用】——任何环节里的 ++ 都改在同一个 n 上，
# 所以中间件在请求结束时能读到全程累计值。没有计数器的上下文（脚本、后台任务）读到
# None，直接跳过：不计也不报错。
class QueryCounter:
    """一条请求的 SQL 计数器（可变对象：所有环节 ++ 的是同一个 n）"""

    __slots__=("n",)

    def __init__(self)->None:
        self.n=0


_query_counter:ContextVar[QueryCounter|None]=ContextVar("db_query_counter",default=None)


@event.listens_for(engine,"before_cursor_execute")
def _count_sql(conn,cursor,statement,parameters,context,executemany)->None:
    """每条 SQL 执行前自增（挂 engine 上=所有连接、所有会话都算）"""
    counter=_query_counter.get()
    if counter is not None:
        counter.n+=1


# 会话工厂：不是 Session 本身，是"生产 Session 的工厂"
#pool_pre_ping=True：MySQL/PostgreSQL 默认闲置 8 小时断连接，不加这个第二天上班第一条请求必报错
#expire_on_commit=False：默认 commit 后对象会被“标记过期”，下次访问属性又要查一次库。关掉它，commit 后对象还是活的，对 FastAPI 场景更省事。
SessionLocal=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)

