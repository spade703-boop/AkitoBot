import os
import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()  # 显式将 .env 写入 os.environ（NoneBot2 自身不做这一步）

TZ_CN  = datetime.timezone(datetime.timedelta(hours=8))   # 北京时间，用于睡眠/routine判定
TZ_JST = datetime.timezone(datetime.timedelta(hours=9))   # 东京时间，用于彰人对话报时
DB_PATH = Path("data/impression_history.db")
IMAGE_BASE_PATH = Path("data/images")
MAX_HISTORY_LEN = 40

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
TAVILY_API_KEY   = os.environ.get("TAVILY_API_KEY", "")
ZHIPU_API_KEY    = os.environ.get("ZHIPU_API_KEY", "")

client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
vision_client = AsyncOpenAI(api_key=ZHIPU_API_KEY, base_url="https://open.bigmodel.cn/api/paas/v4/")

TOYA_QQ_ID   = os.environ.get("TOYA_QQ_ID", "3958033212")
SUPERUSER_QQ = os.environ.get("SUPERUSER_QQ", "2403925946")
TRIGGER_NAMES = {"东云小彰", "小彰"}

ALLOWED_CHAT_GROUPS   = [1041487251, 691188576, 761599729, 740468887]
ALLOWED_CP_GROUPS     = [1041487251, 691188576, 761599729]
ALLOWED_MEMORY_GROUPS = [1041487251]
TARGET_GROUPS         = [740468887, 761599729, 691188576]

GROUP_IMAGE_PERMISSIONS = {
    1041487251: ["all"],
    691188576:  ["all"],
    761599729:  ["all"],
}
