"""
终端对话入口（demo / 调试用）：读用户输入 → Agent 流式回答 → 逐字打印，循环到 quit
用法：cd backend && env -u ALL_PROXY -u all_proxy ../.venv/Scripts/python.exe agent_chat.py

FIX-4 之后消费 ReactAgent.run_stream_async（唯一的 Agent 核心）：
  asyncio.run 起事件循环 → async for 收事件 → content 逐字打印（真打字机观感）
  input() 仍是阻塞的——终端 demo 单用户，等待输入期间事件循环闲着，无所谓
"""
import asyncio

from app.agents.react_agent import ReactAgent


async def answer_once(agent: ReactAgent, user_input: str) -> None:
    """消费一轮事件流：tool_call 打印提示行，content 逐字打印（flush 才能逐字上屏）"""
    print("助手> ", end="", flush=True)
    async for ev in agent.run_stream_async(user_input):
        if ev["type"] == "tool_call":
            args_preview = ev["arguments"][:80] if ev["arguments"] else "{}"
            print(f"\n🔧 [{ev['name']}] {args_preview}", flush=True)
        elif ev["type"] == "tool_result":
            print(f"✅ [{ev['name']}] {ev['summary']}", flush=True)
        elif ev["type"] == "content":
            print(ev["text"], end="", flush=True)
    print()


async def main() -> None:
    agent = ReactAgent(verbose=True)
    print("PaperReader Agent 已启动（退出：quit）")
    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "q", "exit"):
            print("再见")
            break
        await answer_once(agent, user_input)


if __name__ == "__main__":
    asyncio.run(main())
