# ============================================================
# PaperReader 后端镜像（FastAPI + LangGraph + ChromaDB）
# 构建由 docker compose 接管（见仓库根 docker-compose.yml），不要单独指定文件名
# ============================================================
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 网络受限环境可在构建时覆盖镜像源：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ./backend
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# g++：个别依赖无预编译 wheel 时兜底；curl：HEALTHCHECK 探活
RUN apt-get update && apt-get install -y --no-install-recommends g++ curl \
    && rm -rf /var/lib/apt/lists/*

# 依赖清单单独一层：requirements 不变时重建镜像直接命中缓存
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 应用代码
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini run.py ./

# 非 root 运行；data/（PDF + Chroma 向量库）挂载点提前建好并授权
RUN useradd -m appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -sf http://localhost:8000/api/health || exit 1

# 启动前先把数据库结构迁到位（alembic 读环境变量 DATABASE_URL）
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.api.main:app --host 0.0.0.0 --port 8000"]
