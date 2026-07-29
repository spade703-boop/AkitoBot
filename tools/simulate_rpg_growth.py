from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("AKITO_SKIP_PLUGIN_LOAD", "1")

def main() -> None:
    import nonebot

    nonebot.init()
    from nonebot_plugin_akito.features.rpg.simulation import (
        format_growth_simulation,
        growth_baseline_violations,
        simulate_solo_growth,
    )

    parser = argparse.ArgumentParser(description="模拟连续签到并单人打怪的 RPG 长期成长")
    parser.add_argument("--days", type=int, default=360)
    parser.add_argument("--runs", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--strict", action="store_true", help="成长基线偏离时返回非零退出码")
    args = parser.parse_args()
    result = simulate_solo_growth(days=args.days, runs=args.runs, seed=args.seed)
    print(format_growth_simulation(result))
    if args.strict and growth_baseline_violations(result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
