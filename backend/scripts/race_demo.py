# -*- coding: utf-8 -*-
"""
竞态演示（批次③）：并发 POST /api/papers/arxiv 同一 arXiv ID —— 去重竞态修复的实测。

背景：add_by_arxiv 是"先查重、后插入"（check-then-insert），两步之间不是原子的。
单 worker 下没有真并发（事件循环里两步之间没有 await 切换点），竞态窗口出不来；
多 worker（或未来多实例）下两个进程才可能"同时查到没有 → 都去插"，
后提交的一笔会撞唯一索引 —— 修复前直接把 500 抛给前端。

用法（在 backend 目录下执行）：
    # 终端A：起多 worker 服务（竞态演示的前提）
    ../.venv/Scripts/python.exe -m uvicorn app.api.main:app --workers 4 --port 8011
    # 终端B：
    ../.venv/Scripts/python.exe scripts/race_demo.py --rounds 4                   # 常规：跑固定轮数
    ../.venv/Scripts/python.exe scripts/race_demo.py --seek-500 --max-rounds 10   # 修复前：复现 500
    ../.venv/Scripts/python.exe scripts/race_demo.py --rounds 4 --expect-clean    # 修复后：断言无 500

每轮：换一个新假 ID（0000.0000x）→ N 线程同障并发 POST → 统计状态码/duplicated → 查库行数。
收尾：删假 ID 论文（优先 DELETE API、直连库兜底）+ 删临时用户；打印清理复核结果。
"""
import argparse
import concurrent.futures
import os
import sys
import threading
import time
from pathlib import Path

# 本地演示，不经过系统代理（requests 会读这些环境变量）
for _k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

FAKE_IDS = [f"0000.0000{i}" for i in range(10)]  # 每轮一个新鲜假 ID（上限 10 轮）


def _login(base: str):
    """注册 + 登录临时用户 → (username, token)"""
    stamp = int(time.time())
    username, password = f"race_probe_{stamp}", "race-probe-123"
    r = requests.post(f"{base}/api/auth/register",
                      json={"username": username, "password": password}, timeout=15)
    r.raise_for_status()
    r = requests.post(f"{base}/api/auth/login",
                      json={"username": username, "password": password}, timeout=15)
    r.raise_for_status()
    return username, r.json()["access_token"]


def _fire_round(urls, token, fake_id, concurrency):
    """一轮并发 POST。返回每笔请求的 {status, duplicated, id, error} 列表"""
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def one(i):
        url = urls[i % len(urls)]
        s = requests.Session()
        s.trust_env = False
        s.headers["Authorization"] = f"Bearer {token}"
        entry = {"status": None, "duplicated": None, "id": None, "error": None}
        try:
            try:
                s.get(f"{url}/api/health", timeout=10)  # 预热：把建连成本挪出并发窗口
            except Exception:
                pass
            barrier.wait(timeout=60)
            r = s.post(f"{url}/api/papers/arxiv", json={"arxiv": fake_id}, timeout=30)
            entry["status"] = r.status_code
            try:
                body = r.json()
                entry["duplicated"] = body.get("duplicated")
                entry["id"] = body.get("id")
            except Exception:
                pass
        except Exception as e:  # 网络层/超时等
            entry["error"] = f"{type(e).__name__}: {e}"
            try:
                barrier.abort()
            except Exception:
                pass
        finally:
            s.close()
        with lock:
            results.append(entry)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(one, i) for i in range(concurrency)]
        concurrent.futures.wait(futs)
    return results


def _row_count(fake_id):
    from sqlalchemy import func, select

    from app.models.database import SessionLocal
    from app.models.orm import Paper

    db = SessionLocal()
    try:
        return db.scalar(select(func.count()).select_from(Paper).where(Paper.arxiv_id == fake_id))
    finally:
        db.close()


