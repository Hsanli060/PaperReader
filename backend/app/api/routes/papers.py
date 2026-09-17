"""
论文管理路由：/api/papers 的增删查
谁调用它：main.py 里 include_router 挂载
数据隔离规则（批②·成员制）：全局只存一份论文/一份向量，但"谁能看见"由
user_papers 归属关系决定——列表/详情/摘要/引用/删除 一律"我是成员"才放行；
重复添加别人已有的论文 = 只加一行归属（不重跑下载与向量化）。
"""
import json
from pathlib import Path
import re

from fastapi import APIRouter, Depends, Query, UploadFile, File, BackgroundTasks
from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel,Field

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.middleware.error_handler import AppException
from app.models.orm import Paper, conversation_papers, user_papers
from app.rag.vector_store import delete_paper as vs_delete_paper   # 别名：避免和下面的 delete_papers 重名
from app.paper.paper_parser import parse_pdf
from app.rag.splitter import split_paper
from app.services.cache import llm_cache
from app.services.user_keys import ensure_keys, set_current_keys


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

def _get_my_paper(db,paper_id:int,user_id:int)->Paper:
    """查"我库里的"论文（批②成员制）：不是成员一律 404（不泄露存在性，与会话同一思路）。
    单条 SELECT（带成员 join）——保持 X-DB-Queries=1 的查询形状。"""
    paper=db.scalar(
        select(Paper)
        .join(user_papers, user_papers.c.paper_id==Paper.id)
        .where(Paper.id==paper_id, user_papers.c.user_id==user_id)
    )
    if paper is None:
        raise AppException("论文不存在", 404)
    return paper

def _add_member(db,user_id:int,paper_id:int)->None:
    """把论文加进某人的库（幂等：已在库中则忽略）——只发一条 INSERT..ON CONFLICT"""
    db.execute(
        pg_insert(user_papers)
        .values(user_id=user_id, paper_id=paper_id)
        .on_conflict_do_nothing()
    )

def _extract_arxiv_id(raw:str)->str:
    """从用户输入的信息提起论文ID"""
    m=re.search(r"\d{4}\.\d{4,5}(v\d+)?",raw)
    if not m:
        raise AppException("无法识别 arXiv ID，请输入论文链接或 ID", 422)
    return m.group(0)

# 论文派生缓存的键：集中在这两个函数里拼，别处不许手写 "summary:3" 这种字面量。
# 删除接口要靠它们把缓存清干净——前缀写错（summery:3）不会报错，只会静默漏删
def _summary_key(paper_id:int)->str:
    return f"summary:{paper_id}"

def _citations_key(paper_id:int)->str:
    return f"citations:{paper_id}"

