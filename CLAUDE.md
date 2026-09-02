# PaperReader — Claude Code 任务上下文

> ⚠️ **执行前必读：追加指令在 `CLAUDE_APPEND.md`**——移除对话 Prompt 级 LLM 缓存的设计决策，与本文的"缓存键加 scope 指纹"条目冲突时以 APPEND 为准。

## 项目概况
AI 论文研读助手（求职作品）。技术栈：FastAPI + SQLAlchemy 同步 Session + Alembic + ChromaDB(cosine) + Redis + AsyncOpenAI(DeepSeek) + Vue3/Pinia/vite。后端在 `backend/`，前端在 `frontend/`。Python 虚拟环境：项目根 `.venv`，跑后端命令用 `cd backend && ../.venv/Scripts/python.exe`。

## 铁律（必须遵守）
1. async 上下文里所有同步 I/O 一律 `asyncio.to_thread` 包住（SQLAlchemy Session、ChromaDB、ddgs）
2. LLM 一律走 `app/services/llm.py` 的 `async_client` / `chat_once()`，禁止直接 import openai
3. 工具执行出错不抛异常——错误说明文字返回给模型让它自我修正（tools.dispatch 惯例）
4. Agent 只有一个核心：`ReactAgent.run_stream_async()`（async 生成器，事件协议 {"type":"content"|"tool_call"|"tool_result"}），禁止再造 run()/run_stream() 同步版本
5. SSE 响应用 `EventSourceResponse(..., sep="\n")`——前端按 `\n\n` 切消息，默认 `\r\n` 会导致前端解析不出（踩过的坑）
6. Git：小步提交，语义化 commit message（中文），完成后本地 commit，禁止 push
7. 中文注释，风格参照现有代码（教学式注释、解释"为什么"）

## 当前架构要点
- 数据库表：User / Paper(user_id, arxiv_id, status: pending→downloaded→parsed→indexed) / Conversation(user_id, paper_id, title) / Message(conversation_id, role, content)
- 认证：JWT 双 token（access 30min / refresh 7d），bcrypt 密码，`_get_own_paper` 式 404 隔离
- RAG：paper_parser(pymupdf4llm) → splitter(章节感知分块) → vector_store.index_paper(metadata: paper_id, section) → retriever.search(query, paper_id, section, top_k)
- Agent 工具（app/agents/tools.py）：search_paper / web_search / summarize_paper / extract_citations，TOOLS_SCHEMA + TOOLS_IMPLS + async dispatch
- 前端 SSE 消费：frontend/src/services/api.ts 的 ssePost（fetch+ReadableStream，indexOf('\n\n') 切分）；chat store 里改响应式气泡必须取 `messages[messages.length-1]` 代理对象

# 任务：FIX-3' NotebookLM 式共享库 + 会话 scope 改造（P0 主任务）

## 目标
把"论文归属个人"改成"论文库全局共享（一份论文一份向量）"，问答范围由会话级 scope 圈定（借鉴 NotebookLM 的源选择机制）。同时把"添加论文"改成后台任务+前端轮询（FIX-2，见下）。

## FIX-3' 详细要求

### 数据库（Alembic 迁移，两个迁移）
- 迁移 A：papers 表删 user_id 列（或改名 added_by 保留署名，展示"由谁添加"——推荐保留）；arxiv_id 恢复单列 UNIQUE（当前是 (user_id, arxiv_id) 联合唯一，迁移文件 `*_papers_user_scoped_arxiv_uniqueness.py`）
- 迁移 B：conversations 表删 paper_id 单列；新建 conversation_papers 连接表（conversation_id FK + paper_id FK，联合主键）
- 历史数据搬移：现有 conversations.paper_id 的值要 INSERT 进 conversation_papers 再删列，不许丢老会话的 scope

### 数据清理（写一次性脚本 scripts/dedupe_papers.py 并执行）
- 按 arxiv_id 分组，每组保留最老一条，删多余行（paper 5/6 是 1706.03762 的重复，paper 7 是 2608.27454 的重复）
- 删行的同时调 vector_store.delete_paper 清向量
- conversations 里指向被删行的重映射到保留行
- 打印清理报告（删几行、清多少块）

