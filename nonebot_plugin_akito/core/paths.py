"""Data path helpers shared by runtime code and tests."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DATA_ROOTS = (
    "/app/akito_bot/data",
    "data",
    "/akito_bot/data",
    ".",
)

_READ_SUBDIRS = ("persona", "content", "")


def iter_data_roots() -> list[Path]:
    """Return candidate data roots in lookup order, with override first."""
    roots: list[Path] = []
    seen: set[str] = set()

    override = os.environ.get("AKITO_DATA_DIR", "").strip()
    candidates = ([override] if override else []) + list(_DEFAULT_DATA_ROOTS)

    for raw in candidates:
        key = raw.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        roots.append(Path(key))
    return roots


def find_data_path(filename: str, subdirs: tuple[str, ...] = _READ_SUBDIRS) -> Path | None:
    """Locate a readable data file under any known root/subdir."""
    for base in iter_data_roots():
        for subdir in subdirs:
            candidate = base / subdir / filename if subdir else base / filename
            if candidate.exists():
                return candidate
    return None


def get_data_dir() -> Path:
    """Return the preferred writable data root."""
    override = os.environ.get("AKITO_DATA_DIR", "").strip()
    if override:
        return Path(override)

    for base in iter_data_roots():
        if base.exists() and base != Path("."):
            return base
    return Path("data")


def get_data_file_path(filename: str) -> Path:
    """Return an existing file path or the default writable target path."""
    return find_data_path(filename) or (get_data_dir() / filename)