# ==================== 接口 ====================
@router.get("")
def list_papers(
    page:int=Query(1,ge=1),                     # Query 校验：页码最小 1，防 page=0 把 offset 算成负数
    page_size:int=Query(10,ge=1,le=50),         # 上限 50：防止恶意请求一次拉爆全表
    user:dict=Depends(get_current_user),
    db=Depends(get_db),
)->dict:
    """获取"我的论文库"列表（批②：成员制过滤，分页不变）

    :return: {"total": 总数, "page": 当前页, "page_size": 每页几条, "items": [...]}
    """
    # 批②：只看"我的论文库"；join 不增查询条数（count + 分页仍是 2 条 SQL）
    base=(select(Paper)
          .join(user_papers, user_papers.c.paper_id==Paper.id)
          .where(user_papers.c.user_id==user["user_id"]))
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
def _run_pipeline_background(paper_id:int, db_factory, pdf_path:str|None=None,
                             embedding_key:str|None=None,
                             embedding_base_url:str|None=None):
    """后台跑 fetch之后的所有步骤：parse → split → index，每步推进 status。
    db_factory：SessionLocal（后台任务不能复用请求的 session——请求结束它就关了）
    embedding_key / embedding_base_url：批①——后台任务不在请求上下文里，
    嵌入 key 与自带服务地址必须由调度方显式传入
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
            index_paper(paper_id=paper.id, chunks=chunks, api_key=embedding_key,
                        base_url=embedding_base_url)
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
        # 已有（别人先加过 OR 自己加过）→ 不重跑流水线：
        # 批②：只补一行归属（幂等），把这篇文章"加入我的库"，然后告诉调用方
        _add_member(db, user["user_id"], result.id)
        db.commit()
        return {"id": result.id, "title": result.title, "status": result.status, "duplicated": True}

    # 批①：新论文要走"下载→解析→向量化"——先确认添加者配置了嵌入 key（落库前拦下）
    keys = ensure_keys(db, user["user_id"], need_embedding=True)

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
    try:
        db.flush()                                   # 先拿 paper.id（事务未提交）
        _add_member(db, user["user_id"], paper.id)   # 批②：归属与论文同一事务落库
        db.commit()
    except IntegrityError:
        # 竞态兜底：查重与插入之间不是原子的——两个并发请求可能都通过了上面的查重、
        # 然后一起插，后提交的会撞 arxiv_id 唯一索引（check-then-insert race）。
        # 回滚后重查：先插入者已提交 → 当作"重复添加"正常返回；查不到 → 非本竞态，原样上抛。
        db.rollback()
        existing=db.scalar(select(Paper).where(Paper.arxiv_id==arxiv_id))
        if existing is None:
            raise
        # 批②：对方先插成功了——把这篇加进"我的库"再返回（竞态兜底也保归属）
        _add_member(db, user["user_id"], existing.id)
        db.commit()
        logger.info(f"[竞态兜底] arxiv={arxiv_id}：查重后插入撞唯一索引，已转按'重复添加'返回（HTTP 200）")
        return {"id": existing.id, "title": existing.title,
                "status": existing.status, "duplicated": True}

    # 流水线丢后台：先下载，再解析索引（两段各自推进 status）
    # 批①：索引用"添加者自己的嵌入 key + 服务地址"——后台不在请求上下文里，显式传入
    background_tasks.add_task(_fetch_and_mark_downloaded, paper.id, arxiv_id)
    background_tasks.add_task(_run_pipeline_background, paper.id, None, None,
                              keys.embedding, keys.embedding_base_url)

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

    # 批①：上传论文要跑解析索引流水线——先确认添加者配置了嵌入 key
    keys = ensure_keys(db, user["user_id"], need_embedding=True)

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
    db.flush()                       # 拿 id
    _add_member(db, user["user_id"], paper.id)   # 批②：上传即归属
    db.commit()

    # 3. 流水线丢后台（PDF 已在本地，跳过下载段直接 parse→index）
    # 批①：嵌入 key/地址同上，显式传给后台
    background_tasks.add_task(_run_pipeline_background, paper.id, None, None,
                              keys.embedding, keys.embedding_base_url)
    return _paper_to_dict(paper)

@router.get("/{paper_id}")
def get_paper(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """查看论文单篇详情（批②：必须是"我库里的"）。路径参数 paper_id 会自动转 int，传字母 FastAPI 直接 422"""
    paper = _get_my_paper(db, paper_id, user["user_id"])
    return _paper_to_dict(paper)

@router.delete("/{paper_id}")
def delete_papers(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """删除论文（批②退会制）：先从"我的库"移除；
    最后一个成员移除时，才连 向量块 + 会话 scope 引用 + PG 行 + 派生缓存 一起清。"""
    paper=_get_my_paper(db,paper_id,user["user_id"])

    # 1. 退会：删掉我这一行归属
    db.execute(delete(user_papers).where(
        user_papers.c.user_id==user["user_id"], user_papers.c.paper_id==paper_id))

    # 2. 还有别的成员吗？没有 = 这篇论文从全局退场（连向量/缓存/会话引用一起清）
    remain=db.scalar(select(func.count()).select_from(user_papers).where(user_papers.c.paper_id==paper_id))
    if remain==0:
        vs_delete_paper(paper_id)   # 向量库该论文的所有块
        # 会话 scope 引用（外键挡路：先清引用再删论文行）
        db.execute(delete(conversation_papers).where(conversation_papers.c.paper_id==paper_id))
        db.delete(paper)
        llm_cache.delete_raw(_summary_key(paper_id),_citations_key(paper_id))

    db.commit()
    return{"deleted":paper_id}


@router.get("/{paper_id}/summary")
async def get_paper_summary(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """结构化摘要：问题/方法/实验/结论 四段。LLM 生成的结果缓存 1 天。"""
    _get_my_paper(db, paper_id, user["user_id"])   # 批②：只放行"我库里的"论文
    # 批①：本接口会调 LLM——先解析当前用户 key（没配就 409）
    set_current_keys(ensure_keys(db, user["user_id"], need_llm=True))

    cache_key=_summary_key(paper_id)

    async def build()->str|None:
        """返回 None = 模型这次没读出东西，由 get_or_build 透传成"不写缓存" """
        from app.agents.tools import _summarize_paper_tool
        summary=await _summarize_paper_tool(paper_id)   # 异步工具：await 直接等，不占线程
        values=summary.values() if isinstance(summary,dict) else ()
        if not any(isinstance(v,str) and v.strip() not in ("","未提及") for v in values):
            return None
        return json.dumps(summary,ensure_ascii=False)

    # 缓存过期瞬间的并发交给 get_or_build 收敛：一个人真生成，其余人排队等同一份结果
    cached=await llm_cache.get_or_build(cache_key,build,ttl_seconds=86400)

    summary=json.loads(cached) if cached is not None else None
    if not isinstance(summary,dict):
        return {key:"未提及" for key in ("problem","method","experiment","conclusion")}
    return summary

@router.get("/{paper_id}/citations")
async def get_paper_citations(
        paper_id:int,
        user:dict=Depends(get_current_user),
        db=Depends(get_db),
)->dict:
    """引用关系列表：这篇论文引用了谁、各是什么用途。同样缓存 1 天。"""
    _get_my_paper(db, paper_id, user["user_id"])   # 批②：只放行"我库里的"论文
    # 批①：本接口会调 LLM——同上
    set_current_keys(ensure_keys(db, user["user_id"], need_llm=True))

    cache_key=_citations_key(paper_id)

    async def build()->str|None:
        from app.agents.tools import _extract_citations_tool
        citations=await _extract_citations_tool(paper_id)
        if not citations:
            return None
        # 缓存存的就是接口返回的形状 {"items": [...]}——命中路径和生成路径结构永远一致
        return json.dumps({"items":citations},ensure_ascii=False)

    cached=await llm_cache.get_or_build(cache_key,build,ttl_seconds=86400)
    if cached is None:
        return {"items":[]}     # 空结果：给前端空态，但不写缓存，下次进详情页还能重试

    items=json.loads(cached)
    # 兼容历史毒缓存：旧版本把裸 list 直接写进 Redis，命中时必须包上 items 键，
    # 否则返回值不是 dict，撞上 -> dict 的 response_model 校验直接 500
    if isinstance(items,list):
        items={"items":items}
    return items