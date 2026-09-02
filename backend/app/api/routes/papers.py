"""
论文管理路由：/api/papers 的增删查
谁调用它：main.py 里 include_router 挂载
数据隔离规则：所有查询一律先按 user_id 过滤——用户只能看到/操作自己的论文
"""
import json
from pathlib import Path
import re

from fastapi import APIRouter, Depends, Query, UploadFile, File, BackgroundTasks
from sqlalchemy import select, func
from pydantic import BaseModel,Field

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.middleware.error_handler import AppException
from app.models.orm import Paper
from app.rag.vector_store import delete_paper as vs_delete_paper   # 别名：避免和下面的 delete_papers 重名
from app.paper.paper_parser import parse_pdf
from app.rag.splitter import split_paper
from app.services.cache import llm_cache


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
        "status": p.status,      # pending → downloaded → parsed → indexed（FIX-2 后台推进）
        "last_error": p.last_error,  # 流水线失败原因（failed 徽章的提示文本）
        "added_by": p.added_by,      # 谁添加的（署名展示，不做隔离）
        "created_at": str(p.created_at),
    }

def _get_paper(db,paper_id:int)->Paper:
    """查论文（全局共享库：只查存在性，不再按用户过滤）。查不到统一 404。"""
    paper=db.get(Paper,paper_id)
    if paper is None:
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
    """获取论文列表

    :return: {"total": 总数, "page": 当前页, "page_size": 每页几条, "items": [...]}
    """
    base=select(Paper)
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

# ---- FIX-2：后台流水线（BackgroundTasks，不引 Celery——单机场景够用且少一个外部依赖） ----
def _run_pipeline_background(paper_id:int, db_factory, pdf_path:str|None=None):
    """后台跑 fetch之后的所有步骤：parse → split → index，每步推进 status。
    db_factory：SessionLocal（后台任务不能复用请求的 session——请求结束它就关了）
    """
    from app.models.database import SessionLocal
    from app.rag.vector_store import index_paper
    db = SessionLocal()
    try:
        paper = db.get(Paper, paper_id)
        if paper is None:
            return
        try:
            # parse
            parsed = parse_pdf(pdf_path or paper.pdf_path)
            paper.status = "parsed"
            db.commit()
            # split + index
            chunks = split_paper(text=parsed["text"], headings=parsed["headings"])
            index_paper(paper_id=paper.id, chunks=chunks)
            paper.status = "indexed"
            paper.last_error = None
            db.commit()
        except Exception as e:
            paper.status = "failed"
            paper.last_error = f"{type(e).__name__}: {e}"[:500]
            db.commit()
    finally:
        db.close()


def _fetch_and_mark_downloaded(paper_id:int, arxiv_id:str):
    """后台第一步：下载 PDF + 抓元数据，落库 status=downloaded（FIX-2 状态机第一环）"""
    import json as _json
    from app.paper.fetcher import fetch_paper
    from app.models.database import SessionLocal
    db = SessionLocal()
    try:
        paper = db.get(Paper, paper_id)
        if paper is None:
            return
        try:
            meta = fetch_paper(arxiv_id)
            paper.title = meta["title"]
            paper.authors = _json.dumps(meta["authors"], ensure_ascii=False)
            paper.abstract = meta["abstract"]
            paper.pdf_path = meta["pdf_path"]
            paper.status = "downloaded"
            db.commit()
        except Exception as e:
            paper.status = "failed"
            paper.last_error = f"{type(e).__name__}: {e}"[:500]
            db.commit()
    finally:
        db.close()


