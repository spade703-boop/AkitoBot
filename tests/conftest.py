"""
pytest 共享配置与测试环境引导。

原则：
1. 不连接真实 NoneBot / QQ / OpenAI / 外网 API。
2. 业务模块仍导入真实代码，尽量在“假平台 + 真逻辑”下运行。
3. 所有读写都指向临时测试 data 目录，不碰真实运行数据。
"""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
from unittest import mock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_DATA = Path(__file__).parent / "fixtures" / "test_data"
RUNTIME_DATA = Path(tempfile.mkdtemp(prefix="akito_test_data_"))
shutil.copytree(FIXTURE_DATA, RUNTIME_DATA, dirs_exist_ok=True)
atexit.register(lambda: shutil.rmtree(RUNTIME_DATA, ignore_errors=True))

os.environ.setdefault("AKITO_DATA_DIR", str(RUNTIME_DATA))
os.environ.setdefault("AKITO_SKIP_PLUGIN_LOAD", "1")
os.environ.setdefault("SUPERUSER_QQ", "9001")
os.environ.setdefault("TOYA_QQ_ID", "8001")
os.environ.setdefault("ALLOWED_CHAT_GROUPS", "1001,1002")
os.environ.setdefault("ALLOWED_CP_GROUPS", "1001")
os.environ.setdefault("ALLOWED_MEMORY_GROUPS", "1001,1002")
os.environ.setdefault("TARGET_GROUPS", "1001")
os.environ.setdefault("GROUP_IMAGE_PERMISSIONS", '{"1001":["all"],"1002":["self","meme"]}')


class FinishedError(Exception):
    """Raised by fake matcher.finish to mimic NoneBot short-circuiting."""

    def __init__(self, result=None):
        super().__init__(str(result) if result is not None else "")
        self.result = result


FinishedException = FinishedError


