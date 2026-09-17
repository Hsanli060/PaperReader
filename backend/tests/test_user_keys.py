# -*- coding: utf-8 -*-
"""批①：用户自带 API Key —— 加密存储 / 掩码回显 / 严格模式门禁 / 测试接口

跑：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_user_keys.py -v
前提：本地 PG 已启动；不调真实 LLM/嵌入（测试接口的两个探针被 fake 替换）。

约定：所有服务端测试默认由 conftest 打开回退开关（=改造前行为）；
本文件测"严格模式"的用例在函数体内显式 monkeypatch settings.ALLOW_DEFAULT_KEY=False。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.main import app
from app.api.routes import user as user_routes
from app.config import settings
from app.models.database import SessionLocal
from app.models.orm import Conversation, Message, Paper, User, user_papers
from app.security import create_access_token
from app.services.user_keys import decrypt_key, encrypt_key, mask_tail

client = TestClient(app)


# ---------- 工具与夹具 ----------

@pytest.fixture
def fresh_user():
    """建一个专用测试用户（不碰真实账号），测完连人带会话删干净"""
    db = SessionLocal()
    try:
        user = User(username=f"keytest_{uuid.uuid4().hex[:8]}", password_hash="x")  # 占位哈希，不走登录
        db.add(user)
        db.commit()
        uid = user.id
    finally:
        db.close()

    yield uid

    db = SessionLocal()
    try:
        conv_ids = list(db.scalars(select(Conversation.id).where(Conversation.user_id == uid)))
        if conv_ids:
            db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
            for conv in db.scalars(select(Conversation).where(Conversation.id.in_(conv_ids))):
                db.delete(conv)          # ORM 级删除：会话×论文关联表一并清
        db.execute(delete(user_papers).where(user_papers.c.user_id == uid))   # 批②：归属行
        row = db.get(User, uid)
        if row is not None:
            db.delete(row)
        db.commit()
    finally:
        db.close()


def _headers(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(uid, 'keytest')}"}


def _db_row(uid: int) -> User:
    db = SessionLocal()
    try:
        return db.get(User, uid)
    finally:
        db.close()


def _any_paper_id() -> int | None:
    db = SessionLocal()
    try:
        return db.scalar(select(Paper.id).limit(1))
    finally:
        db.close()


def _ensure_member(uid: int, pid: int) -> None:
    """批②：读论文接口要求成员关系——把测试用户加成该论文成员
    （幂等；该行会随用户清理一起删掉）"""
    db = SessionLocal()
    try:
        db.execute(pg_insert(user_papers).values(user_id=uid, paper_id=pid).on_conflict_do_nothing())
        db.commit()
    finally:
        db.close()


# ---------- 1. 加密与掩码（纯函数） ----------

def test_encrypt_roundtrip_and_mask():
    plain = "sk-abcdef1234567890xyz"
    token = encrypt_key(plain)
    assert plain not in token                 # 密文里不出现明文
    assert token != plain
    assert decrypt_key(token) == plain        # 解回来一致
    assert decrypt_key("not-a-valid-token") is None   # 坏数据 → None（视为未配置）
    assert mask_tail(plain) == "…0xyz"
    assert mask_tail(None) is None


# ---------- 2. 保存 / 回显 / 清除 ----------

def test_save_masks_and_stores_ciphertext(fresh_user):
    uid = fresh_user
    resp = client.put("/api/user/keys", headers=_headers(uid),
                      json={"llm_api_key": "sk-llm-test-0001", "embedding_api_key": "sk-emb-test-0002"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["llm"] == {"configured": True, "tail": "…0001", "base_url": None}
    assert data["embedding"] == {"configured": True, "tail": "…0002", "base_url": None}
    assert "sk-llm-test-0001" not in resp.text   # 响应绝不回显全文

    # 库里是密文：不等于明文，但能解回明文
    row = _db_row(uid)
    assert row.llm_api_key != "sk-llm-test-0001"
    assert decrypt_key(row.llm_api_key) == "sk-llm-test-0001"

    # GET 只回尾号
    got = client.get("/api/user/keys", headers=_headers(uid)).json()
    assert got["llm"]["tail"] == "…0001"
    assert got["embedding"]["tail"] == "…0002"


def test_partial_update_and_clear(fresh_user):
    uid = fresh_user
    client.put("/api/user/keys", headers=_headers(uid), json={"llm_api_key": "sk-a-1111"})

    # 只改嵌入：llm 保持不动
    data = client.put("/api/user/keys", headers=_headers(uid),
                      json={"embedding_api_key": "sk-b-2222"}).json()
    assert data["llm"]["configured"] is True and data["embedding"]["configured"] is True

    # 空串 = 清除这一项；缺省字段 = 不动
    data = client.put("/api/user/keys", headers=_headers(uid), json={"llm_api_key": ""}).json()
    assert data["llm"]["configured"] is False
    assert data["embedding"]["configured"] is True


# ---------- 3. 严格模式门禁（409 + NO_API_KEY） ----------

def test_chat_blocked_without_keys_in_strict_mode(fresh_user, monkeypatch):
    uid = fresh_user
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_KEY", False)   # 覆盖 conftest 的回退

    resp = client.post("/api/chat", headers=_headers(uid), json={"question": "你好"})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "NO_API_KEY"
    assert set(body["detail"]["missing"]) == {"llm", "embedding"}

    # 被拦的请求不留任何数据（会话没被创建）
    db = SessionLocal()
    try:
        convs = list(db.scalars(select(Conversation.id).where(Conversation.user_id == uid)))
    finally:
        db.close()
    assert convs == [], "被门禁拦下的请求不该留下空会话"


def test_chat_reports_exactly_missing(fresh_user, monkeypatch):
    uid = fresh_user
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_KEY", False)
    client.put("/api/user/keys", headers=_headers(uid), json={"llm_api_key": "sk-only-llm"})

    resp = client.post("/api/chat", headers=_headers(uid), json={"question": "hi"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["missing"] == ["embedding"]


def test_chat_gate_passes_once_keys_saved(fresh_user, monkeypatch):
    """配上两把 key 后放行：走到假 agent 正常出流（不调真 LLM）"""
    uid = fresh_user
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_KEY", False)
    client.put("/api/user/keys", headers=_headers(uid),
               json={"llm_api_key": "sk-llm-3333", "embedding_api_key": "sk-emb-4444"})

    from app.agents.react_agent import ReactAgent

    async def fake_stream(self, user_input, allowed_paper_ids=None):
        yield {"type": "content", "text": "pong"}

    monkeypatch.setattr(ReactAgent, "run_stream_async", fake_stream)
    resp = client.post("/api/chat", headers=_headers(uid), json={"question": "ping"})
    assert resp.status_code == 200, resp.text
    assert "pong" in resp.text


def test_summary_blocked_without_keys_strict(fresh_user, monkeypatch):
    """非流式接口同样被拦（summary 要调 LLM）"""
    uid = fresh_user
    pid = _any_paper_id()
    if pid is None:
        pytest.skip("本地库还没有论文，跳过")
    _ensure_member(uid, pid)   # 批②：想走到 key 门禁，先得是"我库里的"论文
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_KEY", False)

    resp = client.get(f"/api/papers/{pid}/summary", headers=_headers(uid))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "NO_API_KEY"


def test_add_paper_blocked_without_embedding_key(fresh_user, monkeypatch):
    """加新论文（走下载→索引流水线）没嵌入 key → 409；且不落 pending 记录"""
    uid = fresh_user
    monkeypatch.setattr(settings, "ALLOW_DEFAULT_KEY", False)

    resp = client.post("/api/papers/arxiv", headers=_headers(uid), json={"arxiv": "9999.99999"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["missing"] == ["embedding"]

    db = SessionLocal()
    try:
        leftover = db.scalar(select(Paper.id).where(Paper.arxiv_id == "9999.99999"))
    finally:
        db.close()
    assert leftover is None, "门禁拦下的添加不该留下 pending 记录"


# ---------- 4. 测试接口（探针替换，不调真实服务） ----------

def test_keys_test_endpoint_reports_per_provider(fresh_user, monkeypatch):
    uid = fresh_user
    calls: dict = {}

    async def fake_llm(key, base_url=None):
        calls["llm"] = (key, base_url)
        return {"ok": True}

    async def fake_emb(key, base_url=None):
        calls["emb"] = (key, base_url)
        return {"ok": False, "error": "boom"}

    monkeypatch.setattr(user_routes, "_test_llm_key", fake_llm)
    monkeypatch.setattr(user_routes, "_test_embedding_key", fake_emb)

    resp = client.post("/api/user/keys/test", headers=_headers(uid),
                       json={"llm_api_key": "sk-llm-9999", "embedding_api_key": "sk-emb-8888",
                             "llm_base_url": "https://llm.example.com/v1/",
                             "embedding_base_url": "https://emb.example.com/v1"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["llm"]["ok"] is True
    assert data["embedding"] == {"ok": False, "error": "boom"}
    # key 与地址都直达探针；地址尾斜杠被规整；不落库（本用例没调 PUT）
    assert calls["llm"] == ("sk-llm-9999", "https://llm.example.com/v1")
    assert calls["emb"] == ("sk-emb-8888", "https://emb.example.com/v1")
    assert _db_row(uid).llm_api_key is None


def test_keys_test_endpoint_without_anything(fresh_user):
    """没输入、没保存 → 明确"未配置"。
    ★ 刻意不关回退开关（conftest 默认 True）——证明测试接口根本不看回退，
      绝不会拿服务器 key 冒充"测试成功"（回归：曾把回退 key 也拿去测）"""
    uid = fresh_user
    data = client.post("/api/user/keys/test", headers=_headers(uid), json={}).json()
    assert data["llm"] == {"ok": False, "error": "未配置——先粘贴你的 Key 再测"}
    assert data["embedding"] == {"ok": False, "error": "未配置——先粘贴你的 Key 再测"}


def test_keys_test_endpoint_prefers_typed_then_stored(fresh_user, monkeypatch):
    """测试目标的取用顺序：输入框的值 > 我自己已保存的 > 未配置"""
    uid = fresh_user
    client.put("/api/user/keys", headers=_headers(uid), json={"llm_api_key": "sk-stored-1"})

    calls: dict = {}

    async def fake_llm(key, base_url=None):
        calls["llm"] = key
        return {"ok": True}

    monkeypatch.setattr(user_routes, "_test_llm_key", fake_llm)

    # 没传值：测已保存的那个
    data = client.post("/api/user/keys/test", headers=_headers(uid), json={}).json()
    assert calls["llm"] == "sk-stored-1"
    assert data["llm"]["ok"] is True
    assert data["embedding"] == {"ok": False, "error": "未配置——先粘贴你的 Key 再测"}

    # 传了值：优先测传的（不落库）
    client.post("/api/user/keys/test", headers=_headers(uid), json={"llm_api_key": "sk-typed-2"})
    assert calls["llm"] == "sk-typed-2"


def test_base_url_roundtrip_and_clear(fresh_user):
    """自带服务地址：明文存、可回显、可单独清除（不动 key）；尾斜杠被规整"""
    uid = fresh_user
    data = client.put("/api/user/keys", headers=_headers(uid), json={
        "llm_api_key": "sk-x-0001",
        "llm_base_url": "https://api.moonshot.cn/v1/",   # 带尾斜杠
    }).json()
    assert data["llm"]["base_url"] == "https://api.moonshot.cn/v1"
    assert data["llm"]["configured"] is True

    # GET 回显完整地址（非机密）——key 仍然只有尾号
    got = client.get("/api/user/keys", headers=_headers(uid)).json()
    assert got["llm"]["base_url"] == "https://api.moonshot.cn/v1"
    assert got["llm"]["tail"] == "…0001"

    # 只清地址不动 key（字段缺省=不动）
    data = client.put("/api/user/keys", headers=_headers(uid), json={"llm_base_url": ""}).json()
    assert data["llm"]["base_url"] is None
    assert data["llm"]["configured"] is True


def test_base_url_validation(fresh_user):
    """地址必须 http(s):// 开头——早点 422，别等调用时才炸"""
    uid = fresh_user
    assert client.put("/api/user/keys", headers=_headers(uid),
                      json={"llm_base_url": "ftp://x"}).status_code == 422
    assert client.put("/api/user/keys", headers=_headers(uid),
                      json={"embedding_base_url": "随便写"}).status_code == 422


def test_client_cache_identity_includes_base_url():
    """客户端缓存的身份 = (地址 + key)：地址不同必须拿到不同客户端（防串台）"""
    from app.services.embedding import get_client as get_emb_client
    from app.services.llm import get_async_client

    a1 = get_async_client("k-unit-1", "https://a.example.com/v1")
    a2 = get_async_client("k-unit-1", "https://a.example.com/v1")
    b = get_async_client("k-unit-1", "https://b.example.com/v1")
    assert a1 is a2, "同 地址+key 应复用同一客户端"
    assert a1 is not b, "地址不同 = 不同客户端（防串台）"

    ea = get_emb_client("k-unit-2", "https://a.example.com/v1")
    eb = get_emb_client("k-unit-2", "https://b.example.com/v1")
    assert ea is not eb
