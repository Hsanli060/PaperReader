"""
学术 PDF 解析：PDF → 干净的 Markdown（保留标题层级）
pymupdf4llm 负责把"打印指令"猜回"文档结构"，是流水线里最贵的一步，
所以用 .md 缓存文件避免重复解析
"""
from pathlib import Path

import pymupdf4llm

def parse_pdf(pdf_path:str)->dict:
    """解析论文从 PDF 为 Markdown格式

    :param pdf_path: (str) PDF 文件路径，如 "./data/papers/2312.00752.pdf"
    :return: dict，键固定为 text / headings / from_cache：
        {
            "text": "整篇论文的 Markdown 文本",
            "headings": ["# Mamba: Linear-Time...", "## 1 Introduction", ...],
            "from_cache": True,    # True=命中缓存，False=本次新解析
        }
    """

    # 1. 缓存路径：同名换后缀
    md_path=Path(pdf_path).with_suffix(".md")

    # 2. 命中缓存 → 判断该文件路径是否存在
    if md_path.exists():
        md_text=md_path.read_text(encoding="utf-8")
        return{
            "text":md_text,
            "headings":_extract_headings(md_text),
            "from_cache": True
        }
    #没有缓存，开始解析pdf,并写入缓存
    md_text=pymupdf4llm.to_markdown(pdf_path,show_progress=False)
    md_path.write_text(md_text,encoding="utf-8")
    return{
        "text": md_text,
        "headings": _extract_headings(md_text),
        "from_cache": False
    }


def _extract_headings(text:str)->list[str]:
    """从 Markdown 文本中提取所有标题行

    :param text: (str) 完整 Markdown 文本
    :return:  list[str]，以 '#' 开头的行（已去首尾空白）：
        ["# Mamba: Linear-Time...", "## 1 Introduction", ...]
    """
    return [line.strip() for line in text.splitlines() if line.strip().startswith("#")]