"""消息处理层：主对话引擎(chat)、管理指令(commands)、被动反应(reactions)。"""

import os

if os.environ.get("AKITO_SKIP_PLUGIN_LOAD") != "1":
    from . import chat
    from . import commands
    from . import reactions
