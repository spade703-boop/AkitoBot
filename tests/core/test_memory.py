"""
测试记忆模块的原子写入逻辑。
"""
import json
import os
from pathlib import Path

# ── 从 memory.py 抽取的原子写入逻辑（不依赖运行中的 bot） ─────────────────

def atomic_save(data: dict, target_path: Path):
    """模拟 save_memory() 的原子写入：先写 .tmp 再 os.replace。"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, target_path)


# ── 测试 ───────────────────────────────────────────────────────────────────

def test_atomic_write_creates_file(tmp_path: Path):
    """首次写入创建目标文件。"""
    target = tmp_path / "test_memory.json"
    data = {"key": "value"}
    atomic_save(data, target)
    assert target.exists()


def test_atomic_write_data_integrity(tmp_path: Path):
    """写入后读取的数据完全一致（round-trip）。"""
    target = tmp_path / "test_memory.json"
    data = {
        "group_123": {
            "history": [{"role": "user", "content": "你好"}],
            "temp_implants": [],
        }
    }
    atomic_save(data, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == data


def test_atomic_write_overwrites_previous(tmp_path: Path):
    """第二次写入完全覆盖第一次的内容。"""
    target = tmp_path / "test_memory.json"
    atomic_save({"version": 1}, target)
    atomic_save({"version": 2, "extra": "new"}, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {"version": 2, "extra": "new"}


def test_atomic_write_creates_parent_dir(tmp_path: Path):
    """目标目录不存在时自动创建。"""
    target = tmp_path / "nested" / "deep" / "memory.json"
    data = {"hello": "world"}
    atomic_save(data, target)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == data


def test_atomic_write_tmp_cleaned_up(tmp_path: Path):
    """写入成功后 .tmp 文件已被 os.replace 移动（不应残留）。"""
    target = tmp_path / "memory.json"
    atomic_save({"x": 1}, target)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_atomic_write_handles_unicode(tmp_path: Path):
    """中文和 emoji 数据完整保存和读取。"""
    target = tmp_path / "memory.json"
    data = {"对话": "🎭 东云彰人：嗯，交给我吧。"}
    atomic_save(data, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["对话"] == data["对话"]


def test_atomic_write_handles_empty_dict(tmp_path: Path):
    """空 dict 也能正常写入。"""
    target = tmp_path / "memory.json"
    atomic_save({}, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {}
