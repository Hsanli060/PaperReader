"""
数据库引擎 + 会话工厂
Engine = 电话公司（全局一份，管理连接池）
Session = 一通电话（每个请求开一个，用完关闭）
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings   # 全局唯一配置实例（config.py 里已实例化）

# 所有 ORM 类的基类放在这里（基建层），orm.py 里的表类都继承它。
# env.py / API 层需要"表图纸"时统一从 database.py 导入，保证全局只有一份 Base
class Base(DeclarativeBase):
    pass# echo=True 会打印每条 SQL 到控制台，开发期开着方便观察 ORM 到底发了什么 SQL
engine=create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True      # 每次借连接前先"ping"一下，连接断了自动重连
)

# 会话工厂：不是 Session 本身，是"生产 Session 的工厂"
#pool_pre_ping=True：MySQL/PostgreSQL 默认闲置 8 小时断连接，不加这个第二天上班第一条请求必报错
#expire_on_commit=False：默认 commit 后对象会被“标记过期”，下次访问属性又要查一次库。关掉它，commit 后对象还是活的，对 FastAPI 场景更省事。
SessionLocal=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)

