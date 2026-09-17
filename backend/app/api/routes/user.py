"""用户设置路由：/api/user/keys —— 自带 API Key 的查看 / 保存 / 测试（批①）

安全三原则（写死在这个文件里）：
    1. 存库 = Fernet 密文（services/user_keys.py），库里永不出现明文
    2. 回显 = 只有尾号（"…3f9a"）；完整 key 永不回传（连设置页 input 也不回填全文）
    3. 测试 = 走真实最小调用（LLM: /models 列表，零 token 费用；嵌入: 1 条 ping），
       只回 ok / error，绝不回显 key 本身
"""
import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_db
from app.middleware.error_handler import AppException
from app.models.orm import User
from app.services.user_keys import decrypt_key, encrypt_key, mask_tail

router = APIRouter(prefix="/user", tags=["用户设置"])


class KeysIn(BaseModel):
    """保存入参：字段缺省(None)=不动这一项；空串 ""=清除；非空=保存。
    key 加密存储；base_url 非机密、明文存（要能回显），非空时须以 http(s):// 开头"""
    llm_api_key: str | None = Field(default=None, max_length=512)
    embedding_api_key: str | None = Field(default=None, max_length=512)
    llm_base_url: str | None = Field(default=None, max_length=512)
    embedding_base_url: str | None = Field(default=None, max_length=512)


class KeyTestIn(BaseModel):
    """测试入参：给了就测它（不落库）；没给就测库里已保存的。
    base_url 可选：测试时一并使用（留空=已保存的 / 服务器默认）"""
    llm_api_key: str | None = Field(default=None, max_length=512)
    embedding_api_key: str | None = Field(default=None, max_length=512)
    llm_base_url: str | None = Field(default=None, max_length=512)
    embedding_base_url: str | None = Field(default=None, max_length=512)


def _status(row: User) -> dict:
    """给前端的配置状态：key 只有 是否已配+尾号；base_url 非机密，可整体回显"""
    llm = decrypt_key(row.llm_api_key)
    emb = decrypt_key(row.embedding_api_key)
    return {
        "llm": {"configured": bool(llm), "tail": mask_tail(llm),
                "base_url": row.llm_base_url},
        "embedding": {"configured": bool(emb), "tail": mask_tail(emb),
                      "base_url": row.embedding_base_url},
    }


async def _test_llm_key(key: str, base_url: str | None = None) -> dict:
    """最小只读调用验证 LLM key：拉一次模型列表（不产生 token 费用）"""
    from app.services.llm import get_async_client
    try:
        client = get_async_client(key, base_url)
        await asyncio.wait_for(client.models.list(), timeout=12)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


async def _test_embedding_key(key: str, base_url: str | None = None) -> dict:
    """最小调用验证嵌入 key：embed 一条 "ping"（费用可忽略）"""
    from app.config import settings
    from app.services.embedding import get_client

    def _ping():
        get_client(key, base_url).embeddings.create(model=settings.EMBEDDING_MODEL, input=["ping"])

    try:
        await asyncio.wait_for(asyncio.to_thread(_ping), timeout=15)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


@router.get("/keys")
def get_keys(user: dict = Depends(get_current_user), db=Depends(get_db)) -> dict:
    """我的 key 配置状态（只有尾号，绝无全文）"""
    row = db.get(User, user["user_id"])
    if row is None:
        raise AppException("用户不存在", 404)
    return _status(row)


@router.put("/keys")
def save_keys(data: KeysIn, user: dict = Depends(get_current_user), db=Depends(get_db)) -> dict:
    """保存（加密）或清除某项；返回最新状态。清除 = 传空字符串"""
    row = db.get(User, user["user_id"])
    if row is None:
        raise AppException("用户不存在", 404)
    if data.llm_api_key is not None:
        row.llm_api_key = encrypt_key(data.llm_api_key.strip()) if data.llm_api_key.strip() else None
    if data.embedding_api_key is not None:
        row.embedding_api_key = (
            encrypt_key(data.embedding_api_key.strip()) if data.embedding_api_key.strip() else None
        )
    # base_url：None=不动；""=清除；非空=校验（http(s)://）并去掉尾斜杠
    if data.llm_base_url is not None:
        v = data.llm_base_url.strip().rstrip("/")
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise AppException("LLM 服务地址需以 http:// 或 https:// 开头", 422)
        row.llm_base_url = v or None
    if data.embedding_base_url is not None:
        v = data.embedding_base_url.strip().rstrip("/")
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise AppException("嵌入服务地址需以 http:// 或 https:// 开头", 422)
        row.embedding_base_url = v or None
    db.commit()
    return _status(row)


@router.post("/keys/test")
async def test_keys(data: KeyTestIn, user: dict = Depends(get_current_user), db=Depends(get_db)) -> dict:
    """测试 key 有效性：优先测传入值，没传则测"我自己已保存的"。
    ★ 不用服务器回退（ALLOW_DEFAULT_KEY）——这个按钮测的是"你的 key"：
    没输入也没保存时说"未配置"，绝不拿服务器 key 冒充测试成功。
    两端各自独立返回 {ok, error?}；不落库、不回显。"""
    row = db.get(User, user["user_id"])
    if row is None:
        raise AppException("用户不存在", 404)
    stored_llm = decrypt_key(row.llm_api_key)
    stored_emb = decrypt_key(row.embedding_api_key)
    llm_key = (data.llm_api_key or "").strip() or stored_llm
    emb_key = (data.embedding_api_key or "").strip() or stored_emb
    # 地址同理：输入框的值 > 已保存的（都没有=None，自动落服务器默认）
    llm_url = (data.llm_base_url or "").strip().rstrip("/") or row.llm_base_url
    emb_url = (data.embedding_base_url or "").strip().rstrip("/") or row.embedding_base_url

    not_set = {"ok": False, "error": "未配置——先粘贴你的 Key 再测"}
    result = {"llm": dict(not_set), "embedding": dict(not_set)}
    if llm_key:
        result["llm"] = await _test_llm_key(llm_key, llm_url)
    if emb_key:
        result["embedding"] = await _test_embedding_key(emb_key, emb_url)
    return result
