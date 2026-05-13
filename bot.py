import nonebot
from nonebot.adapters.onebot.v11 import Adapter

# 初始化 (什么参数都不用传，全去读 .env 文件)
nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(Adapter)

# 加载插件
nonebot.load_plugin("nonebot_plugin_akito")

if __name__ == "__main__":
    nonebot.run()