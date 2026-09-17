"""
任务清单（TodoWrite 机制，借鉴 learn-claude-code s05）

用途：多步问题（跨论文对比、多部分综述）时，模型用 todo_write 工具维护一份
执行计划——先列全部步骤（pending），再边做边更新（in_progress / completed）。

设计要点（与 s05 的区别见讨论稿）：
    - 状态随"每次问答运行"一个实例（react_agent 每轮新建），不跨请求串数据
      —— s05 是单用户终端所以用全局单例；我们是多会话并发服务，必须请求级隔离
    - 更新是整体替换（非增量 patch）：模型每次提交完整清单
    - 校验规则（照搬 s05）：最多 20 项 / content 非空 / 同时只能一项 in_progress
    - 渲染成紧凑文本回给模型（[ ] 待办、[>] 进行中、[x] 已完成）
"""
import ast
import json

VALID_STATUSES = ("pending", "in_progress", "completed")


class TodoManager:
    def __init__(self):
        self.items: list[dict] = []

    def update(self, todos: list | str) -> str:
        """校验并整体替换清单，返回渲染文本（给模型看的紧凑状态）"""
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)      # 接受 Python 列表字面量，不用 eval
                except (SyntaxError, ValueError) as error:
                    raise ValueError("todos 必须是列表或 JSON 数组字符串") from error

        if not isinstance(todos, list):
            raise ValueError("todos 必须是列表")
        if len(todos) > 20:
            raise ValueError("任务清单最多 20 项")

        validated = []
        in_progress_count = 0
        for index, todo in enumerate(todos):
            if not isinstance(todo, dict):
                raise ValueError(f"第 {index} 项必须是对象（含 content 和 status）")
            content = str(todo.get("content", "")).strip()
            status = str(todo.get("status", "pending")).lower()
            if not content:
                raise ValueError(f"第 {index} 项缺少 content")
            if status not in VALID_STATUSES:
                raise ValueError(f"第 {index} 项状态非法：{status!r}（可选 pending / in_progress / completed）")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content": content, "status": status})

        if in_progress_count > 1:
            raise ValueError("同一时间只能有一项处于 in_progress")
        self.items = validated
        return self.render()

    def render(self) -> str:
        """渲染给模型的紧凑状态文本"""
        if not self.items:
            return "（任务清单为空）"
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines = [f"{marker[item['status']]} {item['content']}" for item in self.items]
        done = sum(item["status"] == "completed" for item in self.items)
        lines.append(f"\n（已完成 {done}/{len(self.items)}）")
        return "\n".join(lines)

    def snapshot(self) -> list[dict]:
        """结构化快照（todo 事件 / 埋点用；返回拷贝，外部改不到内部）"""
        return [dict(item) for item in self.items]
