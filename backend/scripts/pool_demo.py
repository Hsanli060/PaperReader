# -*- coding: utf-8 -*-
"""
连接池演示（批次④）：同载荷下"调优池 vs 默认池"对照 + 连接回收演示。

用法（在 backend 目录下执行，需 PG 在跑）：
    ../.venv/Scripts/python.exe scripts/pool_demo.py

三段：
    1) 参数核对：读取 app 实际 engine 的连接池参数（确认批次④已生效）
    2) 同载荷对照：25 线程并发、各持连接 1 秒
         - 调优池（app engine，10+20=30 上限） vs 默认池（5+10=15 上限）
         - 指标：峰值借出/溢出、等待时长（中位/最大）、池超时数、总耗时
    3) 回收演示：pool_recycle=1s 的演示引擎 → 隔 2.2s 再借，观察后端 PID 变化
"""
import os
import sys
import threading
import time
from pathlib import Path

for k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy
from sqlalchemy import create_engine, text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.config import settings
from app.models.database import engine as app_engine

N_THREADS = 25
HOLD_S = 1.0


def cfg_str(pool):
    """读连接池参数（演示脚本，少量私有属性 + getattr 兜底）"""
    return (
        f"pool_size={pool.size()}"
        f" max_overflow={getattr(pool, '_max_overflow', '?')}"
        f" timeout={getattr(pool, '_timeout', '?')}"
        f" recycle={getattr(pool, '_recycle', '?')}"
        f" lifo={getattr(getattr(pool, '_pool', None), 'use_lifo', '?')}"  # lifo 在内层 Queue 上
    )


def loadtest(engine, label, n=N_THREADS, hold=HOLD_S):
    """n 个线程并发借连接、各持有 hold 秒；采样峰值 + 统计等待与超时"""
    barrier = threading.Barrier(n)
    waits, errors = [], []
    lock = threading.Lock()
    peak = {"co": 0, "ov": 0, "status": ""}
    stop = threading.Event()

    def sampler():
        pool = engine.pool
        while not stop.is_set():
            try:
                co, ov = pool.checkedout(), pool.overflow()
            except Exception:
                return
            if co > peak["co"]:
                peak["co"], peak["ov"], peak["status"] = co, ov, pool.status()
            time.sleep(0.02)

    def worker():
        try:
            barrier.wait(timeout=60)
            t0 = time.perf_counter()
            conn = engine.connect()
            waited = time.perf_counter() - t0
            try:
                conn.execute(text(f"SELECT pg_sleep({hold})"))
            finally:
                conn.close()
            with lock:
                waits.append(waited)
        except Exception as e:  # 池超时/其它异常都记账
            tag = "池借等超时" if isinstance(e, PoolTimeoutError) else type(e).__name__
            with lock:
                errors.append(f"{tag}: {e}")

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    st = threading.Thread(target=sampler, daemon=True)
    st.start()
    for t in threads:
        t.join()
    stop.set()
    st.join(timeout=2)
    wall = time.perf_counter() - t0

    ws = sorted(waits)
    med = ws[len(ws) // 2] * 1000 if ws else -1
    mx = ws[-1] * 1000 if ws else -1
    slow = sum(1 for w in ws if w > 0.5)

    print(f"\n[{label}]")
    print(f"  引擎参数: {cfg_str(engine.pool)}")
    print(f"  峰值借出: {peak['co']}（其中溢出 {peak['ov']}）")
    print(f"  峰值状态: {peak['status']}")
    print(f"  等待时长: 中位 {med:.1f} ms / 最大 {mx:.1f} ms；等待>500ms 的线程数: {slow}")
    print(f"  池超时数: {len(errors)}" + (f"  异常明细: {errors[:3]}" if errors else ""))
    print(f"  载荷后状态: {engine.pool.status()}")
    print(f"  总耗时: {wall:.2f} s（{n} 线程 × 持连接 {hold}s）")
    return {"peak": peak["co"], "wait_max": mx, "slow": slow, "timeouts": len(errors), "wall": wall}


def main():
    print("=" * 72)
    print(f"第 1 段 · 参数核对（SQLAlchemy {sqlalchemy.__version__}）")
    print("=" * 72)
    print(f"app engine : {cfg_str(app_engine.pool)}   ← 批次④调优后")
    default_engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    print(f"对照 engine: {cfg_str(default_engine.pool)}   ← SQLAlchemy 默认")

    print()
    print("=" * 72)
    print(f"第 2 段 · 同载荷对照（{N_THREADS} 线程并发，各持连接 {HOLD_S}s）")
    print("=" * 72)
    r1 = loadtest(app_engine, "调优池")
    r2 = loadtest(default_engine, "默认池")

    print()
    print("---- 对照小结 ----")
    print(f"峰值借出 : 调优 {r1['peak']} vs 默认 {r2['peak']}")
    print(f"最大等待 : 调优 {r1['wait_max']:.1f} ms vs 默认 {r2['wait_max']:.1f} ms")
    print(f"等待>0.5s: 调优 {r1['slow']} 线程 vs 默认 {r2['slow']} 线程")
    print(f"池超时   : 调优 {r1['timeouts']} vs 默认 {r2['timeouts']}")
    print(f"总耗时   : 调优 {r1['wall']:.2f}s vs 默认 {r2['wall']:.2f}s")

    print()
    print("=" * 72)
    print("第 3 段 · 回收演示（pool_recycle：连接用后归还，隔 2.2s 再借）")
    print("=" * 72)
    e_rec = create_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0, pool_recycle=1)
    e_keep = create_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0, pool_recycle=-1)

    def pid_of(engine):
        with engine.connect() as conn:
            return conn.execute(text("SELECT pg_backend_pid()")).scalar()

    for name, eng in (("recycle=1s", e_rec), ("recycle=-1", e_keep)):
        p1 = pid_of(eng)
        p2 = pid_of(eng)
        time.sleep(2.2)
        p3 = pid_of(eng)
        verdict = "旧连接被回收 → 换了新连接" if p3 != p1 else "同一连接持续复用"
        same12 = "同一条" if p2 == p1 else "不同！"
        print(f"  {name}: pid={p1} → 立即再借 pid={p2}（{same12}）→ 隔 2.2s 再借 pid={p3} 【{verdict}】")

    print(f"\n[完成] 全程池超时数合计: {r1['timeouts'] + r2['timeouts']}")


if __name__ == "__main__":
    main()
