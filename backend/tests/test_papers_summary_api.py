"""
/papers/{id}/summary 回归测试：空壳摘要不写缓存 + 怪形状缓存命中不 500。

背景（2026-09-16）：summarizer._parse_json_block 遇到"根本不是 JSON"会抛异常，
但【合法空壳】能溜进来 —— {}、四段全"未提及"，甚至裸字符串/数字。
旧代码 json.dumps 之后一律缓存 24 小时，于是：
    - 空壳被钉死 → 详情页 24 小时都是空卡片，连重试机会都没有
    - 裸字符串被钉死 → 命中时 json.loads 出 str，撞上 -> dict 校验直接 500
      （和 citations 的毒缓存是同一类 bug）

运行：cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_papers_summary_api.py -v
前提：本地 PG / Redis 已启动；paper_id=3 存在；不打 LLM（工具被 monkeypatch）
"""
import json

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.security import create_access_token

client = TestClient(app)

# get_current_user 只解 token 不查库，任意 user_id 都能通过鉴权
HEADERS = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}

KEY = "summary:3"   # 真实存在的论文（Attention），路由的存在性校验能过
_r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)

FULL = {"problem": "P", "method": "M", "experiment": "E", "conclusion": "C"}
PLACEHOLDER = {"problem": "未提及", "method": "未提及",
               "experiment": "未提及", "conclusion": "未提及"}


@pytest.fixture
def summary_cache_key():
    """用真实存在的 paper 3 跑生成路径，测完恢复原值和原 TTL（不污染开发数据）"""
    original = _r.get(KEY)
    original_ttl = _r.ttl(KEY)
    _r.delete(KEY)
    yield KEY
    if original is not None:
        _r.set(KEY, original, ex=max(original_ttl, 1))
    else:
        _r.delete(KEY)


def _patch_tool(monkeypatch, return_value):
    """把 _summarize_paper_tool 换成固定返回值的假工具。

    路由里是函数内延迟 import（from app.agents.tools import ...），
    所以打在模块属性上能生效。
    """
    import app.agents.tools as tools

    async def fake_tool(paper_id, allowed_paper_ids=None):
        return return_value

    monkeypatch.setattr(tools, "_summarize_paper_tool", fake_tool)


# ==================== 空壳：返回占位符，但绝不写缓存 ====================

@pytest.mark.parametrize("empty", [
    {},                                                              # 模型直接输出 {}
    {"problem": "", "method": "", "experiment": "", "conclusion": ""},  # 四段空串
    {"problem": "未提及", "method": "未提及",
     "experiment": "未提及", "conclusion": "未提及"},                    # 四段全占位词
    "纯字符串",                                                       # 解析器可能返回非 dict
    123,
], ids=["空对象", "空串", "全未提及", "裸字符串", "数字"])
def test_summary_empty_result_not_cached(summary_cache_key, monkeypatch, empty):
    """没读出东西：接口照常 200 给四段占位符，但不写缓存——下次进详情页还能重试"""
    _patch_tool(monkeypatch, empty)
    resp = client.get("/api/papers/3/summary", headers=HEADERS)
    assert resp.status_code == 200, f"空壳应 200，实际 {resp.status_code}: {resp.text}"
    assert resp.json() == PLACEHOLDER, "空态要给四个键，前端(PaperSummary)才渲染得出来"
    assert _r.get(summary_cache_key) is None, "空壳不该写进缓存，否则空态被钉死 24 小时"


def test_summary_nonempty_result_cached(summary_cache_key, monkeypatch):
    """有内容时正常写缓存"""
    _patch_tool(monkeypatch, FULL)
    resp = client.get("/api/papers/3/summary", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == FULL
    assert json.loads(_r.get(summary_cache_key)) == FULL, "有内容要落缓存"


# ==================== 缓存命中路径 ====================

def test_summary_poisoned_cache_returns_200(summary_cache_key):
    """命中历史遗留的裸字符串缓存必须 200——回归：json.loads 出 str 撞 -> dict 校验曾必 500"""
    _r.set(summary_cache_key, json.dumps("纯字符串", ensure_ascii=False), ex=60)
    resp = client.get("/api/papers/3/summary", headers=HEADERS)
    assert resp.status_code == 200, f"怪形状缓存命中应 200，实际 {resp.status_code}: {resp.text}"
    assert resp.json() == PLACEHOLDER


def test_summary_valid_cache_passthrough(summary_cache_key):
    """正常缓存命中：原样返回，不碰 LLM"""
    _r.set(summary_cache_key, json.dumps(FULL, ensure_ascii=False), ex=60)
    resp = client.get("/api/papers/3/summary", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == FULL
