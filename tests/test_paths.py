"""测试测试环境下的数据目录解析。"""

from __future__ import annotations

import os
from pathlib import Path

from nonebot_plugin_akito import core
from nonebot_plugin_akito.core import data


def test_data_dir_uses_override_env():
    """AKITO_DATA_DIR 设置后，core/data 统一指向该目录。"""
    expected = Path(os.environ["AKITO_DATA_DIR"])
    assert expected == core.DATA_DIR
    assert expected == data.get_data_dir()


def test_find_data_path_reads_fixture_files():
    """测试数据目录中的 persona/content 文件可被统一定位。"""
    persona = data.find_data_path("akito_persona.txt")
    prompt = data.find_data_path("prompts_system.json")

    assert persona is not None
    assert persona.name == "akito_persona.txt"
    assert prompt is not None
    assert prompt.name == "prompts_system.json"
