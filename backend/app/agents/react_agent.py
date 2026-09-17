"""
Agent 主循环（手写 function calling loop）——异步流式版（FIX-1/FIX-4）

唯一的实现是 run_stream_async()：一个 async 生成器，把过程一件件"直播"出去。
旧的 run() / run_stream() 同步版已删除（少.agent_chat / test 都改为消费这个核心，
循环逻辑只剩一份，改工具行为只改这里）。

消费者（都不含第二份循环逻辑）：
    chat.py         agen()   ← HTTP SSE 直接 async for 消费
    agent_chat.py   asyncio 入口，终端 demo
    test_agent.py   收集事件后断言

事件四种（谁消费谁翻译成 JSON 推给浏览器，见 chat.py）：
    {"type":"tool_call",    "name":..., "arguments":...}   模型申请调工具
    {"type":"tool_result",  "name":..., "summary":...}     工具执行完（摘要，防爆屏）
    {"type":"content",      "text":...}                    回答文本片段
    {"type":"todo",         "todos":[...]}                 任务清单更新（多步任务的计划/进度）

关键设计（五条铁律）：
    1. 真流式：最终回答圈的 delta.content 到手【当场 yield】，不攒完再吐
    2. async 上下文里所有同步 I/O 一律处理：
       - memory.add（同步 SQLAlchemy）→ asyncio.to_thread 包住
       - dispatch 已是 async（工具链全异步：AsyncOpenAI LLM 调用、
         ddgs 搜索 to_thread）→ 直接 await，不再占线程池
       不包/不 await 的话事件循环被占死，第二个并发请求的首 token 会被
       第一个请求的工具执行拖延（并发验收必挂）
    3. 上下文压缩：每次 LLM 调用前跑 ContextCompactor.prepare（裁剪本轮超长结果 +
       老化已消费的旧结果）；上下文超限报错时 emergency 压缩后重试一次
       —— 借鉴 Claude Code context compact 机制，实现见 app/agents/context.py
    4. 工具并发调度：一轮内多工具申请用 asyncio.gather 并发执行（保序回填、
       信号量限流保护上游 API）
    5. 任务清单（借鉴 s05 TodoWrite）：多步问题模型可调 todo_write 维护执行计划，
       清单状态实时直播（todo 事件，每运行一个实例——多会话绝不共享）；
       连续 3 轮工具调用未碰清单 → 尾附软提醒
"""

import asyncio
import time
from collections.abc import AsyncIterator

from app.config import settings
from app.services.llm import get_async_client
from app.agents.prompts import AGENT_SYSTEM_PROMPT
from app.agents.memory import ChatMemory
from app.agents.context import ContextCompactor
from app.agents.todo import TodoManager
from app.agents.tools import TOOLS_SCHEMA, dispatch

