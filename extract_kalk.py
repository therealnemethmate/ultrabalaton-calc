#!/usr/bin/env python3
"""Extract optimizer input JSON from UB Kalk.html Google Sheets export."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


DEFAULT_CAR_MAP = {
    "Levi": "1",
    "Regi": "1",
    "Peti": "1",
    "Dóri": "2",
    "Anna": "2",
    "Nóri": "2",
    "Bianka": "3",
    "Gábor": "3",
    "Lackó": "3",
}

DEFAULT_DOUBLE_RUNNERS = {"Dóri", "Anna", "Nóri", "Lilla", "Lackó", "Bianka", "Gábor"}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s == "nan" else s


def _parse_int(value: Any) -> Optional[int]:
    s = _clean(value)
    if not s:
        return None
    if not re.fullmatch(r"\d+", s):
        return None
    return int(s)


def _parse_float_hu(value: Any) -> Optional[float]:
    s = _clean(value).replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _decode_km(raw_km: Any) -> Optional[float]:
    s = _clean(raw_km)
    if not s:
        return None

    parsed = _parse_float_hu(s)
    if parsed is None:
        return None

    if "." in s:
        return parsed

    # HTML export stores decimals without separator: e.g. 47 -> 4.7, 76 -> 7.6.
    if parsed < 10:
        return parsed
    return parsed / 10.0


def _block_count(segment_ids: List[int]) -> int:
    """Count contiguous blocks assuming segment IDs are sequential (1..N)."""
    if not segment_ids:
        return 0
    ids = sorted(segment_ids)
    blocks = 1
    for a, b in zip(ids, ids[1:]):
        if b != a + 1:
            blocks += 1
    return blocks


def build_input(df: pd.DataFrame, include_default_cars: bool) -> Dict[str, Any]:
    segments: List[Dict[str, Any]] = []
    current_owner: Dict[str, str] = {}
    assignments: Dict[str, List[int]] = defaultdict(list)

    # Segment rows: G=id, H=km, L=runner
    for _, row in df.iterrows():
        seg_id = _parse_int(row.get("G"))
        km = _decode_km(row.get("H"))
        runner = _clean(row.get("L"))
        if seg_id is None or km is None or not runner:
            continue
        if seg_id < 1 or seg_id > 56:
            continue
        segments.append({"id": seg_id, "km": km})
        current_owner[str(seg_id)] = runner
        assignments[runner].append(seg_id)

    segments = sorted({s["id"]: s for s in segments}.values(), key=lambda x: x["id"])

    # Runner table rows: C=name, D=pace, E=target
    target_by_runner: Dict[str, float] = {}
    for _, row in df.iterrows():
        name = _clean(row.get("C"))
        if not name or name == "CSAPATTAG":
            continue
        target = _parse_float_hu(row.get("E"))
        if target is None:
            continue
        target_by_runner[name] = target

    runners: List[Dict[str, Any]] = []
    for runner in sorted(assignments.keys(), key=lambda n: min(assignments[n])):
        target = target_by_runner.get(runner)
        if target is None:
            # Fallback to current assigned km if target column is missing.
            km_sum = sum(s["km"] for s in segments if current_owner.get(str(s["id"])) == runner)
            target = round(km_sum, 1)
        blocks = _block_count(assignments[runner])
        max_blocks = blocks if blocks in (1, 2) else 2
        is_double = include_default_cars and runner in DEFAULT_DOUBLE_RUNNERS
        min_blocks = 2 if is_double else 1
        if is_double:
            max_blocks = max(max_blocks, 2)
        entry: Dict[str, Any] = {
            "name": runner,
            "target_km": target,
            "min_blocks": min_blocks,
            "max_blocks": max_blocks,
            "car_id": None,
            "rest_priority": 1 if is_double else 0,
        }
        if include_default_cars and runner in DEFAULT_CAR_MAP:
            entry["car_id"] = DEFAULT_CAR_MAP[runner]
        runners.append(entry)

    return {
        "segments": segments,
        "runners": runners,
        "current_owner": current_owner,
        "settings": {
            "scale": 10,
            "time_limit_sec": 30,
            "num_workers": 8,
            "max_overflow_km": 4.0,
            "min_block_km": 4.0 if include_default_cars else 0.0,
            "first_leg_ratio_min": 0.4 if include_default_cars else 0.0,
            "first_leg_ratio_max": 0.7 if include_default_cars else 1.0,
            "enforce_second_round_order": include_default_cars,
            "enforce_car_block_grouping": include_default_cars,
            "require_every_runner_used": True,
            "weights": {
                "overflow": 4,
                "underfill": 1,
                "change": 2,
                "car_span": 1,
                "rest_gap": 6 if include_default_cars else 0,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract UB optimizer input JSON from Kalk.html")
    parser.add_argument("--kalk-html", required=True, help="Path to Kalk.html")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument(
        "--include-default-cars",
        action="store_true",
        help="Include default car mapping from prior UB constraints",
    )
    args = parser.parse_args()

    html_path = Path(args.kalk_html)
    out_path = Path(args.output)
    tables = pd.read_html(str(html_path))
    if not tables:
        raise SystemExit("No table found in HTML.")

    df = tables[0]
    payload = build_input(df, include_default_cars=args.include_default_cars)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} ({len(payload['segments'])} segments, {len(payload['runners'])} runners)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
