"""
认证路由：/api/auth/register、/api/auth/login、/api/auth/refresh
谁调用它：main.py 里 app.include_router(...) 把它挂上路由树
"""
from fastapi import Depends, APIRouter
from pydantic import BaseModel,Field
from sqlalchemy import select

from app.api.deps import get_current_user, get_db
from app.middleware.error_handler import AppException
from app.models.database import SessionLocal
from app.models.orm import User
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router=APIRouter(prefix="/auth",tags=["认证"])

class RegisterIn(BaseModel):
    #注册。不合格直接被 FastAPI 拦下返回 422
    username:str=Field(min_length=3,max_length=50)
    password:str=Field(min_length=6,max_length=72)

class LoginIn(BaseModel):
    username:str
    password:str

class RefreshIn(BaseModel):
    refresh_token:str

@router.post("/register")
def register(data:RegisterIn,db=Depends(get_db))->dict:
    """注册：查重 → 哈希 → 落库。返回公开信息，绝不含密码哈希"""
    exists=db.scalar(select(User).where(User.username==data.username))
    if exists:
        raise AppException("用户名已存在",409)
    user=User(username=data.username,password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    return {"id":user.id,"username":user.username}

@router.post("/login")
def login(data:LoginIn,db=Depends(get_db))->dict:
    """登录：查用户 → 验密码 → 发一对 token"""
    user=db.scalar(select(User).where(User.username==data.username))
    if user is None or not verify_password(data.password,user.password_hash):
        raise AppException("用户名或密码错误", 401)
    return{
        "access_token": create_access_token(user.id, user.username),
        "refresh_token": create_refresh_token(user.id, user.username),
    }

@router.post("/refresh")
def refresh(data:RefreshIn,db=Depends(get_db))->dict:
    """用 refresh token 换一对新 token。access 过期时前端自动调它，用户无感"""
    payload=decode_token(data.refresh_token)
    # 双重校验：验签通过 + 类型必须是 refresh（防 access 冒充，我实测过：冒充会被拒）
    if payload is None or payload.get("type")!="refresh":
        raise AppException("refresh token 无效", 401)

    # token 有效但用户可能已被删除——重新查一次库确认人还在
    user=db.get(User,int(payload["sub"]))
    if user is None:
        raise AppException("用户不存在", 401)
    return{
        "access_token": create_access_token(user.id, user.username),
        "refresh_token": create_refresh_token(user.id, user.username),
    }