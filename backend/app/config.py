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

    #RAG
    CHUNK_SIZE:int=1000              #每块大约多少字符
    CHUNK_OVERLAP:int=150            #相邻块重叠多少（防止句子被切断丢失上下文）
    RETRIEVAL_TOP_K:int=5            #每次检索取前几块

    #存储
    PAPERS_DIR:str="./data/papers"      #PDF 存放目录
    CHROMA_DIR:str="./data/chroma_db"   #向量库目录

    #arXiv
    ARXIV_MAX_RESULTS:int=10            #搜索时最多返回几篇

    AGENT_MAX_HISTORY:int=20            ##记忆窗口：最多带最近多少条 user/assistant 消息

    #启动时自动读取 backend/.env；extra=ignore：.env 里有多余变量也不报错
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env",extra="ignore")

# 全项目唯一的配置实例：其他模块一律 from app.config import settings（和 engine 一样，只建一次）
settings = Settings()
