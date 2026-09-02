"""FIX-3' 数据清理：合并重复论文（全局唯一化）+ 重映射老会话 scope

执行前提：迁移 A/B 之后运行（papers.user_id 已删、conversation_papers 已建）
策略：
  1. 按 arxiv_id 分组，每组保留 id 最小的一条（最老的）
  2. 重复行：先清向量块（delete_paper），再删 PG 行
  3. conversation_papers 里指向被删 paper_id 的 → 重映射到保留行（去重防冲突）
  4. 打印清理报告
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/

from sqlalchemy import select, delete

from app.models.database import SessionLocal
from app.models.orm import Paper, ConversationPaper, Conversation
from app.rag.vector_store import delete_paper as vs_delete_paper, get_stats


def main() -> None:
    db = SessionLocal()
    report = {"merged_groups": 0, "deleted_papers": [], "remapped": []}
    try:
        # 1. 按 arxiv_id 分组找重复
        papers = db.scalars(select(Paper).order_by(Paper.id.asc())).all()
        groups: dict[str, list[Paper]] = {}
        for p in papers:
            groups.setdefault(p.arxiv_id, []).append(p)

        id_map: dict[int, int] = {}   # 被删 id → 保留 id
        for arxiv_id, dup in groups.items():
            if len(dup) == 1:
                continue
            keep = dup[0]                       # id 最小的保留
            for victim in dup[1:]:
                # 2. 清向量块 + 删 PG 行
                vs_delete_paper(victim.id)
                db.delete(victim)
                id_map[victim.id] = keep.id
                report["deleted_papers"].append(
                    {"id": victim.id, "arxiv_id": victim.arxiv_id, "merged_into": keep.id}
                )
            report["merged_groups"] += 1

        db.flush()   # 先让删除生效，再处理重映射

        # 3. 重映射 conversation_papers
        for cp in db.scalars(select(ConversationPaper)).all():
            if cp.paper_id in id_map:
                target = id_map[cp.paper_id]
                # 防止重映射后 (conversation_id, target) 冲突：先查有没有
                exists = db.scalar(
                    select(ConversationPaper).where(
                        ConversationPaper.conversation_id == cp.conversation_id,
                        ConversationPaper.paper_id == target,
                    )
                )
                if exists:
                    db.delete(cp)           # 已有同目标 → 直接删这条重复
                else:
                    cp.paper_id = target
                report["remapped"].append({"conversation_id": cp.conversation_id, "to": target})

        # 老会话的单 paper_id（迁移 B 搬移时已写入 conversation_papers，这里兜底再查一遍）
        for conv in db.scalars(select(Conversation)).all():
            if getattr(conv, "paper_id", None) and conv.paper_id in id_map:
                cp = ConversationPaper(conversation_id=conv.id, paper_id=id_map[conv.paper_id])
                db.add(cp)

        db.commit()

        # 4. 报告
        print("== 清理报告 ==")
        print(f"合并组数: {report['merged_groups']}")
        for d in report["deleted_papers"]:
            print(f"  删论文 {d['id']} ({d['arxiv_id']}) → 并入 {d['merged_into']}")
        for r in report["remapped"]:
            print(f"  会话 {r['conversation_id']} 的 scope 重映射 → paper {r['to']}")
        print("向量库现状:", get_stats())
    finally:
        db.close()


if __name__ == "__main__":
    main()
