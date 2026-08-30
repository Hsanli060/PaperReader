"""
终端对话入口（demo 用）：读用户输入 → Agent 回答 → 打印，循环到 quit
用法：cd backend && ../.venv/Scripts/python.exe agent_chat.py
"""
from app.agents.react_agent import ReactAgent

def main()->None:
    """主对话循环。

   每轮：input() 阻塞等输入 → agent.run() 回答 → 打印。
   quit/q 退出。Ctrl+C 也正常退出（try/except 接住 KeyboardInterrupt）。
   """
    agent=ReactAgent(verbose=True)
    print("PaperReader Agent 已启动（退出：quit）")
    while True:
        try:
            user_input=input("\n你> ").strip()
        except (EOFError,KeyboardInterrupt):     #Ctrl+C / Ctrl+D 也能优雅退出
            print("\n再见")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit","q","exit"):
            print("再见")
            break
        print("助手> ",end="",flush=True)
        print(agent.run(user_input))

if __name__ =="__main__":
    main()