### 后端 API
- papers.py：list 去掉 user_id 过滤（全员可见）；查重回全局 arxiv_id；_get_own_paper → _get_paper（只查存在性）；响应里带 added_by 用户名
- chat.py：ChatIn.paper_id → paper_ids: list[int] | None（None=全库）；会话创建/续用时 scope 写入/读出 conversation_papers；scope 校验（paper_ids 必须存在，否则 404）
- history 接口：会话详情/列表返回 scope（paper_ids 列表）
- 缓存键（services/cache.py）：键里加入 scope 指纹——md5(sorted paper_ids)[:8]（None 则 'all'），避免不同 scope 串缓存

### Agent 层（安全关键：scope 由服务端注入，绝不放 LLM 可写参数）
- react_agent.run_stream_async 加参数 allowed_paper_ids: list[int] | None（None=全库），透传给工具
- retriever.search 加 paper_ids: list[int] | None 参数，ChromaDB where 用 {"paper_id": {"$in": [...]}}
- tools.py：search_paper 的 _search_paper 接受 allowed_paper_ids（注意：不进 TOOLS_SCHEMA，由 dispatch 外部注入——在 react_agent 调 dispatch 时把 scope 拼进 arguments 或改 dispatch 签名传 ctx，任选干净的方式，但 LLM 的 schema 里绝不能出现 allowed_paper_ids）
- summarize_paper / extract_citations 工具同样校验 paper_id ∈ allowed（越权返回错误说明文字）

### 前端
- Chat.vue 左侧栏「问答范围」改成 checkbox 多选面板：列出论文库全部论文，勾选=进 scope；全不勾=全库
- stores/chat.ts：paperScope: number|null → scopeIds: number[]；send() 传 paper_ids；loadConversation 从 history 回填 scope
- PaperCard / Library 的「去问它」：把该论文加入 scope 并跳聊天页
- stores/paper.ts 若有 user 过滤逻辑则去除

## FIX-2 详细要求（论文添加后台化）
- papers.py 的 add_by_arxiv / upload_pdf：写库 status="pending" 后立即返回论文 JSON；流水线（fetch→parse→split→index）放 BackgroundTasks；流水线每完成一步更新 status 字段
- 流水线失败：status 置 "failed" + error 信息存 paper 表新字段（或日志），前端可显示
- 前端 paper.ts：addByArxiv 成功后启动 pollStatus(id)：每 3s GET /papers/{id}，更新卡片徽章（处理中/下载完成/解析完成/已索引/失败），indexed 或 failed 停止轮询；PaperCard.vue 徽章 UI
- 现有前端手动刷新问题由此根治

## 验收标准（必须全部满足并自测）
1. alembic upgrade head 成功（两个迁移），老会话 scope 不丢
2. dedupe 脚本跑完：向量库里 1706.03762 和 2608.27454 各只剩一份，total 块数 = 各论文块数之和
3. 用户 A 的会话勾选论文 X 后提问，Agent 检索只命中 scope 内论文（写一个 pytest 用例：allowed 注入后越权内容检索不到）
4. 不勾任何论文（全库）时行为同旧版
5. 缓存键：同问题不同 scope 不共用缓存（改完写或改 test）
6. 添加论文：接口 1 秒内返回 pending 论文，后台流水线推进 status，最终 indexed；前端卡片自动点亮无需手动刷新
7. `../.venv/Scripts/python.exe -m pytest tests/ -v` 全绿（改老测试时保持测试意图不丢）
8. 前端 npm run build 无类型错误
9. 每完成一个模块本地 git commit（feat:/refactor:/test: 中文 message）

## 环境注意
- 跑后端测试/脚本：cd backend && ../.venv/Scripts/python.exe ...
- 常驻代理 socks5://127.0.0.1:10808：跑需要外网的命令（pytest 会调 embedding API、alembic 不需要）时用 `env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy` 前缀；本地 127.0.0.1 服务（uvicorn/前端/Redis/PG）不要走代理
- PG 和 Redis 已在本机运行（DATABASE_URL 看 backend/.env）
- 前端构建：cd frontend && npm run build
