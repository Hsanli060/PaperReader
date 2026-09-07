# PaperReader 📄

AI 论文研读助手 —— 让你像读中文文档一样读英文论文。

## ✨ 功能特性

- **学术 PDF 深度解析**：基于 PyMuPDF，精准提取论文正文、公式、表格、引用，保留学术文档结构
- **RAG 智能问答**：论文全文切片 + 向量检索，对话精准引用原文段落
- **LangGraph Agent 工具链**：支持论文搜索（arXiv）、摘要生成、引用网络分析等工具调用
- **引用网络可视化**：基于 D3.js 的引用关系图谱，一键查看论文间的引用链路
- **会话级 Scope**：每个对话圈定论文范围，避免全局污染，精准聚焦
- **JWT 认证**：用户注册/登录，多用户隔离

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Uvicorn |
| AI Agent | LangGraph + LangChain |
| LLM | DeepSeek v4 Flash (API) |
| Embedding | 阿里云百炼 text-embedding-v4 (1024 维) |
| 向量数据库 | ChromaDB (余弦相似度) |
| 关系型数据库 | PostgreSQL + SQLAlchemy + Alembic |
| 缓存 | Redis (仅 prompt 级 LLM 缓存) |
| 前端 | Vue 3 + Pinia + Ant Design Vue |
| 公式渲染 | KaTeX |
| 引用网络 | D3.js |
| PDF 解析 | PyMuPDF + pymupdf4llm |

## 🚀 快速开始

### 环境要求

- Python >= 3.10
- Node.js >= 18
- PostgreSQL
- Redis

### 后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r ../PaperReader-backend-requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填写数据库、LLM API Key 等配置

# 数据库迁移
alembic upgrade head

# 启动服务
python run.py
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
DATABASE_URL=postgresql://user:password@localhost:5432/paperreader

# Redis
REDIS_URL=redis://localhost:6379/0

# LLM (DeepSeek)
LLM_API_KEY=your-deepseek-api-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# Embedding (阿里云百炼)
EMBEDDING_API_KEY=your-dashscope-api-key
EMBEDDING_MODEL=text-embedding-v4

# JWT
JWT_SECRET_KEY=your-secret-key

# arXiv
ARXIV_API_URL=http://export.arxiv.org/api/query
```

## 📁 项目结构

```
PaperReader/
├── backend/
│   ├── app/
│   │   ├── agents/        # LangGraph Agent 定义
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
│   │   └── api/           # axios 请求封装
│   └── package.json
└── PaperReader-backend-requirements.txt
```

## 📝 License

MIT
