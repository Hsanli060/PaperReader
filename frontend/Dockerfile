# ============================================================
# PaperReader 前端镜像：Node 构建 → Nginx 托管静态文件 + API 反代
# 两个阶段，最终镜像只含 Nginx + 静态产物（~50MB）
# ============================================================

# ---- 阶段一：构建 ----
FROM node:22-alpine AS builder

# 网络受限环境可覆盖源：
#   docker build --build-arg NPM_REGISTRY=https://registry.npmmirror.com ./frontend
ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm config set registry ${NPM_REGISTRY} && npm ci

COPY . .
RUN npm run build

# ---- 阶段二：运行 ----
FROM nginx:1.27-alpine

# 覆盖默认站点配置：托管静态文件 + 把 /api 反代给后端容器
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
