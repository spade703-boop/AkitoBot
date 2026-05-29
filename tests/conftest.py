"""
pytest 共享配置与 fixtures。

在导入 nonebot_plugin_akito 之前 mock 掉 NoneBot 框架及所有第三方依赖，
避免触发 NoneBot 初始化检查或加载完整的 handlers/features 链。
"""
from pathlib import Path
import sys
from unittest import mock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Mock nonebot 框架 ──────────────────────────────────────────────────────

_fake_driver = mock.MagicMock()
_fake_driver.config = mock.MagicMock()

_mock_nonebot = mock.MagicMock()
_mock_nonebot.get_driver.return_value = _fake_driver
_mock_nonebot.log.logger = mock.MagicMock()
_mock_nonebot.plugin.PluginMetadata = mock.MagicMock
_mock_nonebot.plugin.inherit_supported_adapters = lambda name: set()
_mock_nonebot.require = mock.MagicMock()
_mock_nonebot.exception.FinishedException = type("FinishedException", (Exception,), {})

sys.modules["nonebot"] = _mock_nonebot
sys.modules["nonebot.log"] = _mock_nonebot.log
sys.modules["nonebot.plugin"] = _mock_nonebot.plugin
sys.modules["nonebot.plugin.load"] = mock.MagicMock()
sys.modules["nonebot.plugin.load"].require = mock.MagicMock()
sys.modules["nonebot.exception"] = _mock_nonebot.exception

# 初始化假适配器
sys.modules["nonebot.adapters"] = mock.MagicMock()
sys.modules["nonebot.adapters.onebot"] = mock.MagicMock()
sys.modules["nonebot.adapters.onebot.v11"] = mock.MagicMock()
sys.modules["nonebot.adapters.onebot.v11.event"] = mock.MagicMock()
sys.modules["nonebot.adapters.onebot.v11.message"] = mock.MagicMock()
sys.modules["nonebot.adapters.onebot.v11.bot"] = mock.MagicMock()

# ── Mock 第三方插件 ─────────────────────────────────────────────────────────

for _mod in [
    "nonebot_plugin_htmlrender",
    "nonebot_plugin_alconna",
    "nonebot_plugin_apscheduler",
    "nonebot_plugin_uninfo",
]:
    sys.modules[_mod] = mock.MagicMock()

# ── Mock 第三方库 ───────────────────────────────────────────────────────────

sys.modules["openai"] = mock.MagicMock()
sys.modules["openai"].AsyncOpenAI = mock.MagicMock
sys.modules["dotenv"] = mock.MagicMock()
sys.modules["dotenv"].load_dotenv = mock.MagicMock()
sys.modules["PIL"] = mock.MagicMock()
sys.modules["PIL.Image"] = mock.MagicMock()
sys.modules["aiohttp"] = mock.MagicMock()

# ── 阻止 handlers 和 features 子包的级联导入 ──────────────────────────────

# 预注册空子包，避免 import 链进入 handlers/chat.py 等文件
sys.modules["nonebot_plugin_akito.handlers"] = mock.MagicMock()
sys.modules["nonebot_plugin_akito.features"] = mock.MagicMock()
