from pathlib import Path

from pydantic_settings import BaseSettings,SettingsConfigDict

# .env 固定锚定在 backend/ 目录下：config.py 位于 backend/app/，
# 两级 parent 回到 backend/。这样无论从哪个目录启动程序都能找到 .env，
# 避免"相对路径"灵异 bug
BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    #应用
    APP_NAME:str="PaperReader"          #服务名
    DEBUG:bool=False                    #调试开关
    API_PREFIX:str="/api"               #路由前缀

    #数据库
    DATABASE_URL:str

    #缓存
    REDIS_URL:str|None=None         #Redis 地址

    #LLM
    LLM_API_KEY:str
    LLM_BASE_URL:str
    LLM_MODEL:str
    LLM_TEMPERATURE:float=0.3   #模型温度

    #向量
    EMBEDDING_API_KEY:str
    EMBEDDING_BASE_URL:str
    EMBEDDING_MODEL:str
    EMBEDDING_DIM:int=1024      # 向量维度

    #用户级 API Key（实现见 services/user_keys.py）
    #默认严格模式：用户未配置自己的 key 时拒绝 LLM/嵌入调用（部署服务器保持默认 false）
    #本地开发在 .env 里设 ALLOW_DEFAULT_KEY=true 回退用 .env 里的 key
    ALLOW_DEFAULT_KEY:bool=False

    #RAG
    CHUNK_SIZE:int=1000              #每块大约多少字符
    CHUNK_OVERLAP:int=150            #相邻块重叠多少（防止句子被切断丢失上下文）
    RETRIEVAL_TOP_K:int=5            #每次检索取前几块
    RETRIEVAL_CANDIDATES: int = 20  # 每路召回先取多少条候选（供 RRF 融合和精排用）
    RERANK_MODEL:str="qwen3-rerank"  #重排模型（精排用；与 embedding 共用 workspace 和 key）

    PAPERS_DIR:str=str(BASE_DIR/"data/papers")      #PDF 存放目录
    CHROMA_DIR:str=str(BASE_DIR/"data/chroma_db")   #向量库目录

    #arXiv
    ARXIV_MAX_RESULTS:int=10            #搜索时最多返回几篇

    AGENT_MAX_HISTORY:int=20            ##记忆窗口：最多带最近多少条 user/assistant 消息

    #Agent 上下文压缩（借鉴 Claude Code context compact 机制；实现见 app/agents/context.py）
    #阈值按实测校准：search_paper 单条≈5K 字符、web_search≈2.2K、summarize≈0.5K
    AGENT_RESULT_CHAR_LIMIT:int=6000      #单条工具结果的硬上限（超出截断留预览）
    AGENT_BATCH_CHAR_LIMIT:int=15000      #单轮新增工具结果的批量预算（并行多工具按份数均分）
    AGENT_CONTEXT_CHAR_LIMIT:int=16000    #消息总字符软线：超过才老化已消费的旧结果
    AGENT_KEEP_RECENT_RESULTS:int=3       #已消费工具结果保留最近几条原文不压

    #SSE 流式（心跳保活；实现见 app/api/routes/chat.py）
    SSE_PING_INTERVAL:int=15              #心跳注释帧间隔（秒）：须小于反代空闲超时（nginx 默认 60s）

    #启动时自动读取 backend/.env；extra=ignore：.env 里有多余变量也不报错
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env",extra="ignore")

    # JWT
    JWT_SECRET: str  # 签名密钥，无默认值=.env 必须提供
    JWT_ALGORITHM: str = "HS256"  # 签名算法
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 短期通行证
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7  # 换证凭证

# 全项目唯一的配置实例：其他模块一律 from app.config import settings（和 engine 一样，只建一次）
settings = Settings()
