"""
FastAPI 入口：整个后端从这启动
启动命令（在 backend/ 目录下）：
    uvicorn app.api.main:app --reload
        --reload：代码改了自动重启（开发期专用）

装配清单：
    中间件：日志（每个请求记一行）、CORS（允许 5173 前端跨域访问）
    异常：register_error_handlers 统一错误格式
    路由：auth（注册/登录/刷新）+ papers（论文管理）+ health
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes.chat import router as chat_router

from app.config import settings
from app.middleware.logging import LoggingMiddleware
from app.middleware.error_handler import register_error_handlers
from app.api.routes.auth import router as auth_router
from app.api.routes.papers import router as papers_router

app=FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
)

# ---- 中间件注册（注意顺序）----
# add_middleware 是"洋葱模型"：后注册的在外面，先注册的在里面。
# 请求穿过顺序 = 注册的逆序。CORS 放最后注册 = 它在最外层，
# 这样即使内层报错返回 500，响应头上也盖着 CORS 章，前端才能读到错误信息
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,                             #跨域中间件，解决前后端不同源的问题
    allow_origins=["http://localhost:5173"],    #告诉跨域中间件该地址允许拿到后端数据
    allow_credentials=True,                     #允许携带凭证（Cookie、身份认证 Token 等）
    allow_methods=["*"],                        #允许所有请求方法
    allow_headers=["*"],                        #允许所有请求头
)

#将自定义的所有异常处理注册到该 app 中
register_error_handlers(app)

# ---- 路由挂载（prefix 统一用 config 里的 /api，一次就够，别重复挂）----
app.include_router(auth_router,prefix=settings.API_PREFIX)
app.include_router(papers_router,prefix=settings.API_PREFIX)
app.include_router(chat_router,prefix=settings.API_PREFIX)

# ---- 健康检查 ----
@app.get("/api/health")
def health():
    """运维惯用：浏览器访问一下就知道后端活没活着。"""
    return {"status": "ok"}