@router.post("/arxiv")
async def add_by_arxiv(
        data:ArxivIn,
        background_tasks: BackgroundTasks,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """给一个 arXiv ID/链接 → 登记 + 受理流水线 → 立即返回（FIX-2 后台化）。

    全流程（时间维度）：提取ID → 查重 → 落库(pending) →【立即返回】
                        后台：downloaded → parsed → indexed（前端轮询看徽章）
    """
    arxiv_id=_extract_arxiv_id(data.arxiv)

    # 去重回全局：一份论文全系统一行（共享库语义）
    result=db.scalar(
        select(Paper).where(Paper.arxiv_id==arxiv_id)
    )
    if result:
        # 已有 → 不重复跑流水线，直接告诉调用方
        return {"id": result.id, "title": result.title, "status": result.status, "duplicated": True}

    # 先从输入提取 ID 落一条 pending 记录（前端 1 秒内看到卡片）
    paper=Paper(
        added_by=user["user_id"],
        arxiv_id=arxiv_id,
        title=f"arXiv:{arxiv_id}",   # 占位标题，后台 fetch 后覆盖为真实标题
        authors="[]",
        abstract=None,
        pdf_path=None,               # PDF 还没下载，后台任务里补
        status="pending",
    )
    db.add(paper)
    db.commit()

    # 流水线丢后台：先下载，再解析索引（两段各自推进 status）
    background_tasks.add_task(_fetch_and_mark_downloaded, paper.id, arxiv_id)
    background_tasks.add_task(_run_pipeline_background, paper.id, None)

    return {**_paper_to_dict(paper),"duplicated":False}

@router.post("/upload")
async def upload_pdf(
        background_tasks: BackgroundTasks,
        file:UploadFile=File(...),
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """上传本地 PDF：存盘 → 登记(pending) → 立即返回；解析流水线后台跑（FIX-2）。

    上传的论文如果只登记不索引，Agent 的 search_paper 永远搜不到它
    （向量库里没有块）——流水线在后台把 parse→split→index 跑完，
    前端轮询 status 看到徽章从"处理中"点亮为"已索引"。
    """
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
        added_by=user["user_id"],
        arxiv_id=None,
        title=Path(file.filename).stem,  # "mamba.pdf" → "mamba"
        authors="[]",
        abstract=None,
        pdf_path=str(path),
        status="pending",
    )
    db.add(paper)
    db.commit()

    # 3. 流水线丢后台（PDF 已在本地，跳过下载段直接 parse→index）
    background_tasks.add_task(_run_pipeline_background, paper.id, None)
    return _paper_to_dict(paper)

@router.get("/{paper_id}")
def get_paper(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """查看论文单篇详情。路径参数 paper_id 会自动转 int，传字母 FastAPI 直接 422"""
    paper = _get_paper(db, paper_id)
    return _paper_to_dict(paper)

@router.delete("/{paper_id}")
def delete_papers(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """删除论文（全局共享库：任何登录用户可删自己添加的——简化为都可删）：PG 一行 + 向量库该论文的所有块"""
    paper=_get_paper(db,paper_id)
    #删除向量数据库的论文向量
    vs_delete_paper(paper_id)
    #再删除PG数据库
    db.delete(paper)
    db.commit()
    return{"deleted":paper_id}


@router.get("/{paper_id}/summary")
async def get_paper_summary(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """结构化摘要：问题/方法/实验/结论 四段。LLM 生成的结果缓存 1 天。"""
    _get_paper(db, paper_id)   # 存在性校验（全局共享库，无归属检查）

    cache_key=f"summary:{paper_id}"
    cached=llm_cache.get_raw(cache_key)
    if cached:
        return json.loads(cached)

    from app.agents.tools import _summarize_paper_tool
    summary=await _summarize_paper_tool(paper_id)   # 异步工具：await 直接等，不占线程
    llm_cache.set_raw(cache_key,json.dumps(summary,ensure_ascii=False),ttl_seconds=86400)
    return summary

@router.get("/{paper_id}/citations")
async def get_paper_citations(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """引用关系列表：这篇论文引用了谁、各是什么用途。同样缓存 1 天。"""
    _get_paper(db, paper_id)   # 存在性校验

    cache_key=f"citations:{paper_id}"
    cached=llm_cache.get_raw(cache_key)
    if cached:
        items=json.loads(cached)
        # 兼容历史毒缓存：旧版本把裸 list 直接写进 Redis，命中时必须包上 items 键，
        # 否则返回值不是 dict，撞上 -> dict 的 response_model 校验直接 500
        if isinstance(items, list):
            items={"items": items}
        return items

    from app.agents.tools import _extract_citations_tool
    citations=await _extract_citations_tool(paper_id)
    # 缓存存的就是接口返回的形状 {"items": [...]}——命中路径和生成路径结构永远一致
    llm_cache.set_raw(cache_key,json.dumps({"items":citations},ensure_ascii=False),ttl_seconds=86400)
    return {"items":citations}