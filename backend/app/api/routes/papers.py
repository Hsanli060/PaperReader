"""
论文管理路由：/api/papers 的增删查
谁调用它：main.py 里 include_router 挂载
数据隔离规则：所有查询一律先按 user_id 过滤——用户只能看到/操作自己的论文
"""
import json
from pathlib import Path
import re

from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy import select, func
from pydantic import BaseModel,Field

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.middleware.error_handler import AppException
from app.models.orm import Paper
from app.rag.vector_store import delete_paper as vs_delete_paper   # 别名：避免和下面的 delete_papers 重名
from app.paper.paper_parser import parse_pdf
from app.rag.splitter import split_paper


router = APIRouter(prefix="/papers", tags=["论文"])

class ArxivIn(BaseModel):
    """POST /arxiv 的入参：接受纯 ID 或完整链接"""
    arxiv:str=Field(min_length=5,max_length=200)

# ==================== 工具函数 ====================
def _paper_to_dict(p:Paper)->dict:
    """ORM 对象 → JSON 字典。authors 存的是 JSON 字符串，读出来要解析回列表"""
    return {
        "id":p.id,
        "arxiv_id":p.arxiv_id,
        "title":p.title,
        "authors":json.loads(p.authors) if p.authors else [],
        "abstract":p.abstract,
        "pdf_path":p.pdf_path,
        "status": p.status,  # pending → downloaded → parsed → indexed 的生命周期
        "created_at": str(p.created_at),
    }

def _get_own_paper(db,paper_id:int,user_id:int)->Paper:
    """查"我的"论文，查不到/不是我的统一报 404。"""
    paper=db.get(Paper,paper_id)
    if paper is None or paper.user_id!=user_id:
        raise AppException("论文不存在", 404)
    return paper

def _extract_arxiv_id(raw:str)->str:
    """从用户输入的信息提起论文ID"""
    m=re.search(r"\d{4}\.\d{4,5}(v\d+)?",raw)
    if not m:
        raise AppException("无法识别 arXiv ID，请输入论文链接或 ID", 422)
    return m.group(0)

# ==================== 接口 ====================
@router.get("")
def list_papers(
    page:int=Query(1,ge=1),                     # Query 校验：页码最小 1，防 page=0 把 offset 算成负数
    page_size:int=Query(10,ge=1,le=50),         # 上限 50：防止恶意请求一次拉爆全表
    user:dict=Depends(get_current_user),
    db=Depends(get_db),
)->dict:
    """分页列出我的论文。

    :return: {"total": 总数, "page": 当前页, "page_size": 每页几条, "items": [...]}
    """
    #查询该用户存储的所有论文（键名是 user_id，对齐 deps.py 里 get_current_user 的返回）
    base=select(Paper).where(Paper.user_id==user["user_id"])
    total=db.scalar(select(func.count()).select_from(base.subquery()))

    #当前页的论文
    rows=db.scalars(
        base.order_by(Paper.id.desc())
        .offset((page-1)*page_size)
        .limit(page_size)
    ).all()
    return {
        "total":total,
        "page":page,
        "page_size":page_size,
        "items":[_paper_to_dict(p) for p in rows]
    }

@router.post("/arxiv")
def add_by_arxiv(
        data:ArxivIn,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """给一个 arXiv ID/链接 → 跑完整流水线 → 返回论文信息。
    全流程：提取ID → fetch(下载PDF) → 查重 → 落库 → parse(解析成md) → split(切片) → 向量化 → indexed(入库)
    """
    arxiv_id=_extract_arxiv_id(data.arxiv)

    result=db.scalar(select(Paper).where(Paper.arxiv_id==arxiv_id))
    if result:
        # 已有 → 不重复跑流水线，直接告诉调用方
        return {"id": result.id, "title": result.title, "status": result.status, "duplicated": True}
    from app.paper.fetcher import fetch_paper
    #下载论文到文件夹内
    meta=fetch_paper(arxiv_id)

    #落库->pg数据库
    paper=Paper(
        user_id=user["user_id"],
        arxiv_id=meta["arxiv_id"],
        title=meta["title"],
        authors=json.dumps(meta["authors"], ensure_ascii=False),  # 写侧：列表 → JSON 字符串
        abstract=meta["abstract"],
        pdf_path=meta["pdf_path"],
        status="downloaded",
    )
    db.add(paper)
    db.commit()

    #解析论文->pdf格式 + 切片 + 向量化
    parsed=parse_pdf(paper.pdf_path)
    chunks=split_paper(text=parsed["text"],headings=parsed["headings"])
    from app.rag.vector_store import index_paper
    index_paper(paper_id=paper.id,chunks=chunks)
    paper.status="indexed"
    db.commit()
    return {**_paper_to_dict(paper),"duplicated":False}

@router.post("/upload")
def upload_pdf(
        file:UploadFile=File(...),
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """上传本地 PDF：存盘 → 登记。状态从 pending 开始"""
    # 1. 简单校验：只认 .pdf 后缀（魔数校验更严但超出本课范围）
    if not (file.filename or "").lower().endswith(".pdf"):
        raise AppException("只接受 PDF 文件", 415)  # 415 Unsupported Media Type

    # 2. 落盘。文件名用 uuid 前缀防重名/防路径攻击
    papers_dir=Path(settings.PAPERS_DIR)
    papers_dir.mkdir(parents=True,exist_ok=True)

    import uuid
    #生成随机文件名： upload_a1b2c3d4（随机）_paper（上传文件名）.pdf
    saved_name = f"upload_{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"  # Path(...).name 把目录部分剥掉
    path = papers_dir / saved_name
    #写入硬盘
    path.write_bytes(file.file.read())

    paper=Paper(
        user_id=user["user_id"],
        arxiv_id=None,
        title=Path(file.filename).stem,  # "mamba.pdf" → "mamba"
        authors="[]",
        abstract=None,
        pdf_path=str(path),
        status="pending",
    )
    db.add(paper)
    db.commit()
    return _paper_to_dict(paper)

@router.get("/{paper_id}")
def get_paper(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """查看论文单篇详情。路径参数 paper_id 会自动转 int，传字母 FastAPI 直接 422"""
    paper = _get_own_paper(db, paper_id, user["user_id"])
    return _paper_to_dict(paper)

@router.delete("/{paper_id}")
def delete_papers(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """删除论文：PG 一行 + 向量库该论文的所有块 + （暂不删 PDF 文件，课程尾声做清理脚本）"""
    paper=_get_own_paper(db,paper_id,user["user_id"])
    #删除向量数据库的论文向量
    vs_delete_paper(paper_id)
    #再删除PG数据库
    db.delete(paper)
    db.commit()
    return{"deleted":paper_id}