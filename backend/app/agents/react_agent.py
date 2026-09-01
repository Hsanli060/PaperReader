"""
Agent 主循环（手写 function calling loop）
run() 一次的流程：组装消息 → 调 LLM → 模型要工具就执行、结果塞回去再调，
直到模型直接给出回答；草稿纸上的中间消息用完即丢，只有问答本身进记忆
"""

import json
from app.config import settings
from app.services.llm import client
from app.agents.prompts import AGENT_SYSTEM_PROMPT
from app.agents.memory import ChatMemory
from app.agents.tools import TOOLS_SCHEMA, dispatch

MAX_ROUNDS=10
class ReactAgent:
    def __init__(self,system_prompt:str=AGENT_SYSTEM_PROMPT,max_rounds:int=MAX_ROUNDS,verbose:bool=False):
        """
        :param system_prompt: (str) 系统提示词
        :param max_rounds: (int) 循环圈数上限
        :param verbose: (bool) True 时把每圈动作打印到终端，demo 时能看见循环在转
        """
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds
        self.verbose = verbose
        self._memory=ChatMemory()  # 跨轮记忆，只存 user/assistant；3.3 课换成 ChatMemory

    def run(self,user_input:str)->str:
        """回答一句话（内部可能循环多次调 LLM 和工具）。

        :param user_input: (str) 用户这轮说的话
        :return: (str) 模型的最终回答
        """
        # 1. 组装消息列表（草稿纸）：system + 历史 + 新问题
        #    历史用 * 解包摊平进去，保持 messages 是一层扁平的 dict 列表
        messages=[
            {"role":"system","content":self.system_prompt},
            *self._memory.history(),
            {"role":"user","content":user_input},
        ]

        #主循环
        for rd in range(1,self.max_rounds+1):
            resp=client.chat.completions.create(
                model=settings.LLM_MODEL,
                temperature=settings.LLM_TEMPERATURE,
                messages=messages,
                tools=TOOLS_SCHEMA,
            )
            m=resp.choices[0].message

            # 情况 1：模型不调工具，直接回答 → 循环结束
            if not m.tool_calls:
                answer=m.content or ""
                self._memory.add("user",user_input)
                self._memory.add("assistant",answer)
                return answer

            # 情况 2：模型申请调工具（可能一次申请多个，tool_calls 是列表）
            if self.verbose:
                print(f"[第{rd}圈] 模型申请调用：{[tc.function.name for tc in m.tool_calls]}")

            # 2a. 先把模型的"调用申请"补进消息列表。
            #     协议要求：后面每条 role="tool" 的结果，必须能和这里某条申请对上
            #     （靠 tool_call_id 配对），所以申请消息不能省
            messages.append({
                "role":"assistant",
                "content":m.content or "",
                "tool_calls":[
                    {
                        "id":tc.id,
                        "type":"function",
                        "function":{"name":tc.function.name,"arguments":tc.function.arguments},
                    }for tc in m.tool_calls
                ],
            })

            # 2b. 逐个执行申请，结果按同顺序塞回去
            for tc in m.tool_calls:
                #遍历模型想要调用的工具列表并执行工具
                result=dispatch(tc.function.name,tc.function.arguments)
                if self.verbose:
                    print(f"[第{rd}圈]   {tc.function.name} 执行完，结果 {len(result)} 字符")
                messages.append({
                    "role":"tool",
                    "tool_call_id":tc.id,       # 告诉 模型 这是哪条申请的结果
                    "content":result
                })
            # 2c. 塞完结果，回到循环开头再调 LLM：它看了结果决定继续要工具还是作答

        # 3. 跑满圈数模型还不停：强制中止，别让用户干等
        return "（工具调用轮数超出上限，已中止。请换个问法试试）"

    def run_stream(self,user_input:str):
        messages=[
            {"role": "system", "content": self.system_prompt},
            *self._memory.history(),
            {"role": "user", "content": user_input},
        ]

        for rd in range(1,self.max_rounds+1):
            response=client.chat.completions.create(
                model=settings.LLM_MODEL,
                temperature=settings.LLM_TEMPERATURE,
                messages=messages,
                tools=TOOLS_SCHEMA,
                stream=True,
            )
            content_parts=[]
            calls={}
            for chunk in response:
                delta=chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        #将调用的函数信息存入字典中
                        slot=calls.setdefault(tc.index,{"id":"","name":"","args":""})
                        if tc.id:
                            slot["id"]=tc.id
                        if tc.function and tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
            content="".join(content_parts)

            if not calls:
                answer=content
                for i in range(0,len(answer),24):
                    yield answer[i:i+24]
                # 存记忆
                self._memory.add("user",user_input)
                self._memory.add("assistant",answer)
                return
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"id": slot["id"], "type": "function",
                         "function": {"name": slot["name"], "arguments": slot["args"]}}
                        for _, slot in sorted(calls.items())
                    ],
                }
            )

            for _,slot in sorted(calls.items()):
                result=dispatch(slot["name"],slot["args"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": slot["id"],
                    "content": result,
                })
        yield "（工具调用轮数超出上限，已中止。请换个问法试试）"