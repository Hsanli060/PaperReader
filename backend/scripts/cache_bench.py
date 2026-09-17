# -*- coding: utf-8 -*-
"""
缓存收益基准：冷构建 vs 缓存命中 的时延对比 + 会话观测计数快照。

跑（在 backend 目录下执行，或 PyCharm 直接运行本文件）：
    ../.venv/Scripts/python.exe scripts/cache_bench.py

说明：
    - 会真实调用一次 LLM 生成摘要（paper 3，约 10~30s）；其余请求全走缓存
    - 前提：本地 PG / Redis 已启动；paper 3 已索引
    - httpx 没装 socks 支持——脚本在导入任何东西之前先摘掉 socks 代理环境变量
"""
import os

# 环境怪癖：httpx 没装 socks 支持，留着 socks 代理变量在建客户端时会直接报错
for _k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(_k, None)

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # backend/，从任意目录运行都能 import app

import redis as redis_lib
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.security import create_access_token
from app.services.cache import llm_cache

PID, HOT = 3, 5
KEY = f"summary:{PID}"


def main() -> None:
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(1, 'demo')}"}
    r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)

    before = llm_cache.stats()
    r.delete(KEY)   # 清场：确保第一次请求是冷构建

    # 1) 冷构建（真实 LLM 生成）
    t0 = time.perf_counter()
    resp = client.get(f"/api/papers/{PID}/summary", headers=headers)
    cold_ms = (time.perf_counter() - t0) * 1000
    assert resp.status_code == 200, f"冷请求失败：{resp.status_code} {resp.text[:200]}"

    # 2) 命中路径（连续多次）
    hots = []
    for _ in range(HOT):
        t0 = time.perf_counter()
        resp = client.get(f"/api/papers/{PID}/summary", headers=headers)
        assert resp.status_code == 200
        hots.append((time.perf_counter() - t0) * 1000)

    after = llm_cache.stats()
    delta_builds = after["builds"] - before["builds"]
    warm = max(min(hots), 0.001)

    print(f"[冷构建] {cold_ms:,.0f} ms（含真实 LLM 生成）")
    print(f"[命中]   {HOT} 次：min {min(hots):.1f} / 中位 {sorted(hots)[HOT // 2]:.1f} ms")
    print(f"[提升]   约 {cold_ms / warm:,.0f} 倍（冷 → 热，按最快一次算）")
    print(f"[会话]   访问 {HOT + 1} 次 → 生成 {delta_builds} 次，省 {HOT + 1 - delta_builds} 次生成")
    print(f"[计数]   {after}")
    print(f"[TTL]    {r.ttl(KEY)}s（抖动落地验证：应落在 86400 ~ 95040）")


if __name__ == "__main__":
    main()