MAX_ROUNDS = 10
TOOL_CONCURRENCY = 3   # 单轮内并发执行的工具数上限（保护上游 API，防模型批量申请打爆）


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
        self._compactor = ContextCompactor()
        self._tool_sem = asyncio.Semaphore(TOOL_CONCURRENCY)  # 工具并发闸门（单轮最多同时打 3 个上游）
        self._todos = TodoManager()     # 任务清单（run_stream_async 开始时重置为全新实例）
        self.stats: dict = {}   # 本次运行的埋点（轮数/usage/压缩/批次/清单），脚本和测试读这里

    def load_history(self, items: list[dict]) -> None:
        """把 DB 里的历史一次性灌进记忆窗口（chat 路由每个请求开始时调用）。
        代理到 ChatMemory.load_history —— 消费方不必知道内部结构"""
        self._memory.load_history(items)

    def history(self) -> list[dict]:
        """取最近消息（OpenAI messages 格式）"""
        return self._memory.history()

    async def run_stream_async(self, user_input: str, allowed_paper_ids: list[int] | None = None) -> AsyncIterator[dict]:
        """流式主循环：不吐纯文本，吐"事件字典"。
        :param user_input: (str) 用户这轮说的话
        :param allowed_paper_ids: (list[int]|None) 会话 scope——检索只允许落在这几篇论文里。
            ★ 由服务端注入（chat.py 从 conversation_papers 查出来传进来），
        :yields: (dict) 事件字典，四种类型见文件头注释
        """
        # scope 存实例属性，dispatch 时由 execute 工具方法读取（避免改 dispatch 全局签名）
        self._allowed_paper_ids = allowed_paper_ids
        # 埋点：轮数 / usage / 压缩 / 工具批次 / 清单（脚本和测试读这里；不影响主流程）
        self.stats = {"rounds": 0, "usage": [], "compactions": [], "reactive_compactions": [],
                      "tool_batches": [], "todos": [], "todo_reminders": 0}
        # 任务清单：每次问答运行一个全新实例（多会话并发安全：绝不跨请求共享状态）
        self._todos = TodoManager()
        rounds_since_todo = 0   # 连续多少轮工具调用没更新过清单（s05 式提醒计数）
        # 1. 组装消息列表（草稿纸）：system + 历史 + 新问题
        messages = [
            {"role": "system", "content": self.system_prompt},
            *self._memory.history(),
            {"role": "user", "content": user_input},
        ]

        for rd in range(1, self.max_rounds + 1):
            self.stats["rounds"] = rd
            # 上下文压缩管线：每次 LLM 调用前跑一遍
            info = self._compactor.prepare(messages)
            if info["changed"]:
                self.stats["compactions"].append({"round": rd, **info})
                if self.verbose:
                    print(f"[compact] round={rd} 裁剪={info['clipped']} 老化={info['aged']} "
                          f"字符 {info['chars_before']:,}→{info['chars_after']:,}")

            # 异步流式调用：事件循环托管，期间不占线程池（超限报错有反应式压缩兜底）
            stream = await self._create_stream_with_retry(messages, rd)

            content_parts = []
            calls = {}          # tool_calls 片段累积槽 {index: {"id","name","args"}}
            new_tool_names = [] # 本圈新出现的工具名（保持 yield 顺序用）
            usage_seen = None   # 本圈 usage（循环后统一记录）

            async for chunk in stream:
                # usage 埋点：每个 chunk 都看一眼，取最后一个非空
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    usage_seen = usage
                # 空 choices 的 chunk 跳过（防 IndexError）
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                #content 到手【当场 yield】，不攒
                if delta.content:
                    yield {"type": "content", "text": delta.content}
                    content_parts.append(delta.content)

                # 同一 chunk 可能同时带 content 和 tool_calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        # {index:{"id": "", "name": "", "args": ""}}
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
                        # 新工具首次出现：记下名字，圈结束后按出现顺序返回前端
                        if tc.index not in new_tool_names:
                            new_tool_names.append(tc.index)

            # 本圈 usage 进埋点：prompt/total + cached（缓存命中 token）
            if usage_seen is not None:
                self.stats["usage"].append({
                    "round": rd,
                    "prompt": getattr(usage_seen, "prompt_tokens", None),
                    "total": getattr(usage_seen, "total_tokens", None),
                    "cached": getattr(usage_seen, "prompt_cache_hit_tokens", None),
                })

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

            # 2b. 先广播全部申请（工具面板这一圈同时亮 N 个）——申请之间无依赖，可并发
            for call in tool_calls_list:
                yield {"type": "tool_call",
                       "name": call["function"]["name"],
                       "arguments": call["function"]["arguments"]}

            # 2c. 并发执行：gather 保序（第 i 个结果对应第 i 条申请），信号量限流；
            #     dispatch 内部已全捕获异常（返回错误说明文字），gather 不会炸
            batch_t0 = time.perf_counter()
            results = await asyncio.gather(*[
                self._dispatch_limited(call["function"]["name"], call["function"]["arguments"])
                for call in tool_calls_list
            ])
            self.stats["tool_batches"].append({
                "round": rd,
                "count": len(tool_calls_list),
                "seconds": round(time.perf_counter() - batch_t0, 2),
            })

            # 2d. 按申请顺序回填消息（tool_call_id 配对协议）+ 直播结果
            used_todo = False
            for call, result in zip(tool_calls_list, results):
                name = call["function"]["name"]
                yield {"type": "tool_result",
                       "name": name,
                       "summary": f"{name} 执行完成，结果 {len(result)} 字符"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],      # 告诉模型这是哪条申请的结果
                    "content": result,
                })
                # 任务清单直播：更新后把结构化快照推给前端 + 埋点（s05 借鉴）
                if name == "todo_write":
                    used_todo = True
                    snapshot = self._todos.snapshot()
                    self.stats["todos"].append(snapshot)
                    yield {"type": "todo", "todos": snapshot}
                    if self.verbose:
                        done = sum(t["status"] == "completed" for t in snapshot)
                        print(f"[todo] 清单更新：{done}/{len(snapshot)} 已完成")

            # 2e. 提醒计数器（借鉴 s05）：连续 3 轮工具调用没碰过清单 → 尾附软提醒后归零
            rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
            if rounds_since_todo >= 3:
                rounds_since_todo = 0
                self.stats["todo_reminders"] += 1
                messages[-1]["content"] += ("\n\n<reminder>如任务还有未完成的步骤，"
                                            "记得用 todo_write 更新清单；简单问题可忽略本提醒。</reminder>")
            # 2f. 塞完结果，回到循环开头再调 LLM：它看了结果决定继续要工具还是作答

        # 3. 跑满圈数模型还不停：强制中止，别让用户干等（兜底也要落记忆）
        fallback = "（工具调用轮数超出上限，已中止。请换个问法试试）"
        yield {"type": "content", "text": fallback}
        await asyncio.to_thread(self._memory.add, "user", user_input)
        await asyncio.to_thread(self._memory.add, "assistant", fallback)

    # ---------- 内部：工具并发调度 ----------

    async def _dispatch_limited(self, name: str, arguments: str) -> str:
        """带并发上限的工具执行（一轮内最多 TOOL_CONCURRENCY 个同时打上游）。
        dispatch 内部已全捕获异常、返回错误说明文字，所以 gather 不会炸。"""
        async with self._tool_sem:
            return await dispatch(name, arguments, allowed_paper_ids=self._allowed_paper_ids,
                                  todo_state=self._todos)

    # ---------- 内部：LLM 调用与反应式压缩兜底 ----------

    async def _create_stream_with_retry(self, messages: list[dict], rd: int):
        """流式调用 + 反应式压缩兜底（借鉴 s08 reactive compact）：
        上下文超限错误 → 紧急压掉全部已消费旧结果 → 重试一次（再炸就抛给上层）。
        这是估算偏保守时的保险丝，平时不触发。"""
        try:
            return await self._create_stream(messages)
        except Exception as error:
            if not self._is_context_length_error(error):
                raise
            info = self._compactor.emergency(messages)
            self.stats["reactive_compactions"].append({"round": rd, **info})
            if self.verbose:
                print(f"[reactive-compact] round={rd} 紧急压缩 {info['aged']} 条旧结果 "
                      f"字符 {info['chars_before']:,}→{info['chars_after']:,}，重试一次")
            return await self._create_stream(messages)

    @staticmethod
    async def _create_stream(messages: list[dict]):
        """流式请求本体（抽出来供重试复用）"""
        return await get_async_client().chat.completions.create(
            model=settings.LLM_MODEL,
            temperature=settings.LLM_TEMPERATURE,
            messages=messages,
            tools=TOOLS_SCHEMA,
            stream=True,
            stream_options={"include_usage": True},  # 流式也要 usage（埋点用；默认流式不带）
        )

    @staticmethod
    def _is_context_length_error(error: Exception) -> bool:
        """错误文本是否属于"上下文超限"类（各家报错措辞不同，弱特征匹配）"""
        text = str(error).lower()
        return any(marker in text for marker in (
            "prompt is too long", "prompt_too_long", "context length",
            "context_length_exceeded", "maximum context", "too many tokens",
            "reduce the length",
        ))
