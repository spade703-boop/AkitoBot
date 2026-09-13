"""Run the RPG pre-release checks from a clean, repeatable entry point."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RPG_ROOT = ROOT / "nonebot_plugin_akito" / "features" / "rpg"
EXPECTED_COMMAND_COUNT = 20
LEGACY_PATHS = ("rpg.hunt", "rpg.rewards", "rpg.boss")


def _command_names() -> list[str]:
    names: list[str] = []
    for path in RPG_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "on_command":
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                names.append(node.args[0].value)
    return names


def _check_structure() -> None:
    names = _command_names()
    if len(names) != EXPECTED_COMMAND_COUNT:
        raise SystemExit(f"RPG command count mismatch: expected {EXPECTED_COMMAND_COUNT}, found {len(names)}")
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise SystemExit(f"Duplicate RPG commands: {', '.join(duplicates)}")

    for path in RPG_ROOT.rglob("*.py"):
        if path.name in {"settlement.py", "logic.py"} and "world_boss" in path.parts:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "on_command"
                for node in ast.walk(tree)
            ):
                raise SystemExit(f"world boss command registration leaked into {path}")

    production_files = (ROOT / "nonebot_plugin_akito").rglob("*.py")
    legacy_hits = [
        str(path.relative_to(ROOT))
        for path in production_files
        if any(legacy in path.read_text(encoding="utf-8") for legacy in LEGACY_PATHS)
    ]
    if legacy_hits:
        raise SystemExit(f"Legacy RPG paths referenced by: {', '.join(legacy_hits)}")

    print(f"structure: {len(names)} unique RPG commands")


def _run(label: str, args: list[str]) -> None:
    print(f"{label}: {' '.join(args)}")
    result = subprocess.run(args, cwd=ROOT, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="also run the full repository test suite")
    args = parser.parse_args()

    _check_structure()
    _run("RPG tests", [sys.executable, "-m", "pytest", "-q", "tests/features/rpg"])
    _run("Ruff", [sys.executable, "-m", "ruff", "check", "."])
    if args.full:
        _run("Full tests", [sys.executable, "-m", "pytest", "-q"])


if __name__ == "__main__":
    main()
