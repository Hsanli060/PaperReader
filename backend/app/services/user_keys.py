"""用户级 API Key：加密存储 + 请求级解析（服务端注入）

设计哲学（和 allowed_paper_ids 一脉相承）：key 的归属由服务端说了算——
请求进来先解析"当前用户自己的 key"，放进 contextvar；后续深处（agent / 工具 /
检索链路）一律从 contextvar 读，不信任任何客户端传参。

三件事：
    1. Fernet 对称加密：库里只存密文，密钥从 JWT_SECRET 派生（不新增环境变量；
       换了 JWT_SECRET 旧密文解不开 = 视为未配置，用户重填即可）
    2. contextvar：请求进行中"当前生效的 key"（线程/任务隔离，天然并发安全）
    3. 回退开关 ALLOW_DEFAULT_KEY：默认 false（严格）——没配 key 就 409；
       本地开发在 .env 设 true 回退用服务器 key

解析优先级：用户自己的 key → （开关开启时）.env 默认 key → 无（拒绝服务）
"""
import base64
import contextvars
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from app.config import settings
from app.middleware.error_handler import AppException


@dataclass(frozen=True)
class UserKeys:
    """一次请求解析出的"生效 key + 服务地址"（None=未配置 / 未自定义）"""
    llm: str | None
    embedding: str | None
    llm_base_url: str | None = None        # 自带地址（留空=服务端默认）
    embedding_base_url: str | None = None


# ---------- 加密（库里只存密文） ----------

def _fernet() -> Fernet:
    """从 JWT_SECRET 派生 Fernet 密钥（sha256 → 32 字节 → urlsafe base64）"""
    digest = hashlib.sha256(("paperreader:user-keys:" + settings.JWT_SECRET).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(plain: str) -> str:
    """明文 → Fernet 密文（存库用）"""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_key(token: str | None) -> str | None:
    """密文 → 明文；解不开（换过 JWT_SECRET / 数据损坏）返回 None=视为未配置"""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.warning("用户 API Key 解密失败（JWT_SECRET 换过？）——已按'未配置'处理")
        return None


def mask_tail(plain: str | None) -> str | None:
    """给前端的展示形态：只给尾号（sk-…3f9a）；永远不出现完整 key"""
    if not plain:
        return None
    return "…" + plain[-4:] if len(plain) > 8 else "已配置"


# ---------- contextvar：请求进行中"当前生效的 key" ----------

_current: contextvars.ContextVar[UserKeys | None] = contextvars.ContextVar(
    "current_user_keys", default=None
)


def set_current_keys(keys: UserKeys) -> None:
    """请求入口调用（路由层，streaming 起流之前）"""
    _current.set(keys)


def get_current_keys() -> UserKeys | None:
    return _current.get()


def current_llm_key() -> str | None:
    keys = _current.get()
    return keys.llm if keys else None


def current_embedding_key() -> str | None:
    keys = _current.get()
    return keys.embedding if keys else None


def current_llm_base_url() -> str | None:
    keys = _current.get()
    return keys.llm_base_url if keys else None


def current_embedding_base_url() -> str | None:
    keys = _current.get()
    return keys.embedding_base_url if keys else None


# ---------- 解析（请求入口调用） ----------

def load_user_keys(db, user_id: int) -> UserKeys:
    """从库里读该用户的 key（解密）。没配且开关开启 → 回退 .env 默认"""
    from app.models.orm import User  # 延迟导入：避免 models ↔ services 循环

    user = db.get(User, user_id)
    llm = decrypt_key(user.llm_api_key) if user is not None else None
    embedding = decrypt_key(user.embedding_api_key) if user is not None else None
    llm_base = (user.llm_base_url or None) if user is not None else None
    emb_base = (user.embedding_base_url or None) if user is not None else None

    if settings.ALLOW_DEFAULT_KEY:
        llm = llm or settings.LLM_API_KEY
        embedding = embedding or settings.EMBEDDING_API_KEY

    return UserKeys(llm=llm, embedding=embedding,
                    llm_base_url=llm_base, embedding_base_url=emb_base)


def ensure_keys(db, user_id: int, *, need_llm: bool = False, need_embedding: bool = False) -> UserKeys:
    """要求必备的 key 都就位，否则 409（detail.code=NO_API_KEY 给前端跳设置页用）"""
    keys = load_user_keys(db, user_id)
    missing: list[str] = []
    if need_llm and not keys.llm:
        missing.append("llm")
    if need_embedding and not keys.embedding:
        missing.append("embedding")
    if missing:
        names = "、".join({"llm": "LLM", "embedding": "嵌入"}[m] for m in missing)
        raise AppException(
            f"尚未配置{names} API Key——请到「设置」页添加你自己的 Key 后再使用",
            409,
            detail={"code": "NO_API_KEY", "missing": missing},
        )
    return keys
