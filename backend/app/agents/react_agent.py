"""
Agent 主循环（手写 function calling loop）——异步流式版（FIX-1/FIX-4）

唯一的实现是 run_stream_async()：一个 async 生成器，把过程一件件"直播"出去。
旧的 run() / run_stream() 同步版已删除（少.agent_chat / test 都改为消费这个核心，
循环逻辑只剩一份，改工具行为只改这里）。

消费者（都不含第二份循环逻辑）：
    chat.py         agen()   ← HTTP SSE 直接 async for 消费
    agent_chat.py   asyncio 入口，终端 demo
    test_agent.py   收集事件后断言

事件三种（谁消费谁翻译成 JSON 推给浏览器，见 chat.py）：
    {"type":"tool_call",    "name":..., "arguments":...}   模型申请调工具
    {"type":"tool_result",  "name":..., "summary":...}     工具执行完（摘要，防爆屏）
    {"type":"content",      "text":...}                    回答文本片段

关键设计（两条铁律）：
    1. 真流式：最终回答圈的 delta.content 到手【当场 yield】，不攒完再吐
    2. async 上下文里所有同步 I/O 一律 asyncio.to_thread 包住：
       dispatch（内部有 ChromaDB 同步查询、LLM 同步调用、httpx 同步下载）、
       memory.add（内部有同步 SQLAlchemy 写入）——不包的话事件循环被占死，
       第二个并发请求的首 token 会被第一个请求的工具执行拖延（并发验收必挂）
"""

import asyncio
from collections.abc import AsyncIterator

from app.config import settings
from app.services.llm import async_client
from app.agents.prompts import AGENT_SYSTEM_PROMPT
from app.agents.memory import ChatMemory
from app.agents.tools import TOOLS_SCHEMA, dispatch

MAX_ROUNDS = 10


class ReactAgent:
    def __init__(self, system_prompt: str = AGENT_SYSTEM_PROMPT, max_rounds: int = MAX_ROUNDS, verbose: bool = False):
        """
        :param system_prompt: (str) 系统提示词
        :param max_rounds: (int) 循环圈数上限
        :param verbose: (bool) True 时把每圈动作打印到终端，demo 时能看见循环在转
        """
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds
        self.verbose = verbose
        self._memory = ChatMemory()

    def load_history(self, items: list[dict]) -> None:
        """把 DB 里的历史一次性灌进记忆窗口（chat 路由每个请求开始时调用）。
        代理到 ChatMemory.load_history —— 消费方不必知道内部结构"""
        self._memory.load_history(items)

    def history(self) -> list[dict]:
        """取最近消息（OpenAI messages 格式）"""
        return self._memory.history()

    async def run_stream_async(self, user_input: str) -> AsyncIterator[dict]:
        """流式主循环：不吐纯文本，吐"事件字典"。

        一个 async 生成器，谁消费谁负责把事件转成前端能懂的数据。
        每圈：流式调 LLM（AsyncOpenAI, stream=True）→
            - delta.content 到手当场 yield（真流式，不攒完再吐）
            - delta.tool_calls 按 index 分槽累积（id/name/arguments 逐片拼）
        圈结束：有工具申请 → 补申请消息、执行工具（to_thread）、结果回填 → 下一圈
                没有工具申请 → 已逐字吐完，收工落记忆

        :param user_input: (str) 用户这轮说的话
        :yields: (dict) 事件字典，三种类型见文件头注释
        """
        # 1. 组装消息列表（草稿纸）：system + 历史 + 新问题
        messages = [
            {"role": "system", "content": self.system_prompt},
            *self._memory.history(),
            {"role": "user", "content": user_input},
        ]

        for rd in range(1, self.max_rounds + 1):
            # ★ 异步流式调用：事件循环托管，期间不占线程池
            stream = await async_client.chat.completions.create(
                model=settings.LLM_MODEL,
                temperature=settings.LLM_TEMPERATURE,
                messages=messages,
                tools=TOOLS_SCHEMA,
                stream=True,
            )

            content_parts = []
            calls = {}          # tool_calls 片段累积槽 {index: {"id","name","args"}}
            new_tool_names = [] # 本圈新出现的工具名（保持 yield 顺序用）

            async for chunk in stream:
                # 末尾的 usage-only chunk 没有 choices，跳过（防 IndexError）
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # ★★★ FIX-1 的心脏：content 到手【当场 yield】，不攒
                if delta.content:
                    yield {"type": "content", "text": delta.content}
                    content_parts.append(delta.content)

                # 同一 chunk 可能同时带 content 和 tool_calls —— 两个都处理，别 continue 吞掉
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
                        # 新工具首次出现：记下名字，圈结束后按出现顺序直播
                        if tc.index not in new_tool_names:
                            new_tool_names.append(tc.index)

            content = "".join(content_parts)

            # 情况 1：本圈没有工具申请 → 最终回答圈（已经逐字吐过了），收工落记忆
            if not calls:
                # 同步 SQLAlchemy 写入：to_thread 包住，不占事件循环
                await asyncio.to_thread(self._memory.add, "user", user_input)
                await asyncio.to_thread(self._memory.add, "assistant", content)
                return

            # 情况 2：本圈有工具申请 → 补申请消息 + 执行工具 + 回填结果
            # 先把申请排成有序列表（按 index 排序，协议要求结果与申请按序配对）
            tool_calls_list = [
                {"id": slot["id"], "type": "function",
                 "function": {"name": slot["name"], "arguments": slot["args"]}}
                for _, slot in sorted(calls.items())
            ]

            # 2a. 申请消息进草稿纸（协议要求：tool 结果必须能和某条申请对上号）
            #     content 是模型的中间自言自语，也要带上保持上下文连贯（可为空串）
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls_list,
            })

            # 2b. 逐个执行申请，结果按同顺序塞回去
            for call in tool_calls_list:
                name = call["function"]["name"]
                args = call["function"]["arguments"]

                # 直播：先报"模型要调工具"（工具面板亮起的时机）
                yield {"type": "tool_call", "name": name, "arguments": args}

                # ★ dispatch 是同步的（内部含 ChromaDB 同步查询 / LLM 调用 / httpx），
                #   to_thread 丢线程池执行，工具跑 10-30 秒期间事件循环继续服务其他请求
                result = await asyncio.to_thread(dispatch, name, args)

                # 直播：工具执行完（摘要，防爆屏）
                yield {"type": "tool_result",
                       "name": name,
                       "summary": f"{name} 执行完成，结果 {len(result)} 字符"}

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],      # 告诉模型这是哪条申请的结果
                    "content": result,
                })
            # 2c. 塞完结果，回到循环开头再调 LLM：它看了结果决定继续要工具还是作答

        # 3. 跑满圈数模型还不停：强制中止，别让用户干等（兜底也要落记忆）
        fallback = "（工具调用轮数超出上限，已中止。请换个问法试试）"
        yield {"type": "content", "text": fallback}
        await asyncio.to_thread(self._memory.add, "user", user_input)
        await asyncio.to_thread(self._memory.add, "assistant", fallback)
