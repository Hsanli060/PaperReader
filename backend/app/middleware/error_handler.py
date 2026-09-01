"""
全局异常处理：任何路由里抛出的异常，都在这里被翻译成统一格式的 JSON
    {"code": 状态码, "message": 人话错误信息, "detail": 可选的补充信息}

谁调用它：FastAPI 框架。路由函数抛异常 → 框架查注册表 → 调用对应 handler
"""
from fastapi import FastAPI,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

# 自定义业务异常：业务代码里想报错就 raise AppException("用户名已存在", 409)
# 它不继承 HTTPException——故意的，业务错误和 HTTP 语义错误分开，互不干扰
class AppException(Exception):
    def __init__(self,message:str,status:int=400):
        self.message=message        #错误信息
        self.status=status          #HTTP 状态码

def _json_error(status:int,message:str,detail=None)->JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code":status,"message":message,"detail":detail},
    )

# ----  4 个 handler，分别兜 4 种情况 ----
async def app_exception_handler(request:Request,exc:AppException):
    """情况1：业务代码主动 raise AppException（预期内的错误）"""
    return _json_error(exc.status,exc.message)

async def http_exception_handler(request:Request,exc:StarletteHTTPException):
    """情况2：框架抛出的 HTTP 语义错误（404 找不到路径、405 方法不对等）。"""
    return _json_error(exc.status_code,str(exc.detail))

async def validation_exception_handler(request:Request,exc:RequestValidationError):
    """情况3：请求参数不合法（比如接口要求 int，前端传了 "abc"）。"""
    # exc.errors() 返回的数据结构长这样：
    # [
    #     {
    #         "type": "int_parsing",
    #         "loc": ("body", "user", "age"),  # 错误位置：请求体 -> user -> age
    #         "msg": "Input should be a valid integer, unable to parse string as an integer",  # 报错原因
    #         "input": "abc",
    #     }
    # ]
    detail=[
        # e["loc"] 形如 ("body", "username")，去掉第一个元素得到字段名
        {"field":".".join(str(x) for x in e["loc"][1:]),"reason":e["msg"]}
        for e in exc.errors()
    ]
    return _json_error(422,"请求参数不合法",detail)

async def unhandled_exception_handler(request:Request,exc:Exception):
    """情况4：上面三种之外的任何意外异常（代码 bug、数据库挂了...）。
    logger.exception 会把完整堆栈打进日志（排查 bug 就靠它），
    但返回给前端的只有"服务器内部错误"——堆栈属于内部信息，不外泄"""
    logger.exception(f"未处理异常 {request.method} {request.url.path}")
    return _json_error(500,"服务器内部错误")

def register_error_handlers(app:FastAPI)->None:
    """往 app 的异常注册表里填 4 个条目。main.py 启动时调用一次"""
    app.add_exception_handler(AppException,app_exception_handler)
    app.add_exception_handler(StarletteHTTPException,http_exception_handler)
    app.add_exception_handler(RequestValidationError,validation_exception_handler)
    app.add_exception_handler(Exception,unhandled_exception_handler)