def _cleanup(urls, token, username, fake_ids):
    """删假 ID 论文 + 临时用户 → (api 删除数, 库兜底数, 残留数, 用户已删)"""
    from sqlalchemy import func, select

    from app.models.database import SessionLocal
    from app.models.orm import Paper, User

    db = SessionLocal()
    try:
        ids = [p.id for p in db.scalars(select(Paper).where(Paper.arxiv_id.in_(fake_ids))).all()]
    finally:
        db.close()

    api_deleted = 0
    for pid in ids:
        try:
            r = requests.delete(f"{urls[0]}/api/papers/{pid}",
                                headers={"Authorization": f"Bearer {token}"}, timeout=15)
            if r.status_code == 200:
                api_deleted += 1
        except Exception:
            pass

    # 兜底：直连库清漏网 + 复核残留 + 删临时用户
    db = SessionLocal()
    try:
        leftover = db.scalars(select(Paper).where(Paper.arxiv_id.in_(fake_ids))).all()
        for p in leftover:
            db.delete(p)
        if leftover:
            db.commit()
        remaining = db.scalar(select(func.count()).select_from(Paper)
                              .where(Paper.arxiv_id.in_(fake_ids)))
        user_deleted = False
        user = db.scalar(select(User).where(User.username == username))
        if user is not None:
            db.delete(user)
            db.commit()
            user_deleted = True
        return api_deleted, len(leftover), remaining, user_deleted
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description="并发去重竞态演示（批次③）")
    ap.add_argument("--base-url", action="append", default=None,
                    help="服务地址，可给多个；默认 http://127.0.0.1:8011")
    ap.add_argument("--rounds", type=int, default=4, help="常规模式轮数")
    ap.add_argument("--concurrency", type=int, default=8, help="每轮并发数")
    ap.add_argument("--seek-500", action="store_true", help="修复前模式：复现出 ≥1 个 500 即停")
    ap.add_argument("--max-rounds", type=int, default=10, help="seek-500 模式的轮数上限")
    ap.add_argument("--expect-clean", action="store_true", help="出现任何 500 则退出码 2")
    args = ap.parse_args()

    urls = args.base_url or ["http://127.0.0.1:8011"]
    print(f"[race_demo] 目标: {', '.join(urls)} | 每轮并发 {args.concurrency}")
    try:
        username, token = _login(urls[0])
    except Exception as e:
        print(f"[race_demo] 登录失败（服务起了吗？）：{type(e).__name__}: {e}")
        sys.exit(1)
    print(f"[race_demo] 临时用户: {username}（结束即删）\n")

    limit = args.max_rounds if args.seek_500 else args.rounds
    limit = min(limit, len(FAKE_IDS))
    used_ids, total_200, total_500, found = [], 0, 0, False

    for i in range(limit):
        fake_id = FAKE_IDS[i]
        used_ids.append(fake_id)
        results = _fire_round(urls, token, fake_id, args.concurrency)
        time.sleep(0.3)  # 给后台 fetch/pipeline 一小段落脚时间（会优雅失败）
        rows = _row_count(fake_id)

        statuses, dup_t, dup_f, errs = {}, 0, 0, 0
        for e in results:
            statuses[e["status"]] = statuses.get(e["status"], 0) + 1
            if e["error"]:
                errs += 1
            elif e["duplicated"] is True:
                dup_t += 1
            elif e["duplicated"] is False:
                dup_f += 1
        n500, n200 = statuses.get(500, 0), statuses.get(200, 0)
        total_500 += n500
        total_200 += n200
        mark = "   <<< 500!" if n500 else ""
        print(f"[轮{i + 1}/{limit}] {fake_id}  状态码={statuses}  "
              f"duplicated:True×{dup_t}/False×{dup_f}  行数={rows}{mark}")
        if errs:
            print(f"    请求异常×{errs}：{next(e['error'] for e in results if e['error'])}")
        if args.seek_500 and n500:
            found = True
            break

    n_req = len(used_ids) * args.concurrency
    print(f"\n[汇总] {len(used_ids)} 轮 × {args.concurrency} 并发 = {n_req} 笔请求："
          f"200×{total_200}，500×{total_500}")
    if args.seek_500 and not found:
        print("       ⚠️ 上限轮数内未复现 500（窗口很窄：可加大 --concurrency/--max-rounds 再试）")

    time.sleep(1.5)  # 等后台任务收尾
    api_del, fallback, remaining, user_del = _cleanup(urls, token, username, used_ids)
    print(f"[清理] DELETE API 删论文 {api_del} 行；库兜底 {fallback} 行；残留={remaining}；临时用户={user_del}")

    if args.expect_clean and total_500:
        print("[断言] FAILED：期望 0 个 500")
        sys.exit(2)
    if args.expect_clean:
        print("[断言] PASS：全 200、无 500")


if __name__ == "__main__":
    main()
