"""
安全工具箱：密码哈希（bcrypt）+ JWT 生成/校验（python-jose）
谁调用它：routes/auth.py（注册/登录/刷新）、api/deps.py（验 token）

密码方案选型说明：
    bcrypt 是业界标准（Django 默认方案之一）。本可以走 passlib 中转，
    但 passlib 1.7.4 和新版 bcrypt 5.0 不兼容（实测会报错），
    所以这里直接用 bcrypt 库本体——它只有两个常用函数，非常简单
"""

import bcrypt
from datetime import datetime,timedelta,timezone

from jose import jwt
from jose.exceptions import JWTError

from app.config import settings

# ==================== 密码哈希 ====================
def hash_password(plain:str)->str:
    """明文密码 → 哈希乱码（注册时调用，存进 users.password_hash）

    :param plain:str 明文密码
    :return:str 哈希乱码
    """
    #哈希处理hashpw（密码字节，随机盐）.字符串化
    return bcrypt.hashpw(plain.encode("utf-8"),bcrypt.gensalt()).decode("utf-8")

def verify_password(plain:str,hashed:str)->bool:
    """校验密码：登录时调用：用户输入的明文 vs 库里存的哈希，对得上返回 True

    :param plain: str 明文密码
    :param hashed: str 哈希乱码
    :return: bool 密码是否正确
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"),hashed.encode("utf-8"))
    except ValueError:
        return False

# ==================== JWT 生成 / 校验 ====================
def _make_token(user_id:int,username:str,token_type:str,minutes:int)->str:
    """生成 token 函数，返回一串长长的字符串凭证"""
    payload={
        "sub":str(user_id),      # "subject"：给谁发的通行证。JWT 标准字段，按规范存字符串
        "username":username,
        "type":token_type,       # "access" / "refresh"——短期凭证\长期凭证
        "exp":datetime.now(timezone.utc)+timedelta(minutes=minutes)# 创建有效时间 现在时刻+有效时间
    }
    return jwt.encode(payload,settings.JWT_SECRET,algorithm=settings.JWT_ALGORITHM)

def create_access_token(user_id:int,username:str)->str:
    """生成短期 Token

    :return: str 短期Token（凭证）
    """
    return _make_token(user_id,username,"access",settings.ACCESS_TOKEN_EXPIRE_MINUTES)

def create_refresh_token(user_id:int,username:str)->str:
    """生成长期 Token

    :return: str 长期 Token
    """
    # refresh 期限是"天"，换算成分钟传给底座（7*24*60）
    return _make_token(user_id,username,"refresh",settings.REFRESH_TOKEN_EXPIRE_DAYS*24*60)


def decode_token(token:str)->dict|None:
    """解析 Token 信息

    :param token:str 凭证
    :return: None | dict 该Token包含的信息{"sub": "1", "username": "zhangsan", "type": "access", ...}
    """
    try:
        # algorithms 必须显式指定：防止攻击者把头部算法改成 "none" 绕过签名
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None