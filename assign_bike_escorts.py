#!/usr/bin/env python3
"""Post-process final.csv with bike escort assignments."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class BikeAssignError(ValueError):
    pass


@dataclass(frozen=True)
class BikeEscort:
    name: str
    target_km: float


@dataclass(frozen=True)
class FixedRange:
    name: str
    start_seg: int
    end_seg: int
    day_only: bool = False


@dataclass(frozen=True)
class SegmentRow:
    seg_id: int
    km: float
    row_idx: int
    biker: str
    is_night: bool


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s == "nan" else s


def _parse_hu_float(value: Any) -> Optional[float]:
    s = _clean(value).replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _decode_km(raw_km: Any) -> Optional[float]:
    s = _clean(raw_km)
    if not s:
        return None
    parsed = _parse_hu_float(s)
    if parsed is None:
        return None
    if "." in s:
        return parsed
    if parsed < 10:
        return parsed
    return parsed / 10.0


def _load_config(path: Path) -> Tuple[List[BikeEscort], List[FixedRange], List[str], bool, bool]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BikeAssignError("Config root must be a JSON object.")

    escorts_raw = payload.get("bike_escorts", [])
    if escorts_raw is None:
        escorts_raw = []
    if not isinstance(escorts_raw, list):
        raise BikeAssignError("config.bike_escorts must be a list if provided.")

    escorts: List[BikeEscort] = []
    seen_escort_names = set()
    for i, item in enumerate(escorts_raw):
        if not isinstance(item, dict):
            raise BikeAssignError(f"bike_escorts[{i}] must be an object.")
        name = _clean(item.get("name"))
        if not name:
            raise BikeAssignError(f"bike_escorts[{i}].name must be non-empty.")
        if name in seen_escort_names:
            raise BikeAssignError(f"Duplicate bike escort name: {name}")
        seen_escort_names.add(name)
        target = _parse_hu_float(item.get("target_km"))
        if target is None or target <= 0:
            raise BikeAssignError(f"bike_escorts[{i}].target_km must be > 0.")
        escorts.append(BikeEscort(name=name, target_km=float(target)))

    fixed_ranges_raw = payload.get("fixed_ranges", [])
    if fixed_ranges_raw is None:
        fixed_ranges_raw = []
    if not isinstance(fixed_ranges_raw, list):
        raise BikeAssignError("config.fixed_ranges must be a list if provided.")

    fixed_ranges: List[FixedRange] = []
    for i, item in enumerate(fixed_ranges_raw):
        if not isinstance(item, dict):
            raise BikeAssignError(f"fixed_ranges[{i}] must be an object.")
        name = _clean(item.get("name"))
        if not name:
            raise BikeAssignError(f"fixed_ranges[{i}].name must be non-empty.")
        start_seg_raw = item.get("start_seg")
        end_seg_raw = item.get("end_seg")
        try:
            start_seg = int(start_seg_raw)
            end_seg = int(end_seg_raw)
        except (TypeError, ValueError) as exc:
            raise BikeAssignError(
                f"fixed_ranges[{i}].start_seg/end_seg must be integers."
            ) from exc
        if start_seg < 1 or end_seg < 1 or start_seg > end_seg:
            raise BikeAssignError(
                f"fixed_ranges[{i}] invalid range: {start_seg}-{end_seg}."
            )
        fixed_ranges.append(
            FixedRange(
                name=name,
                start_seg=start_seg,
                end_seg=end_seg,
                day_only=bool(item.get("day_only", False)),
            )
        )

    if not escorts and not fixed_ranges:
        raise BikeAssignError(
            "Provide at least one of config.bike_escorts or config.fixed_ranges."
        )

    priority_raw = payload.get("priority", ["Regi", "Lilla", "Bianka"])
    if not isinstance(priority_raw, list):
        raise BikeAssignError("config.priority must be a list if provided.")
    priority: List[str] = []
    for i, name_raw in enumerate(priority_raw):
        name = _clean(name_raw)
        if not name:
            raise BikeAssignError(f"priority[{i}] must be non-empty.")
        if name not in priority:
            priority.append(name)

    fill_day_segments = bool(payload.get("fill_day_segments", True))
    write_into_empty_only = bool(payload.get("write_into_empty_only", True))
    return escorts, fixed_ranges, priority, fill_day_segments, write_into_empty_only


def _parse_final_rows(final_csv: Path) -> Tuple[List[List[str]], List[SegmentRow], int]:
    with final_csv.open("r", encoding="utf-8", newline="") as f:
        rows = [list(r) for r in csv.reader(f)]

    header_idx = -1
    km_col = -1
    biker_col = -1
    day_col = -1
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            c = _clean(cell)
            if c == "SZAKASZ HOSSZA":
                header_idx = i
                km_col = j
            elif c == "KERÉKPÁROS":
                biker_col = j
            elif c == "Napszak":
                day_col = j
        if header_idx >= 0 and km_col >= 0 and biker_col >= 0:
            break

    if header_idx < 0 or km_col < 1 or biker_col < 0:
        raise BikeAssignError("Could not find segment table/KERÉKPÁROS column in final.csv.")

    seg_col = km_col - 1
    segment_rows: List[SegmentRow] = []
    for row_idx, row in enumerate(rows[header_idx + 1 :], start=header_idx + 1):
        if seg_col >= len(row):
            continue
        seg_raw = _clean(row[seg_col])
        if not seg_raw.isdigit():
            continue
        seg_id = int(seg_raw)
        if not (1 <= seg_id <= 200):
            continue
        km = _decode_km(row[km_col] if km_col < len(row) else "")
        if km is None:
            continue
        biker = _clean(row[biker_col]) if biker_col < len(row) else ""
        day = _clean(row[day_col]) if day_col < len(row) else ""
        segment_rows.append(
            SegmentRow(
                seg_id=seg_id,
                km=km,
                row_idx=row_idx,
                biker=biker,
                is_night=("🌙" in day),
            )
        )
    segment_rows.sort(key=lambda x: x.seg_id)
    return rows, segment_rows, biker_col


def _pick_night(
    candidates: List[str],
    assigned: Dict[str, float],
    targets: Dict[str, float],
    priority_idx: Dict[str, int],
) -> str:
    def key(name: str) -> Tuple[int, int, int, float, float, str]:
        target = targets[name]
        done = assigned[name]
        ratio = done / target if target > 0 else 1e9
        return (
            0 if name in priority_idx else 1,
            0 if done < target else 1,
            priority_idx.get(name, 9999),
            ratio,
            done,
            name,
        )

    return min(candidates, key=key)


def _pick_day(candidates: List[str], assigned: Dict[str, float], targets: Dict[str, float]) -> str:
    def key(name: str) -> Tuple[float, float, str]:
        target = targets[name]
        done = assigned[name]
        ratio = done / target if target > 0 else 1e9
        return (ratio, done, name)

    return min(candidates, key=key)


def _recompute_candidate_km(
    owner_by_segment: Dict[int, str],
    segments: List[SegmentRow],
    candidate_names: List[str],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    assigned = {name: 0.0 for name in candidate_names}
    night = {name: 0.0 for name in candidate_names}
    candidate_set = set(candidate_names)
    for s in segments:
        owner = owner_by_segment.get(s.seg_id, "")
        if owner not in candidate_set:
            continue
        assigned[owner] += s.km
        if s.is_night:
            night[owner] += s.km
    return assigned, night


def assign_bikers(
    segments: List[SegmentRow],
    escorts: List[BikeEscort],
    fixed_ranges: List[FixedRange],
    priority: List[str],
    fill_day_segments: bool,
    write_into_empty_only: bool,
) -> Tuple[Dict[int, str], Dict[str, Dict[str, Any]]]:
    targets = {e.name: e.target_km for e in escorts}
    candidate_names = [e.name for e in escorts]
    priority_idx = {name: i for i, name in enumerate(priority)}

    owner_by_segment: Dict[int, str] = {}
    for s in segments:
        if s.biker:
            owner_by_segment[s.seg_id] = s.biker

    fixed_assigned = set()
    for fr in fixed_ranges:
        for s in segments:
            if s.seg_id < fr.start_seg or s.seg_id > fr.end_seg:
                continue
            if fr.day_only and s.is_night:
                continue
            has_existing = bool(owner_by_segment.get(s.seg_id, ""))
            if write_into_empty_only and has_existing:
                continue
            owner_by_segment[s.seg_id] = fr.name
            fixed_assigned.add(s.seg_id)

    if candidate_names:
        assigned, _ = _recompute_candidate_km(owner_by_segment, segments, candidate_names)

        todo = [
            s
            for s in segments
            if s.seg_id not in fixed_assigned
            and ((not write_into_empty_only) or (not owner_by_segment.get(s.seg_id, "")))
        ]
        night_todo = [s for s in todo if s.is_night]
        day_todo = [s for s in todo if not s.is_night]

        for s in night_todo:
            picked = _pick_night(candidate_names, assigned, targets, priority_idx)
            owner_by_segment[s.seg_id] = picked
            assigned[picked] += s.km

        if fill_day_segments:
            for s in day_todo:
                under_target = [name for name in candidate_names if assigned[name] < targets[name]]
                if not under_target:
                    break
                picked = _pick_day(under_target, assigned, targets)
                owner_by_segment[s.seg_id] = picked
                assigned[picked] += s.km

    # Summary for all seen names (targets only for configured escorts).
    ordered_names: List[str] = []
    seen = set()
    for name in candidate_names:
        if name not in seen:
            ordered_names.append(name)
            seen.add(name)
    for fr in fixed_ranges:
        if fr.name not in seen:
            ordered_names.append(fr.name)
            seen.add(fr.name)
    for s in segments:
        owner = owner_by_segment.get(s.seg_id, "")
        if owner and owner not in seen:
            ordered_names.append(owner)
            seen.add(owner)

    assigned_all = {name: 0.0 for name in ordered_names}
    night_all = {name: 0.0 for name in ordered_names}
    segs_by_name: Dict[str, List[int]] = {name: [] for name in ordered_names}
    for s in segments:
        owner = owner_by_segment.get(s.seg_id, "")
        if not owner:
            continue
        if owner not in assigned_all:
            assigned_all[owner] = 0.0
            night_all[owner] = 0.0
            segs_by_name[owner] = []
        assigned_all[owner] += s.km
        if s.is_night:
            night_all[owner] += s.km
        segs_by_name[owner].append(s.seg_id)

    summary: Dict[str, Dict[str, Any]] = {}
    for name in ordered_names:
        done = assigned_all.get(name, 0.0)
        target = targets.get(name)
        summary[name] = {
            "name": name,
            "target_km": (round(target, 3) if target is not None else None),
            "assigned_km": round(done, 3),
            "underfill_km": (round(max(0.0, target - done), 3) if target is not None else None),
            "overflow_km": (round(max(0.0, done - target), 3) if target is not None else None),
            "night_km": round(night_all.get(name, 0.0), 3),
            "segment_count": len(segs_by_name.get(name, [])),
            "segments": sorted(segs_by_name.get(name, [])),
        }
    return owner_by_segment, summary


def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Assign bike escorts into final.csv.")
    parser.add_argument("--final-csv", required=True, help="Input final.csv path")
    parser.add_argument("--config", required=True, help="Bike escort config JSON path")
    parser.add_argument("--output", default="", help="Output CSV path (default: input path)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write CSV, print JSON summary only")
    args = parser.parse_args(argv)

    in_path = Path(args.final_csv)
    out_path = Path(args.output) if args.output else in_path
    escorts, fixed_ranges, priority, fill_day_segments, write_into_empty_only = _load_config(
        Path(args.config)
    )
    rows, segments, biker_col = _parse_final_rows(in_path)

    owner_by_segment, summary = assign_bikers(
        segments=segments,
        escorts=escorts,
        fixed_ranges=fixed_ranges,
        priority=priority,
        fill_day_segments=fill_day_segments,
        write_into_empty_only=write_into_empty_only,
    )

    if not args.dry_run:
        seg_row_map = {s.seg_id: s for s in segments}
        for seg_id, owner in owner_by_segment.items():
            s = seg_row_map.get(seg_id)
            if s is None:
                continue
            row = rows[s.row_idx]
            while len(row) <= biker_col:
                row.append("")
            row[biker_col] = owner
        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows)

    payload = {
        "input": str(in_path),
        "output": str(out_path),
        "dry_run": bool(args.dry_run),
        "priority": priority,
        "fixed_ranges": [
            {
                "name": fr.name,
                "start_seg": fr.start_seg,
                "end_seg": fr.end_seg,
                "day_only": fr.day_only,
            }
            for fr in fixed_ranges
        ],
        "bike_escorts": list(summary.values()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
