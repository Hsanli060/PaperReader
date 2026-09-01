"""
依赖注入：可复用的"参数供给器"
谁调用它：FastAPI 框架（路由函数参数里写 Depends(get_current_user) 时自动调用）

bearer 是 FastAPI 自带的"从请求头提取 Bearer token"提取器：
    请求头形如 Authorization: Bearer eyJhbGci...
    auto_error=False：没带头时不让它自己抛 403，交给我们抛统一格式的 401
"""
from fastapi import FastAPI, Depends
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials

from app.middleware.error_handler import AppException
from app.models.database import SessionLocal
from app.security import decode_token

#创建一个“Token 提取器”对象，负责获取请求里的Authorization字段，并把Bearer(scheme字段) 和 Token(credentials字段)分开存储
bearer=HTTPBearer(auto_error=False)     #False：如果前端没带 Token，FastAPI 不会报错，而是默默返回 None。

def get_db():
    """数据库会话供给器：每个请求开一个 Session，请求结束自动关。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
        cred:HTTPAuthorizationCredentials|None=Depends(bearer),
)->dict:
    """从请求头的 token 里解出当前用户。
    抛 AppException(401) 的三种情况：没带 token / token 假 / token 是 refresh 型
    :return: dict {"user_id": 1, "username": "alice"}
    """
    #判断是否登录
    if cred is None:
        raise AppException("未登录",401)

    #已登录验证Token
    payload=decode_token(cred.credentials)
    if payload is None:
        raise AppException("token 无效或已过期",401)

    #必须是 access 型——refresh token 是用来换票的，不该有访问接口的权限
    if payload.get("type")!="access":
        raise AppException("token 类型错误", 401)

    return {"user_id":int(payload["sub"]),"username":payload["username"]}


