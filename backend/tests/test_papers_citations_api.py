"""
/papers/{id}/citations 回归测试：毒缓存（裸 list）命中必须 200，不能再 500。

背景 bug（2026-09-02）：路由把 LLM 提取的裸 list 直接 json.dumps 进 Redis，
而路由签名是 -> dict。缓存命中时 json.loads 出 list，撞上 FastAPI
response_model 校验 → 每次命中必 500（且 0.02 秒就炸，根本没碰 LLM）。

运行：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_papers_citations_api.py -v
前提：本地 PG / Redis 已启动；paper_id=1 存在；不打 LLM（只测缓存命中路径和纯函数）
"""
import json

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.paper.citation import _parse_citations
from app.security import create_access_token

client = TestClient(app)

# get_current_user 只解 token 不查库，任意 user_id 都能通过鉴权
HEADERS = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}

KEY = "citations:1"   # 真实存在的论文（Mamba），路由的存在性校验能过
POISON = json.dumps(  # 旧版代码写进 Redis 的形状：裸 list
    [{"ref": "[1]", "title": "Poisoned Cache Probe", "why": "回归测试探针"}],
    ensure_ascii=False,
)

# 测试要直连 Redis 存取毒缓存键（llm_cache 没有 delete/ttl 接口，测试里直接用 redis 客户端）
_r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)


@pytest.fixture
def poisoned_cache():
    """把 citations:1 换成毒缓存，测完恢复原值和原 TTL（不污染开发数据）"""
    original = _r.get(KEY)
    original_ttl = _r.ttl(KEY)
    _r.set(KEY, POISON, ex=60)
    yield
    if original is not None:
        _r.set(KEY, original, ex=max(original_ttl, 1))
    else:
        _r.delete(KEY)


# ==================== 接口层：缓存命中路径 ====================

def test_citations_poisoned_cache_returns_200(poisoned_cache):
    """命中裸 list 毒缓存必须 200 且包成 {items: [...]}——回归：曾必 500"""
    resp = client.get("/api/papers/1/citations", headers=HEADERS)
    assert resp.status_code == 200, f"毒缓存命中应 200，实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert isinstance(body, dict), f"响应必须是 dict，实际 {type(body)}"
    assert body["items"][0]["ref"] == "[1]"


def test_citations_cache_dict_shape_passthrough(poisoned_cache):
    """新版缓存格式 {"items": [...]} 命中时原样返回"""
    _r.set(KEY, json.dumps({"items": [{"ref": "[2]", "title": "New Format", "why": "w"}]},
                           ensure_ascii=False), ex=60)
    resp = client.get("/api/papers/1/citations", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["title"] == "New Format"


# ==================== 纯函数：_parse_citations 健壮性 ====================

def test_parse_citations_cited_by_us_dict():
    """prompt 约定的正常输出：{"cited_by_us": [...]} → list"""
    raw = '{"cited_by_us": [{"ref": "[1]", "title": "A", "why": "B"}]}'
    assert _parse_citations(raw)[0]["title"] == "A"


def test_parse_citations_bare_list():
    """模型偶尔不按约定输出裸 list 也能接住"""
    raw = '[{"ref": "[1]", "title": "A", "why": "B"}]'
    assert _parse_citations(raw)[0]["ref"] == "[1]"


def test_parse_citations_fenced():
    """带 ```json 代码块标记的输出要剥壳"""
    raw = '```json\n{"cited_by_us": [{"ref": "[1]", "title": "A", "why": "B"}]}\n```'
    assert _parse_citations(raw)[0]["ref"] == "[1]"


def test_parse_citations_garbage_returns_empty():
    """模型输出废话时返回 [] 而不是抛异常——曾直接炸成 500"""
    assert _parse_citations("抱歉，我无法从这段内容中提取引用关系。") == []
    assert _parse_citations("") == []


def test_parse_citations_wrong_field_returns_empty():
    """cited_by_us 字段缺失或不是 list 时返回 []，不让怪形状流到路由"""
    assert _parse_citations('{"other_field": 1}') == []
    assert _parse_citations('{"cited_by_us": "模型抽风输出了字符串"}') == []
