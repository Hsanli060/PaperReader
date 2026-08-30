"""
按论文章节切块：整篇 Markdown → 一盒盒带元数据的语义块
策略：章节优先（标题层级 1-2 当边界），章内超长再按段落定量切，切缝留重叠
"""
from app.config import settings


def split_paper(text:str,headings:list[str])->list[dict]:
    """把整篇 Markdown 论文切成带章节元数据的语义块。

    :param text: (str)整篇论文的 Markdown 文本
    :param headings: (list[str]) 标题行列表（切分逻辑不看它，仅供调试对照）
    :return: list[dict]，每块键固定为 text / section：
        [
            {"text": "块内容（长度 ≤ CHUNK_SIZE 上下浮动）",
             "section": "所属章节标题，如 '3 Selective State Space Models'"},
            ...
        ]
    """
    sections=_find_sections(text)

    all_chunks=[]
    for title,content in sections:
        all_chunks.extend(          #通过从可迭代对象中追加元素来扩展列表
            _split_long_section(title,content)
        )
    return all_chunks

def _find_sections(text:str)->list[tuple[str,str]]:
    """整篇文本 → 章节列表。
    规则：
        '# '/'## ' 开头的行开新章；普通行归当前章；
        第一个标题之前的内容归 "Preamble"（前言块）。
    :param text: (str) 整篇论文的 Markdown 文本
    :return: list[tuple[str, str]]，每个元素是 (章节标题, 章节全文)：
        [("Preamble", "标题/作者/摘要..."), ("1 Introduction", "..."), ...]
    """
    sections=[]                 #以元组的形式包含多个章节标题和内容 （标题，该标题下的内容）
    current_title="Preamble"
    current_lines=[]            #一行一行的增加数据

    for line in text.splitlines():      #按行进行分割
        s=line.strip()
        # 只认 '#'/'##' 开头（后跟空格）的行当章节边界；
        # '### ' 是小节/作者行，归入当前章节内容（### 必然也命中 startswith("# ")，
        # 所以必须先把 ### 排除掉）
        if (s.startswith("# ") or s.startswith("## ")) and not s.startswith("### "):
            if current_lines:
                sections.append((current_title,"\n\n".join(current_lines)))
            # 洗标题：'## **1 Introduction**' → '1 Introduction'
            current_title=s.strip("#* ").strip()
            current_lines=[]
        else:
            current_lines.append(line)

    # 最后一章没有"下一行标题"来触发收工，循环结束后补收一次
    if current_lines:
        sections.append((current_title,"\n\n".join(current_lines)))

    return sections

def _split_long_section(title:str,content:str)->list[dict]:
    """单个章节 → 块列表。

    :param title: (str) 章节标题，如 "1 Introduction"
    :param content: (str) 该章全文
    :return: list[dict]：[{"text": 块内容, "section": title}, ...]
        章 ≤ CHUNK_SIZE → 原样一块；超限 → 按空行切段装箱（切缝留 overlap）。
    """
    if len(content)<=settings.CHUNK_SIZE:
        return [{"text":content,"section":title}]

    chunks=[]
    box=""       # 正在装的箱（只此一个身份）
    carry=""     # 封箱时留下的前情回顾，并入下一个新箱

    def _seal():
        """封箱：箱子收进结果 → box 清空 → 本箱末尾存为前情回顾"""
        nonlocal box,carry
        chunks.append({"text":box,"section":title})
        carry=box[-settings.CHUNK_OVERLAP:]
        box=""

    def _pack(piece:str,from_chop:bool=False):
        """把一段内容装进当前箱；装不下先封箱；装完若已满立即封箱

        from_chop=True 表示这段来自硬剁（para 切片）。硬剁的切缝已自带
        CHUNK_OVERLAP 回退（下一刀开头重复上一刀末尾 150 字符），
        若再并入 carry 会叠成双重 overlap（同一段内容块内出现两次），
        所以硬剁 piece 装箱时跳过 carry。
        """
        nonlocal box,carry
        if box and len(box)+len(piece)>settings.CHUNK_SIZE:
            _seal()
        if box:
            box=box+"\n\n"+piece
        elif from_chop:
            box=piece                       # 硬剁段：不并 carry（刀缝已有 overlap）
        else:
            box=carry+"\n\n"+piece if carry else piece
        carry=""     # 回顾已并入箱子（或被跳过），用掉即清
        if len(box)>settings.CHUNK_SIZE:
            _seal()

    for para in content.split("\n\n"):
        para=para.strip()
        if not para:
            continue

        # 超长段落：先按 CHUNK_SIZE 硬剁（切缝带 overlap），剁出的段逐个流入装箱流水线。
        # 注意：while 退出后的余段开头 150 字符仍是刀缝回退部分（它切自上一刀末尾 150 前），
        # 所以余段同样算"硬剁段"，from_chop 必须为 True，否则会并入 carry 造成双重 overlap
        chopped=False
        while len(para)>settings.CHUNK_SIZE:
            _pack(para[:settings.CHUNK_SIZE],from_chop=True)
            para=para[settings.CHUNK_SIZE-settings.CHUNK_OVERLAP:]
            chopped=True
        if para:
            _pack(para,from_chop=chopped)

    if box:
        chunks.append({"text":box,"section":title})

    return chunks