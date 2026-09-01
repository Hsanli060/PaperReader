"""
流水线总入口：一条命令跑通 fetch → parse → split
用法：cd backend && python run.py --arxiv 2312.00752
"""

import argparse
import hashlib
import json
from collections import Counter

from sqlalchemy import select

from app.models.database import SessionLocal
from app.models.orm import User,Paper
from app.paper.fetcher import fetch_paper
from app.paper.paper_parser import parse_pdf
from app.rag.splitter import split_paper
from app.rag.vector_store import index_paper, get_stats


def get_or_create_user(db,username:str)->User:
    """查找用户，无就创建用户

    :param db: (Session) SQLAlchemy 会话
    :param username: (str) 用户名，如 "demo"
    :return:  User，ORM 对象（commit 后 id 已回填）：
        User(id=1, username="demo", password_hash="1a2b3c...")
    """
    user=db.scalar(select(User).where(User.username==username))
    if user:
        return user

    user=User(username=username,password_hash=hashlib.sha256(b"demo123").hexdigest())
    db.add(user)
    db.commit()
    return user

def upsert_paper(db,user:User,meta:dict)->Paper:
    """把 获取到的论文存入pg数据库（有则更新，无则插入），状态置 downloaded。

    :param db: (Session) SQLAlchemy 会话
    :param user: (User) 论文归属的种子用户
    :param meta: (dict) fetch_paper 的返回，键含 arxiv_id/title/authors/abstract/pdf_path
    :return: Paper，ORM 对象（已 commit，id 已回填）：
        Paper(id=1, arxiv_id="2312.00752", status="downloaded")
    """
    # demo 种子用户自己范围内去重（和 API 的 /arxiv 同一思路）
    result=db.scalars(
        select(Paper).where(Paper.user_id==user.id,Paper.arxiv_id==meta["arxiv_id"])
    ).first()
    #更新数据
    if result:
        result.title=meta["title"]
        result.abstract=meta["abstract"]
        result.pdf_path=meta["pdf_path"]
        result.status="downloaded"
        paper=result
    else:
        paper=Paper(
            user_id=user.id,
            arxiv_id=meta["arxiv_id"],
            title=meta["title"],
            authors=json.dumps(meta["authors"],ensure_ascii=False),
            abstract=meta["abstract"],
            pdf_path=meta["pdf_path"],
            status="downloaded",
        )
    db.add(paper)
    db.commit()     # 下载完成即存档：后面 parse 炸了，这条记录也已经在库里
    return paper


def print_stats(text:str,headings:list[str],chunks:list[dict])->None:
    """把流水线产出打印成统计报告（给流水线一个看得见的终点）。

    :param text: (str) 整篇 Markdown 文本（统计字符数用）
    :param headings: (list[str]) 标题行列表
    :param chunks: (list[dict]) split_paper 的产出，[{"text":..., "section":...}, ...]
    :return: None
    """

    #所有块长
    lengths=[len(c["text"]) for c in chunks]

    #统计某个字符串出现的次数，结果按“频次从大到小”降序排列，取前5个，list[Tuple(str,int)]
    top5=Counter(c["section"] for c in chunks).most_common(5)
    print(f"Markdown 字符数：{len(text)}")
    print(f"标题数：{len(headings)}")
    print(f"块长 平均 {sum(lengths)//len(lengths)} / 最大 {max(lengths)} / 最小 {min(lengths)}")
    print("章节分布 TOP5：")
    for section, count in top5:  # 每项是 (章节名, 票数)，顺手解包
        print(f"  {count:>4} 块  {section}")


def main()->None:
    """流水线主流程：建用户 → fetch → 入库(downloaded) → parse → split → 状态(parsed)。

    用法（argparse 从终端收参数）：
        python run.py --arxiv 2312.00752 [--user demo]
        --arxiv 必填，论文 ID 或链接；--user 选填，种子用户名，默认 "demo"
    返回：
        None（统计报告打印到控制台）
    """
    # 1. argparse：--arxiv 必填，--user 选填默认 "demo"
    #    parser = argparse.ArgumentParser()
    #    parser.add_argument("--arxiv", required=True)
    #    parser.add_argument("--user", default="demo")
    #    args = parser.parse_args()

    # 2. 开 session（记得 try/finally 里 db.close()，脚本也一样要守规矩）
    # 3. user = get_or_create_user(db, args.user)
    # 4. meta = fetch_paper(args.arxiv)                # 网络最慢的一步
    # 5. paper = upsert_paper(db, user, meta)          # 状态 → downloaded
    # 6. parsed = parse_pdf(paper.pdf_path)            # 有 .md 缓存，第二次飞快
    # 7. chunks = split_paper(parsed["text"], parsed["headings"])
    # 8. paper.status = "parsed"; db.commit()          # 点亮第三格
    # 9. print_stats(...) + 打印 paper.id（第八课 API 要用它当门牌号）

    #方便在终端中传入arxiv、user等参数
    parser=argparse.ArgumentParser()
    parser.add_argument("--arxiv",required=True)
    parser.add_argument("--user", default="demo")
    args = parser.parse_args()

    db=SessionLocal()
    try:
        #先创建用户、下载论文，然后存储论文（或更新）
        user=get_or_create_user(db,args.user)
        meta=fetch_paper(args.arxiv)
        paper=upsert_paper(db,user,meta)

        #解析论文->md格式
        parsed=parse_pdf(paper.pdf_path)
        #论文切片
        chunks=split_paper(text=parsed["text"],headings=parsed["headings"])
        #更新论文状态
        paper.status="parsed"
        db.commit()

        #将切块存入向量数据库
        index_paper(paper.id,chunks)
        paper.status="indexed"
        db.commit()

        print_stats(text=parsed["text"],headings=parsed["headings"],chunks=chunks)
        stats=get_stats()
        print(f"向量库：共 {stats['total']} 块，分布 {stats['per_paper']}")
        print(f"\npaper.id = {paper.id}")

    finally:
        # 不管流水线中途炸没炸，电话必须挂：防连接泄漏
        db.close()


if __name__ == "__main__":
    main()