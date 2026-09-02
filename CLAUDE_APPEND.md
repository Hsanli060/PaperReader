# Claude Code 追加指令（请在继续主任务前先读本文件）

少爷（项目所有者）刚做了一个设计决策，并入你的当前任务，**优先级高于任务书里与之冲突的部分**：

## 移除对话 Prompt 级 LLM 缓存

**决策理由**（少爷拍板）：多轮对话语义依赖上下文，同问题不同 scope/历史答案不同，缓存键不可判定；命中时走 cached_gen 回放会丢失工具调用轨迹（前端工具面板变空）；论文研读场景真实命中率趋近于零。Redis 保留在真正合适的位置。

## 具体改动

1. **`backend/app/api/routes/chat.py`**：
   - 删除 `_prepare()` 里的 `cached = llm_cache.get(...)` 查询和返回值中的 cached
   - 删除整个 `if cached:` 分支（含 `_persist_cached` 和 `cached_gen`）
   - 删除 `agen()` 里的 `llm_cache.set(...)` 调用
   - 聊天统一走真 Agent 流式（`run_stream_async`）
2. **`backend/app/services/cache.py`**：
   - 删除 `get(paper_id, question)` 和 `set(paper_id, question, answer, ttl_seconds)` 两个方法
   - **保留** `get_raw/set_raw`——papers.py 的 summary/citations 派生对象缓存还在用，那是正确的缓存场景
3. **测试**：如有对对话缓存的断言（test_api.py 或其他），一并修改/删除
4. **CLAUDE.md 任务书**里"缓存键加 scope 指纹"一条作废——不要做
5. **commit message** 注明设计决策：
   `refactor: 移除对话 Prompt 级 LLM 缓存——多轮语义依赖上下文键不可判定+命中丢失工具轨迹+真实命中率低，Redis 保留 summary/citations 派生对象缓存`

## 执行时机

建议在你**完成手头正在写的文件后、下一个 git commit 之前**插入处理（避免半成品冲突）。其余任务（FIX-3' scope 改造 + FIX-2 后台化）按原任务书继续，不受影响。
