"""
请求日志中间件：每个 HTTP 请求都自动穿过它，记录 方法、路径、状态码、耗时

谁调用它：uvicorn（框架层自动调用，不需要任何人手动触发）
它夹在哪：请求 → [这个中间件] → 路由函数 → 响应 → [回到这个中间件] → 浏览器
"""

import time

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class LoggingMiddleware(BaseHTTPMiddleware):
    """继承 BaseHTTPMiddleware = 告诉框架"我是一个中间件"。
    只需要实现一个 dispatch 方法，框架每个请求都会调它一次。"""
    async def dispatch(self, request: Request, call_next)->Response:
        """异步执行请求，并记录日志

        :param request: 请求参数，包含请求方式 GET/POST、请求路径、参数等
        :param call_next:一个函数，表示“交给下一个环节去处理业务逻辑”。
        :return:将最终的返回结果递交给前端/客户端。
        """
        start=time.perf_counter()       #记录本次请求开始时间
        response=await call_next(request)   #本次请求返回的内容
        cost_ms=(time.perf_counter()-start)*1000    #计算本次请求总共耗时

        #日志记录请求方法、请求路径、HTTP 状态码、保留 1 位小数的耗时
        logger.info(
            f"{request.method} {request.url.path} -> {response.status_code} ({cost_ms:.1f} ms)"
        )
        return response