class FakeMatcher:
    """Minimal matcher object returned by on_* registration helpers."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.handlers = []

    def handle(self):
        def decorator(func):
            self.handlers.append(func)
            return func

        return decorator

    async def finish(self, result=None):
        raise FinishedException(result)


class FakeMessage(str):
    def extract_plain_text(self) -> str:
        return str(self)


class FakeMessageSegment(str):
    @classmethod
    def reply(cls, message_id):
        return cls(f"[reply:{message_id}]")

    @classmethod
    def image(cls, _data):
        return cls("[image]")


class FakeEvent:
    def __init__(
        self,
        plain_text: str = "",
        *,
        group_id: int | None = None,
        user_id: str = "12345",
        sender=None,
        reply=None,
        message=None,
        original_message=None,
        post_type: str = "",
        sub_type: str = "",
        target_id=None,
        message_id: str = "msg-1",
    ):
        self._plain_text = plain_text
        self.group_id = group_id
        self.user_id = user_id
        self.sender = sender or types.SimpleNamespace(card="", nickname="测试用户")
        self.reply = reply
        self.message = message or FakeMessage(plain_text)
        self.original_message = original_message or []
        self.post_type = post_type
        self.sub_type = sub_type
        self.target_id = target_id
        self.message_id = message_id

    def get_user_id(self) -> str:
        return str(self.user_id)

    def get_plaintext(self) -> str:
        return self._plain_text

    def get_message(self):
        return self.message


class FakeBot:
    def __init__(self, self_id: str = "114514"):
        self.self_id = self_id
        self.send_group_msg = mock.AsyncMock()
        self.send = mock.AsyncMock()
        self.get_msg = mock.AsyncMock(
            return_value={
                "message": [],
                "sender": {"nickname": "测试用户", "user_id": "12345"},
            }
        )
        self.get_group_member_info = mock.AsyncMock(return_value={"card": "", "nickname": "测试成员"})


class FakeImage:
    def __init__(self, *, url=None, raw=None, path=None):
        self.url = url
        self.raw = raw
        self.path = path


class FakeText:
    def __init__(self, text: str):
        self.text = text


class FakeUniMessage(list):
    def __add__(self, other):
        return FakeUniMessage(list(self) + list(other))

    def __iadd__(self, other):
        if isinstance(other, list):
            self.extend(other)
        else:
            self.append(other)
        return self

    @classmethod
    async def generate(cls, message=None, event=None, bot=None):
        if isinstance(message, cls):
            return message
        if isinstance(message, list):
            return cls(message)
        text = ""
        if hasattr(message, "extract_plain_text"):
            text = message.extract_plain_text()
        elif message:
            text = str(message)
        return cls([FakeText(text)] if text else [])


class FakeAsyncOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=mock.AsyncMock()))
        self.embeddings = types.SimpleNamespace(create=mock.AsyncMock())


def _make_registration(*args, **kwargs):
    return FakeMatcher(*args, **kwargs)


_fake_bot = FakeBot()
_fake_driver = types.SimpleNamespace(config=types.SimpleNamespace())

nonebot_mod = types.ModuleType("nonebot")
nonebot_mod.on = _make_registration
nonebot_mod.on_message = _make_registration
nonebot_mod.on_command = _make_registration
nonebot_mod.on_notice = _make_registration
nonebot_mod.get_driver = lambda: _fake_driver
nonebot_mod.get_bot = lambda: _fake_bot
nonebot_mod.require = lambda _name: None

nonebot_log_mod = types.ModuleType("nonebot.log")
nonebot_log_mod.logger = mock.MagicMock()

nonebot_plugin_mod = types.ModuleType("nonebot.plugin")
nonebot_plugin_mod.PluginMetadata = mock.MagicMock
nonebot_plugin_mod.inherit_supported_adapters = lambda _name: set()

nonebot_exception_mod = types.ModuleType("nonebot.exception")
nonebot_exception_mod.FinishedException = FinishedException

nonebot_matcher_mod = types.ModuleType("nonebot.matcher")
nonebot_matcher_mod.Matcher = FakeMatcher

nonebot_params_mod = types.ModuleType("nonebot.params")
nonebot_params_mod.CommandArg = lambda: None
nonebot_params_mod.EventMessage = lambda: None

nonebot_adapters_mod = types.ModuleType("nonebot.adapters")
nonebot_adapters_mod.Bot = FakeBot
nonebot_adapters_mod.Event = FakeEvent
nonebot_adapters_mod.Message = FakeMessage

onebot_v11_mod = types.ModuleType("nonebot.adapters.onebot.v11")
onebot_v11_mod.Adapter = object
onebot_v11_mod.Bot = FakeBot
onebot_v11_mod.Event = FakeEvent
onebot_v11_mod.GroupMessageEvent = FakeEvent
onebot_v11_mod.GroupIncreaseNoticeEvent = FakeEvent
onebot_v11_mod.GroupDecreaseNoticeEvent = FakeEvent
onebot_v11_mod.NoticeEvent = FakeEvent
onebot_v11_mod.PokeNotifyEvent = FakeEvent
onebot_v11_mod.MessageSegment = FakeMessageSegment
onebot_v11_mod.Message = FakeMessage

htmlrender_mod = types.ModuleType("nonebot_plugin_htmlrender")
htmlrender_mod.md_to_pic = mock.AsyncMock(return_value=b"md-image")
htmlrender_mod.html_to_pic = mock.AsyncMock(return_value=b"html-image")

alconna_mod = types.ModuleType("nonebot_plugin_alconna")
alconna_mod.Image = FakeImage
alconna_mod.Text = FakeText
alconna_mod.UniMessage = FakeUniMessage


class _FakeScheduler:
    def scheduled_job(self, *args, **kwargs):
        def decorator(func):
            return func

        return decorator


apscheduler_mod = types.ModuleType("nonebot_plugin_apscheduler")
apscheduler_mod.scheduler = _FakeScheduler()

sys.modules["nonebot"] = nonebot_mod
sys.modules["nonebot.log"] = nonebot_log_mod
sys.modules["nonebot.plugin"] = nonebot_plugin_mod
sys.modules["nonebot.plugin.load"] = types.ModuleType("nonebot.plugin.load")
sys.modules["nonebot.plugin.load"].require = lambda _name: None
sys.modules["nonebot.exception"] = nonebot_exception_mod
sys.modules["nonebot.matcher"] = nonebot_matcher_mod
sys.modules["nonebot.params"] = nonebot_params_mod
sys.modules["nonebot.rule"] = types.ModuleType("nonebot.rule")
sys.modules["nonebot.permission"] = types.ModuleType("nonebot.permission")
sys.modules["nonebot.typing"] = types.ModuleType("nonebot.typing")
sys.modules["nonebot.adapters"] = nonebot_adapters_mod
sys.modules["nonebot.adapters.onebot"] = types.ModuleType("nonebot.adapters.onebot")
sys.modules["nonebot.adapters.onebot.v11"] = onebot_v11_mod
sys.modules["nonebot.adapters.onebot.v11.event"] = onebot_v11_mod
sys.modules["nonebot.adapters.onebot.v11.message"] = onebot_v11_mod
sys.modules["nonebot.adapters.onebot.v11.bot"] = onebot_v11_mod
sys.modules["nonebot_plugin_htmlrender"] = htmlrender_mod
sys.modules["nonebot_plugin_alconna"] = alconna_mod
sys.modules["nonebot_plugin_apscheduler"] = apscheduler_mod
sys.modules["nonebot_plugin_uninfo"] = types.ModuleType("nonebot_plugin_uninfo")

openai_mod = types.ModuleType("openai")
openai_mod.AsyncOpenAI = FakeAsyncOpenAI
sys.modules["openai"] = openai_mod

dotenv_mod = types.ModuleType("dotenv")
dotenv_mod.load_dotenv = lambda *args, **kwargs: None
sys.modules["dotenv"] = dotenv_mod

pil_mod = types.ModuleType("PIL")
pil_image_mod = mock.MagicMock()
pil_imagedraw_mod = mock.MagicMock()
pil_imagefont_mod = mock.MagicMock()
pil_mod.Image = pil_image_mod
pil_mod.ImageDraw = pil_imagedraw_mod
pil_mod.ImageFont = pil_imagefont_mod
sys.modules["PIL"] = pil_mod
sys.modules["PIL.Image"] = pil_image_mod
sys.modules["PIL.ImageDraw"] = pil_imagedraw_mod
sys.modules["PIL.ImageFont"] = pil_imagefont_mod

sys.modules["aiohttp"] = mock.MagicMock()
