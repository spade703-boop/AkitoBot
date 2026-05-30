"""东云彰人 Bot 插件入口：声明插件元数据、require 依赖插件，并加载 core/handlers/features 三层。"""

import nonebot
from nonebot import require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_alconna")
require("nonebot_plugin_htmlrender")
require("nonebot_plugin_apscheduler")

__plugin_meta__ = PluginMetadata(
    name="东云彰人Bot",
    description="DeepSeek AI (东云彰人)",
    usage="东云小彰 / 小彰 [文本] - AI 角色扮演对话",
    type="application",
    homepage="https://github.com/spade703-boop/AkitoBot",
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna"),
)

from . import core
from . import handlers
from . import features
