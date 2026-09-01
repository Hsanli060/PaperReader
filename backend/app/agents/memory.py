"""
对话记忆（滑动窗口）：每个 Agent 实例配一个，跨轮保存最近 N 条消息
两条设计决定：
    1. 只记 user / assistant —— 工具调用的中间过程只活在本轮循环里，
       不进记忆（检索结果一条几千字，全记窗口秒满）
    2. 存取只有 add/history 两个方法 —— 将来换 DB 存储只改这个文件，主循环不动
"""
from collections import deque
from app.config import settings

class ChatMemory:
    def __init__(self,max_messages:int|None=None):
        """max_messages=None 时用 settings.AGENT_MAX_HISTORY"""
        if max_messages is None:
            max_messages=settings.AGENT_MAX_HISTORY
        self._msgs:deque=deque(maxlen=max_messages)

    def add(self,role:str,content:str)->None:
        """添加对话记录。role 只接受 user/assistant —— 正好对齐将来 messages 表的 role 字段"""
        if role not in("user","assistant"):
            raise ValueError(f"记忆只记 user/assistant，不收 {role!r}")
        self._msgs.append({"role":role,"content":content})

    def history(self)->list[dict]:
        """取最近消息（OpenAI messages 格式，主循环直接拼进请求）。"""
        return list(self._msgs)     # 拷贝出去，外部改不动窗口本身

    def load_history(self,items:list[dict])->None:
        """把 DB 里的历史一次性灌进窗口。超出 maxlen 时 deque 自动淘汰最老的。
        谁调用它：chat 路由（每个请求开始时从 messages 表恢复记忆）"""
        self._msgs.extend(items)