# PaperReader 📄

AI 论文研读助手 —— 让你像读中文文档一样读英文论文。

## ✨ 功能特性

- **学术 PDF 深度解析**：基于 PyMuPDF，精准提取论文正文、公式、表格、引用，保留学术文档结构
- **RAG 智能问答**：论文全文切片 + 向量检索，对话精准引用原文段落
- **Agent 工具链**：手写 ReAct 循环（OpenAI Function Calling，最多 10 轮），四个工具——论文检索（RAG）、摘要生成、引用网络分析、联网搜索（DuckDuckGo）
- **流式对话**：SSE 逐字输出 + 工具调用过程实时直播，前端打字机观感
- **会话历史管理**：多会话列表 / 查询 / 删除，对话记录落库持久化，Agent 配滑动窗口记忆（最近 N 轮）
- **全局论文库 + 会话级 Scope**：论文库全局共享（一份论文一份向量），每个对话圈定论文范围，避免全局污染
- **引用网络可视化**：基于 D3.js 的引用关系图谱，一键查看论文间的引用链路
- **内容派生缓存**：摘要 / 引用提取结果经 Redis 缓存 24h，Redis 挂了自动降级直通
- **本地 PDF 上传**：除 arXiv 抓取外，支持上传本地 PDF 入库解析
- **JWT 认证**：注册 / 登录 / 刷新令牌，多用户隔离

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Uvicorn |
| AI Agent | **手写 ReAct 循环**（OpenAI Function Calling + 异步流式，非 LangChain/LangGraph 框架） |
| LLM | DeepSeek v4 Flash (API) |
| Embedding | 阿里云百炼 text-embedding-v4 (1024 维) |
| 向量数据库 | ChromaDB (余弦相似度) |
| 关系型数据库 | PostgreSQL + SQLAlchemy + Alembic |
| 缓存 | Redis（内容派生对象缓存，挂了降级直通） |
| 前端 | Vue 3 + Pinia + Ant Design Vue |
| 公式渲染 | KaTeX |
| 引用网络 | D3.js |
| PDF 解析 | PyMuPDF + pymupdf4llm |

## 🤔 为什么不用 LangChain/LangGraph？

Agent 核心是一个约 170 行的手写 async ReAct 循环（`backend/app/agents/react_agent.py`）：

- **流程完全可控**：流式事件在循环里"直播"（tool_call / tool_result / content 三种），SSE 打字机效果不需要绕过任何框架抽象
- **依赖更轻**：整个 Agent 层只依赖 `openai` SDK，requirements 比 LangChain 全家桶轻一个数量级
- **调试直观**：每一轮 prompt 组装、工具 dispatch、记忆窗口都透明可见，框架黑盒里发生的意外为零

框架的价值在快速换模型/换编排，而本项目工具集小而稳定（4 个工具），手写循环的收益大于引入成本。

## 🚀 快速开始

### 环境准备

- Python >= 3.10、Node.js >= 18
- PostgreSQL、Redis（本机跑起即可）
- DeepSeek API Key（LLM）、阿里云百炼 API Key（Embedding）

### 后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填写数据库、LLM API Key 等配置

# 数据库迁移
alembic upgrade head

# 启动服务
uvicorn app.api.main:app --reload
```

### 前端

```bash
cd frontend

npm install
npm run dev
```

### 环境变量 (.env)

```env
# PostgreSQL
DATABASE_URL=postgresql+psycopg2://user:***@localhost:5432/paperreader

# Redis（可留空：留空则缓存自动降级直通）
REDIS_URL=redis://localhost:6379/0

# LLM (DeepSeek)
LLM_API_KEY=your-deepseek-api-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash

# Embedding (阿里云百炼)
EMBEDDING_API_KEY=your-dashscope-api-key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIM=1024

# JWT（签名密钥，必填）
JWT_SECRET=your-secret-key
```

## 📁 项目结构

```
PaperReader/
├── backend/
│   ├── requirements.txt   # Python 依赖
│   ├── app/
│   │   ├── agents/        # 手写 ReAct Agent（function calling 循环 + 记忆窗口）
│   │   ├── api/           # FastAPI 路由
│   │   ├── models/        # SQLAlchemy ORM + 数据库模型
│   │   ├── paper/         # 论文抓取、解析、引用提取
│   │   ├── rag/           # RAG 检索链路（切片、向量、检索）
│   │   ├── services/      # LLM、Embedding、缓存服务
│   │   └── config.py      # pydantic-settings 配置
│   ├── alembic/           # 数据库迁移
│   └── tests/             # pytest 测试
├── frontend/
│   ├── src/
│   │   ├── views/         # 页面组件
│   │   ├── components/    # UI 组件
│   │   ├── stores/        # Pinia 状态管理
│   │   └── services/      # axios 请求封装
│   └── package.json
└── README.md
```

## 📝 License

MIT
