#!/usr/bin/env python3
"""
Apply discovered ASIN seeds to .env without manual editing.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List


def parse_asins(raw: str) -> List[str]:
    asins = []
    for token in raw.replace("\n", ",").split(","):
        value = token.strip().upper()
        if len(value) == 10 and value.startswith("B"):
            asins.append(value)
    # Preserve order, remove duplicates
    seen = set()
    ordered = []
    for asin in asins:
        if asin not in seen:
            seen.add(asin)
            ordered.append(asin)
    return ordered


def update_env_file(env_path: Path, updates: Dict[str, str]) -> None:
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    remaining = dict(updates)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue

        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            remaining.pop(key, None)
        else:
            new_lines.append(line)

    if remaining:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply seed ASINs to .env")
    parser.add_argument(
        "--seed-file",
        default="data/seed_asins_eu_qwertz.txt",
        help="Path to comma-separated ASIN file",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file",
    )
    parser.add_argument(
        "--seed-mode",
        default="product_only",
        help="Value for DEAL_SOURCE_MODE",
    )
    parser.add_argument(
        "--inline-limit",
        type=int,
        default=0,
        help=(
            "Optional: set DEAL_SEED_ASINS with the first N ASINs. "
            "Use 0 to keep DEAL_SEED_ASINS empty and rely on DEAL_SEED_FILE."
        ),
    )
    args = parser.parse_args()

    seed_path = Path(args.seed_file)
    if not seed_path.exists():
        print(f"ERROR: Seed file not found: {seed_path}")
        return 1

    raw = seed_path.read_text(encoding="utf-8")
    asins = parse_asins(raw)
    if not asins:
        print("ERROR: No valid ASINs found in seed file.")
        return 1

    env_path = Path(args.env_file)
    inline_limit = max(0, int(args.inline_limit))
    inline_asins = ",".join(asins[:inline_limit]) if inline_limit > 0 else ""
    updates = {
        "DEAL_SOURCE_MODE": args.seed_mode,
        "DEAL_SEED_FILE": str(seed_path),
        "DEAL_SEED_ASINS": inline_asins,
    }
    update_env_file(env_path, updates)

    print(f"Updated {env_path}")
    print(f"ASIN count: {len(asins)}")
    print(f"DEAL_SEED_FILE={seed_path}")
    if inline_limit > 0:
        print(f"DEAL_SEED_ASINS set with first {min(inline_limit, len(asins))} ASINs")
    else:
        print("DEAL_SEED_ASINS cleared (file-based seed mode)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
