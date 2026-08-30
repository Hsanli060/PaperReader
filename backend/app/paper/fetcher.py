# arXiv API 抓取元数据 + 下载 PDF
import re
from pathlib import Path

import arxiv
import httpx

from app.config import settings


def extract_arxiv_id(url_or_id:str)->str:
    """从各种形态的输入（链接或纯 ID）中提取 arXiv 论文 ID。

    :param url_or_id: 论文链接（最后必须是一串数据或数据加字母）
    :return: str，论文 ID（含版本号），如 "2401.12345v2"
    """
    m=re.search(r"\d{4}\.\d{4,5}(v\d+)?",url_or_id)
    if not m:
        raise ValueError(f"不是合法的 arXiv ID：{url_or_id}")
    return m.group(0)

def fetch_paper(arxiv_id:str)->dict:
    """抓取论文元数据，并把 PDF 下载到本地 PAPERS_DIR。

    :param arxiv_id: arxiv_id (str): 论文 ID，如 "2312.00752"
    :return: dict，键固定为 arxiv_id / title / authors / abstract / pdf_path：
        {
            "arxiv_id": "2312.00752",
            "title": "Mamba: Linear-Time Sequence Modeling with Selective State Spaces",
            "authors": ["Albert Gu", "Tri Dao"],
            "abstract": "Foundation models...（摘要全文）",
            "pdf_path": "./data/papers/2312.00752.pdf",
        }
    """
    # 1 拿元数据
    search=arxiv.Search(id_list=[arxiv_id])         #根据指定条件构建一个arXiv API搜索。
    results=list(arxiv.Client().results(search))    #迭代器返回多篇论文
    if not results:
        raise ValueError(f"arXiv 上找不到论文：{arxiv_id}")
    paper=results[0]

    # 2 确保存储目录存在
    papers_dir=Path(settings.PAPERS_DIR)        #创建存储地址
    papers_dir.mkdir(parents=True,exist_ok=True)    #递归创建目录，目录已存在则忽略，防止父级不存在抛异常

    # 3 获取论文 PDF
    resp=httpx.get(paper.pdf_url,timeout=60,follow_redirects=True)
    if resp.status_code!=200:
        raise RuntimeError(f"PDF 下载失败，状态码 {resp.status_code}：{paper.pdf_url}")

    # 4 字节流写盘
    pdf_path=papers_dir / f"{arxiv_id}.pdf"     #文件路径
    pdf_path.write_bytes(resp.content)

    # 5 打包返回
    return {
        "arxiv_id":arxiv_id,
        "title":paper.title,
        "authors":[a.name for a in paper.authors],
        "abstract":paper.summary,
        "pdf_path":str(pdf_path)
    }