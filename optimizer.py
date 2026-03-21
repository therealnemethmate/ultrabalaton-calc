#!/usr/bin/env python3
"""CP-SAT optimizer for ordered relay segment assignment."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from ortools.sat.python import cp_model
except ImportError as exc:  # pragma: no cover - handled at runtime in CLI
    cp_model = None  # type: ignore[assignment]
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@dataclass(frozen=True)
class Segment:
    id: int
    km: float


@dataclass(frozen=True)
class Runner:
    name: str
    target_km: float
    min_blocks: int
    max_blocks: int
    car_id: Optional[str] = None
    max_overflow_km: Optional[float] = None
    rest_priority: int = 1
    min_rest_gap_segments: Optional[int] = None


@dataclass(frozen=True)
class ObjectiveWeights:
    overflow: int = 4
    underfill: int = 1
    change: int = 2
    car_span: int = 1
    rest_gap: int = 0


@dataclass(frozen=True)
class SolverConfig:
    scale: int = 10
    time_limit_sec: int = 30
    num_workers: int = 8
    max_overflow_km: Optional[float] = 4.0
    min_block_km: float = 0.0
    min_rest_gap_segments: int = 0
    first_leg_ratio_min: float = 0.0
    first_leg_ratio_max: float = 1.0
    enforce_second_round_order: bool = False
    enforce_car_block_grouping: bool = False
    car_block_order: Tuple[str, ...] = ()
    require_every_runner_used: bool = True
    weights: ObjectiveWeights = ObjectiveWeights()


class InputError(ValueError):
    pass


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise InputError("Input root must be a JSON object.")
    return payload


def _to_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"Invalid integer for {field_name}: {value!r}") from exc


def _to_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"Invalid number for {field_name}: {value!r}") from exc


def parse_input(
    payload: Dict[str, Any]
) -> Tuple[List[Segment], List[Runner], Dict[int, str], Dict[int, str], SolverConfig, List[str]]:
    segments_raw = payload.get("segments")
    runners_raw = payload.get("runners")
    current_owner_raw = payload.get("current_owner", {})
    fixed_owner_raw = payload.get("fixed_owner", {})
    settings_raw = payload.get("settings", {})

    if not isinstance(segments_raw, list) or not segments_raw:
        raise InputError("`segments` must be a non-empty list.")
    if not isinstance(runners_raw, list) or not runners_raw:
        raise InputError("`runners` must be a non-empty list.")
    if not isinstance(current_owner_raw, dict):
        raise InputError("`current_owner` must be an object if provided.")
    if not isinstance(fixed_owner_raw, dict):
        raise InputError("`fixed_owner` must be an object if provided.")
    if not isinstance(settings_raw, dict):
        raise InputError("`settings` must be an object if provided.")

    segments: List[Segment] = []
    seen_segment_ids = set()
    for i, item in enumerate(segments_raw):
        if not isinstance(item, dict):
            raise InputError(f"segments[{i}] must be an object.")
        seg_id = _to_int(item.get("id"), f"segments[{i}].id")
        seg_km = _to_float(item.get("km"), f"segments[{i}].km")
        if seg_km <= 0:
            raise InputError(f"segments[{i}].km must be > 0.")
        if seg_id in seen_segment_ids:
            raise InputError(f"Duplicate segment id: {seg_id}")
        seen_segment_ids.add(seg_id)
        segments.append(Segment(id=seg_id, km=seg_km))

    runners: List[Runner] = []
    seen_runner_names = set()
    for i, item in enumerate(runners_raw):
        if not isinstance(item, dict):
            raise InputError(f"runners[{i}] must be an object.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InputError(f"runners[{i}].name must be a non-empty string.")
        if name in seen_runner_names:
            raise InputError(f"Duplicate runner name: {name}")
        seen_runner_names.add(name)
        target_km = _to_float(item.get("target_km"), f"runners[{i}].target_km")
        if target_km <= 0:
            raise InputError(f"runners[{i}].target_km must be > 0.")
        min_blocks = _to_int(item.get("min_blocks", 1), f"runners[{i}].min_blocks")
        max_blocks = _to_int(item.get("max_blocks"), f"runners[{i}].max_blocks")
        if min_blocks < 1 or min_blocks > 2:
            raise InputError(f"runners[{i}].min_blocks must be 1 or 2.")
        if max_blocks < 1 or max_blocks > 2:
            raise InputError(f"runners[{i}].max_blocks must be 1 or 2.")
        if min_blocks > max_blocks:
            raise InputError(f"runners[{i}].min_blocks cannot be greater than max_blocks.")
        car_id_raw = item.get("car_id")
        car_id = str(car_id_raw) if car_id_raw is not None else None
        max_overflow_raw = item.get("max_overflow_km")
        max_overflow = _to_float(max_overflow_raw, f"runners[{i}].max_overflow_km") if max_overflow_raw is not None else None
        rest_priority = _to_int(item.get("rest_priority", 1), f"runners[{i}].rest_priority")
        if rest_priority < 0:
            raise InputError(f"runners[{i}].rest_priority must be >= 0.")
        min_rest_gap_raw = item.get("min_rest_gap_segments")
        min_rest_gap_segments = (
            _to_int(min_rest_gap_raw, f"runners[{i}].min_rest_gap_segments")
            if min_rest_gap_raw is not None
            else None
        )
        if min_rest_gap_segments is not None and min_rest_gap_segments < 0:
            raise InputError(f"runners[{i}].min_rest_gap_segments must be >= 0.")
        runners.append(
            Runner(
                name=name,
                target_km=target_km,
                min_blocks=min_blocks,
                max_blocks=max_blocks,
                car_id=car_id,
                max_overflow_km=max_overflow,
                rest_priority=rest_priority,
                min_rest_gap_segments=min_rest_gap_segments,
            )
        )

    weights_raw = settings_raw.get("weights", {})
    if not isinstance(weights_raw, dict):
        raise InputError("settings.weights must be an object if provided.")
    car_block_order_raw = settings_raw.get("car_block_order", [])
    if not isinstance(car_block_order_raw, list):
        raise InputError("settings.car_block_order must be a list if provided.")
    car_block_order: List[str] = []
    for i, item in enumerate(car_block_order_raw):
        car = str(item).strip()
        if not car:
            raise InputError(f"settings.car_block_order[{i}] must be a non-empty string.")
        car_block_order.append(car)
    weights = ObjectiveWeights(
        overflow=_to_int(weights_raw.get("overflow", 4), "settings.weights.overflow"),
        underfill=_to_int(weights_raw.get("underfill", 1), "settings.weights.underfill"),
        change=_to_int(weights_raw.get("change", 2), "settings.weights.change"),
        car_span=_to_int(weights_raw.get("car_span", 1), "settings.weights.car_span"),
        rest_gap=_to_int(weights_raw.get("rest_gap", 0), "settings.weights.rest_gap"),
    )

    config = SolverConfig(
        scale=_to_int(settings_raw.get("scale", 10), "settings.scale"),
        time_limit_sec=_to_int(settings_raw.get("time_limit_sec", 30), "settings.time_limit_sec"),
        num_workers=_to_int(settings_raw.get("num_workers", 8), "settings.num_workers"),
        max_overflow_km=(
            _to_float(settings_raw["max_overflow_km"], "settings.max_overflow_km")
            if "max_overflow_km" in settings_raw and settings_raw["max_overflow_km"] is not None
            else (None if "max_overflow_km" in settings_raw else 4.0)
        ),
        min_block_km=_to_float(settings_raw.get("min_block_km", 0.0), "settings.min_block_km"),
        min_rest_gap_segments=_to_int(
            settings_raw.get("min_rest_gap_segments", 0),
            "settings.min_rest_gap_segments",
        ),
        first_leg_ratio_min=_to_float(settings_raw.get("first_leg_ratio_min", 0.0), "settings.first_leg_ratio_min"),
        first_leg_ratio_max=_to_float(settings_raw.get("first_leg_ratio_max", 1.0), "settings.first_leg_ratio_max"),
        enforce_second_round_order=bool(settings_raw.get("enforce_second_round_order", False)),
        enforce_car_block_grouping=bool(settings_raw.get("enforce_car_block_grouping", False)),
        car_block_order=tuple(car_block_order),
        require_every_runner_used=bool(settings_raw.get("require_every_runner_used", True)),
        weights=weights,
    )

    if config.scale < 1:
        raise InputError("settings.scale must be >= 1.")
    if config.time_limit_sec < 1:
        raise InputError("settings.time_limit_sec must be >= 1.")
    if config.num_workers < 1:
        raise InputError("settings.num_workers must be >= 1.")
    if config.min_block_km < 0:
        raise InputError("settings.min_block_km must be >= 0.")
    if config.min_rest_gap_segments < 0:
        raise InputError("settings.min_rest_gap_segments must be >= 0.")
    if not (0.0 <= config.first_leg_ratio_min <= 1.0):
        raise InputError("settings.first_leg_ratio_min must be in [0,1].")
    if not (0.0 <= config.first_leg_ratio_max <= 1.0):
        raise InputError("settings.first_leg_ratio_max must be in [0,1].")
    if config.first_leg_ratio_min > config.first_leg_ratio_max:
        raise InputError("settings.first_leg_ratio_min cannot be greater than first_leg_ratio_max.")

    known_car_ids = {runner.car_id for runner in runners if runner.car_id is not None}
    for i, car in enumerate(config.car_block_order):
        if car not in known_car_ids:
            raise InputError(
                f"settings.car_block_order[{i}] references unknown car_id {car!r}."
            )

    warnings: List[str] = []
    current_owner: Dict[int, str] = {}
    for raw_seg_id, owner in current_owner_raw.items():
        seg_id = _to_int(raw_seg_id, f"current_owner key {raw_seg_id!r}")
        if seg_id not in seen_segment_ids:
            warnings.append(f"current_owner references unknown segment id {seg_id}; ignored.")
            continue
        if not isinstance(owner, str) or not owner:
            raise InputError(f"current_owner[{seg_id}] must be a non-empty runner name.")
        if owner not in seen_runner_names:
            warnings.append(f"current_owner segment {seg_id} references unknown runner {owner!r}; ignored.")
            continue
        current_owner[seg_id] = owner

    fixed_owner: Dict[int, str] = {}
    for raw_seg_id, owner in fixed_owner_raw.items():
        seg_id = _to_int(raw_seg_id, f"fixed_owner key {raw_seg_id!r}")
        if seg_id not in seen_segment_ids:
            raise InputError(f"fixed_owner references unknown segment id {seg_id}.")
        if not isinstance(owner, str) or not owner:
            raise InputError(f"fixed_owner[{seg_id}] must be a non-empty runner name.")
        if owner not in seen_runner_names:
            raise InputError(f"fixed_owner segment {seg_id} references unknown runner {owner!r}.")
        fixed_owner[seg_id] = owner

    return segments, runners, current_owner, fixed_owner, config, warnings


def to_contiguous_ranges(positions: List[int], segments: List[Segment]) -> List[Dict[str, int]]:
    if not positions:
        return []
    ranges: List[Dict[str, int]] = []
    start = prev = positions[0]
    for pos in positions[1:]:
        if pos == prev + 1:
            prev = pos
            continue
        ranges.append(
            {
                "start_index": start,
                "end_index": prev,
                "start_segment_id": segments[start].id,
                "end_segment_id": segments[prev].id,
            }
        )
        start = prev = pos
    ranges.append(
        {
            "start_index": start,
            "end_index": prev,
            "start_segment_id": segments[start].id,
            "end_segment_id": segments[prev].id,
        }
    )
    return ranges


def solve_runner_assignment(
    segments: List[Segment],
    runners: List[Runner],
    current_owner: Optional[Dict[int, str]] = None,
    fixed_owner: Optional[Dict[int, str]] = None,
    config: SolverConfig = SolverConfig(),
) -> Dict[str, Any]:
    if cp_model is None:
        raise RuntimeError(
            "ortools is not installed. Install with: pip install ortools"
        ) from IMPORT_ERROR

    current_owner = current_owner or {}
    fixed_owner = fixed_owner or {}
    model = cp_model.CpModel()

    r_count = len(runners)
    s_count = len(segments)
    scale = config.scale
    segment_km = [int(round(s.km * scale)) for s in segments]
    total_km_scaled = sum(segment_km)
    target_km = [int(round(r.target_km * scale)) for r in runners]

    runner_idx_by_name = {r.name: i for i, r in enumerate(runners)}
    segment_idx_by_id = {s.id: idx for idx, s in enumerate(segments)}

    x = [
        [model.NewBoolVar(f"x_r{r}_s{s}") for s in range(s_count)]
        for r in range(r_count)
    ]
    for s in range(s_count):
        model.Add(sum(x[r][s] for r in range(r_count)) == 1)

    for seg_id, owner_name in fixed_owner.items():
        s = segment_idx_by_id[seg_id]
        owner_r = runner_idx_by_name[owner_name]
        model.Add(x[owner_r][s] == 1)

    if config.require_every_runner_used:
        for r in range(r_count):
            model.Add(sum(x[r][s] for s in range(s_count)) >= 1)

    start = [
        [model.NewBoolVar(f"start_r{r}_s{s}") for s in range(s_count)]
        for r in range(r_count)
    ]
    end = [
        [model.NewBoolVar(f"end_r{r}_s{s}") for s in range(s_count)]
        for r in range(r_count)
    ]
    block_count_vars = []
    for r in range(r_count):
        model.Add(start[r][0] == x[r][0])
        for s in range(1, s_count):
            model.Add(start[r][s] >= x[r][s] - x[r][s - 1])
            model.Add(start[r][s] <= x[r][s])
            model.Add(start[r][s] <= 1 - x[r][s - 1])
        for s in range(0, s_count - 1):
            model.Add(end[r][s] >= x[r][s] - x[r][s + 1])
            model.Add(end[r][s] <= x[r][s])
            model.Add(end[r][s] <= 1 - x[r][s + 1])
        model.Add(end[r][s_count - 1] == x[r][s_count - 1])
        block_count = model.NewIntVar(0, runners[r].max_blocks, f"block_count_r{r}")
        model.Add(block_count == sum(start[r][s] for s in range(s_count)))
        model.Add(block_count == sum(end[r][s] for s in range(s_count)))
        model.Add(block_count >= runners[r].min_blocks)
        model.Add(block_count <= runners[r].max_blocks)
        block_count_vars.append(block_count)

    min_block_scaled = int(round(config.min_block_km * scale))
    if min_block_scaled > 0:
        # Forbid any maximal contiguous block shorter than min_block_km.
        # If interval [s,e] (km < threshold) is fully assigned and both neighbors are not,
        # it would form a too-short standalone block -> disallow.
        for s in range(s_count):
            interval_km = 0
            for e in range(s, s_count):
                interval_km += segment_km[e]
                if interval_km >= min_block_scaled:
                    break
                interval_len = e - s + 1
                for r in range(r_count):
                    expr = sum(x[r][t] for t in range(s, e + 1))
                    if s > 0:
                        expr -= x[r][s - 1]
                    if e < s_count - 1:
                        expr -= x[r][e + 1]
                    model.Add(expr <= interval_len - 1)

    km_vars = []
    for r in range(r_count):
        km_var = model.NewIntVar(0, total_km_scaled, f"km_r{r}")
        model.Add(km_var == sum(segment_km[s] * x[r][s] for s in range(s_count)))
        km_vars.append(km_var)

    dev_pos = []
    dev_neg = []
    objective_terms = []
    for r in range(r_count):
        dp = model.NewIntVar(0, total_km_scaled, f"dev_pos_r{r}")
        dn = model.NewIntVar(0, total_km_scaled, f"dev_neg_r{r}")
        model.Add(dp - dn == km_vars[r] - target_km[r])
        dev_pos.append(dp)
        dev_neg.append(dn)
        objective_terms.append(config.weights.overflow * dp)
        objective_terms.append(config.weights.underfill * dn)

        overflow_limit = (
            runners[r].max_overflow_km
            if runners[r].max_overflow_km is not None
            else config.max_overflow_km
        )
        if overflow_limit is not None:
            model.Add(km_vars[r] <= target_km[r] + int(round(overflow_limit * scale)))

    if config.weights.change > 0:
        for seg_id, owner_name in current_owner.items():
            if owner_name not in runner_idx_by_name or seg_id not in segment_idx_by_id:
                continue
            s = segment_idx_by_id[seg_id]
            owner_r = runner_idx_by_name[owner_name]
            changed = model.NewBoolVar(f"changed_seg_{seg_id}")
            model.Add(changed + x[owner_r][s] == 1)
            objective_terms.append(config.weights.change * changed)

    car_groups: Dict[str, List[int]] = {}
    for r, runner in enumerate(runners):
        if runner.car_id is not None:
            car_groups.setdefault(runner.car_id, []).append(r)

    car_span_vars = []
    car_block_count_vars: List[Tuple[str, Any]] = []
    car_block_boundaries: Dict[str, Dict[str, Any]] = {}
    for car_id, members in car_groups.items():
        used = [
            model.NewBoolVar(f"car_{car_id}_used_s{s}") for s in range(s_count)
        ]
        for s in range(s_count):
            model.AddMaxEquality(used[s], [x[r][s] for r in members])
        model.Add(sum(used) >= 1)

        # Car-level contiguous groups over the race timeline.
        car_start = [
            model.NewBoolVar(f"car_{car_id}_start_s{s}") for s in range(s_count)
        ]
        car_end = [
            model.NewBoolVar(f"car_{car_id}_end_s{s}") for s in range(s_count)
        ]
        model.Add(car_start[0] == used[0])
        for s in range(1, s_count):
            model.Add(car_start[s] >= used[s] - used[s - 1])
            model.Add(car_start[s] <= used[s])
            model.Add(car_start[s] <= 1 - used[s - 1])
        for s in range(0, s_count - 1):
            model.Add(car_end[s] >= used[s] - used[s + 1])
            model.Add(car_end[s] <= used[s])
            model.Add(car_end[s] <= 1 - used[s + 1])
        model.Add(car_end[s_count - 1] == used[s_count - 1])
        expected_max = max(runners[r].max_blocks for r in members)
        car_block_count = model.NewIntVar(0, expected_max, f"car_{car_id}_block_count")
        model.Add(car_block_count == sum(car_start))
        model.Add(car_block_count == sum(car_end))
        car_block_count_vars.append((car_id, car_block_count))

        if config.enforce_car_block_grouping:
            # Derived expected number of car groups from runner min blocks.
            # Example: all 1-block members -> 1 car block, all 2-block members -> 2 car blocks.
            expected_car_blocks = max(runners[r].min_blocks for r in members)
            model.Add(car_block_count == expected_car_blocks)

        first_start_candidates = []
        second_start_candidates = []
        first_end_candidates = []
        second_end_candidates = []
        for s in range(s_count):
            first_start_c = model.NewIntVar(0, s_count, f"car_{car_id}_first_start_c{s}")
            model.Add(first_start_c == s).OnlyEnforceIf(car_start[s])
            model.Add(first_start_c == s_count).OnlyEnforceIf(car_start[s].Not())
            first_start_candidates.append(first_start_c)

            second_start_c = model.NewIntVar(0, s_count - 1, f"car_{car_id}_second_start_c{s}")
            model.Add(second_start_c == s).OnlyEnforceIf(car_start[s])
            model.Add(second_start_c == 0).OnlyEnforceIf(car_start[s].Not())
            second_start_candidates.append(second_start_c)

            first_end_c = model.NewIntVar(0, s_count, f"car_{car_id}_first_end_c{s}")
            model.Add(first_end_c == s).OnlyEnforceIf(car_end[s])
            model.Add(first_end_c == s_count).OnlyEnforceIf(car_end[s].Not())
            first_end_candidates.append(first_end_c)

            second_end_c = model.NewIntVar(0, s_count - 1, f"car_{car_id}_second_end_c{s}")
            model.Add(second_end_c == s).OnlyEnforceIf(car_end[s])
            model.Add(second_end_c == 0).OnlyEnforceIf(car_end[s].Not())
            second_end_candidates.append(second_end_c)

        first_start = model.NewIntVar(0, s_count, f"car_{car_id}_first_start")
        second_start = model.NewIntVar(0, s_count - 1, f"car_{car_id}_second_start")
        first_end = model.NewIntVar(0, s_count, f"car_{car_id}_first_end")
        second_end = model.NewIntVar(0, s_count - 1, f"car_{car_id}_second_end")
        model.AddMinEquality(first_start, first_start_candidates)
        model.AddMaxEquality(second_start, second_start_candidates)
        model.AddMinEquality(first_end, first_end_candidates)
        model.AddMaxEquality(second_end, second_end_candidates)

        car_block_boundaries[car_id] = {
            "block_count": car_block_count,
            "start_1": first_start,
            "end_1": first_end,
            "start_2": second_start,
            "end_2": second_end,
        }

        if config.weights.car_span > 0 and len(members) >= 2:
            first_candidates = []
            last_candidates = []
            for s in range(s_count):
                first_c = model.NewIntVar(0, s_count, f"car_{car_id}_first_c{s}")
                model.Add(first_c == s).OnlyEnforceIf(used[s])
                model.Add(first_c == s_count).OnlyEnforceIf(used[s].Not())
                first_candidates.append(first_c)

                last_c = model.NewIntVar(0, s_count - 1, f"car_{car_id}_last_c{s}")
                model.Add(last_c == s).OnlyEnforceIf(used[s])
                model.Add(last_c == 0).OnlyEnforceIf(used[s].Not())
                last_candidates.append(last_c)

            first = model.NewIntVar(0, s_count, f"car_{car_id}_first")
            last = model.NewIntVar(0, s_count - 1, f"car_{car_id}_last")
            model.AddMinEquality(first, first_candidates)
            model.AddMaxEquality(last, last_candidates)

            span = model.NewIntVar(0, s_count - 1, f"car_{car_id}_span")
            model.Add(span == last - first)
            car_span_vars.append((car_id, span))
            objective_terms.append(config.weights.car_span * span)

    if config.car_block_order:
        occurrence_required: Dict[str, int] = {}
        for car in config.car_block_order:
            occurrence_required[car] = occurrence_required.get(car, 0) + 1

        for car, req_count in occurrence_required.items():
            if car not in car_block_boundaries:
                raise InputError(f"Car {car!r} appears in car_block_order but has no members.")
            if req_count > 2:
                raise InputError(
                    "car_block_order currently supports at most 2 occurrences per car."
                )
            model.Add(car_block_boundaries[car]["block_count"] == req_count)

        seen: Dict[str, int] = {}
        ordered_blocks: List[Tuple[Any, Any]] = []
        for car in config.car_block_order:
            occ = seen.get(car, 0) + 1
            seen[car] = occ
            if occ == 1:
                ordered_blocks.append(
                    (car_block_boundaries[car]["start_1"], car_block_boundaries[car]["end_1"])
                )
            elif occ == 2:
                ordered_blocks.append(
                    (car_block_boundaries[car]["start_2"], car_block_boundaries[car]["end_2"])
                )
            else:
                raise InputError(
                    "car_block_order currently supports at most 2 occurrences per car."
                )

        for i in range(len(ordered_blocks) - 1):
            cur_end = ordered_blocks[i][1]
            next_start = ordered_blocks[i + 1][0]
            model.Add(cur_end + 1 <= next_start)

    double_runner_data: Dict[int, Dict[str, Any]] = {}
    for r, runner in enumerate(runners):
        if runner.min_blocks != 2:
            continue

        second_start_candidates = []
        first_end_candidates = []
        for s in range(s_count):
            second_start_c = model.NewIntVar(
                0, s_count - 1, f"runner_{r}_second_start_c{s}"
            )
            model.Add(second_start_c == s).OnlyEnforceIf(start[r][s])
            model.Add(second_start_c == 0).OnlyEnforceIf(start[r][s].Not())
            second_start_candidates.append(second_start_c)

            first_end_c = model.NewIntVar(
                0, s_count - 1, f"runner_{r}_first_end_c{s}"
            )
            model.Add(first_end_c == s).OnlyEnforceIf(end[r][s])
            model.Add(first_end_c == s_count - 1).OnlyEnforceIf(end[r][s].Not())
            first_end_candidates.append(first_end_c)

        second_start = model.NewIntVar(0, s_count - 1, f"runner_{r}_second_start")
        first_end = model.NewIntVar(0, s_count - 1, f"runner_{r}_first_end")
        model.AddMaxEquality(second_start, second_start_candidates)
        model.AddMinEquality(first_end, first_end_candidates)

        rest_gap = model.NewIntVar(0, s_count - 1, f"runner_{r}_rest_gap")
        model.Add(rest_gap == second_start - first_end - 1)
        min_rest_gap = (
            runner.min_rest_gap_segments
            if runner.min_rest_gap_segments is not None
            else config.min_rest_gap_segments
        )
        if min_rest_gap > 0:
            model.Add(rest_gap >= min_rest_gap)

        double_runner_data[r] = {
            "first_end": first_end,
            "second_start": second_start,
            "rest_gap": rest_gap,
        }

        if config.weights.rest_gap > 0 and runner.rest_priority > 0:
            # Minimize penalty => maximize gap.
            rest_penalty = model.NewIntVar(0, s_count - 1, f"runner_{r}_rest_penalty")
            model.Add(rest_penalty == (s_count - 1) - rest_gap)
            objective_terms.append(
                config.weights.rest_gap * runner.rest_priority * rest_penalty
            )

        # Optional first-leg ratio constraints for 2-block runners:
        # first block distance must be in [min_ratio, max_ratio] of runner total distance.
        if config.first_leg_ratio_min > 0.0 or config.first_leg_ratio_max < 1.0:
            prefix_vars = []
            for e in range(s_count):
                prefix_km = model.NewIntVar(
                    0, total_km_scaled, f"runner_{r}_prefix_km_e{e}"
                )
                model.Add(
                    prefix_km
                    == sum(segment_km[t] * x[r][t] for t in range(e + 1))
                )
                prefix_vars.append(prefix_km)

            first_block_km = model.NewIntVar(
                0, total_km_scaled, f"runner_{r}_first_block_km"
            )
            model.AddElement(first_end, prefix_vars, first_block_km)
            double_runner_data[r]["first_block_km"] = first_block_km

            ratio_scale = 1000
            min_num = int(round(config.first_leg_ratio_min * ratio_scale))
            max_num = int(round(config.first_leg_ratio_max * ratio_scale))
            model.Add(ratio_scale * first_block_km >= min_num * km_vars[r])
            model.Add(ratio_scale * first_block_km <= max_num * km_vars[r])

    if config.enforce_second_round_order:
        double_runner_ids = sorted(double_runner_data.keys())
        m = s_count
        for i in range(len(double_runner_ids)):
            for j in range(i + 1, len(double_runner_ids)):
                ra = double_runner_ids[i]
                rb = double_runner_ids[j]
                first_end_a = double_runner_data[ra]["first_end"]
                first_end_b = double_runner_data[rb]["first_end"]
                second_start_a = double_runner_data[ra]["second_start"]
                second_start_b = double_runner_data[rb]["second_start"]

                # order_ab=1 means A is before B in first round.
                order_ab = model.NewBoolVar(f"order_r{ra}_r{rb}")
                model.Add(first_end_a + 1 <= first_end_b + m * (1 - order_ab))
                model.Add(first_end_b + 1 <= first_end_a + m * order_ab)

                # Enforce same ordering in second round.
                model.Add(second_start_a + 1 <= second_start_b + m * (1 - order_ab))
                model.Add(second_start_b + 1 <= second_start_a + m * order_ab)

    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.time_limit_sec
    solver.parameters.num_search_workers = config.num_workers

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status": "INFEASIBLE_OR_UNKNOWN",
            "status_code": int(status),
            "objective": None,
            "message": "No feasible solution found with current constraints.",
        }

    status_name = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"

    result_runners: List[Dict[str, Any]] = []
    segment_owner: Dict[int, str] = {}
    for r, runner in enumerate(runners):
        assigned_positions = [s for s in range(s_count) if solver.Value(x[r][s]) == 1]
        for s in assigned_positions:
            segment_owner[segments[s].id] = runner.name
        assigned_segment_ids = [segments[s].id for s in assigned_positions]
        km_value = solver.Value(km_vars[r]) / scale
        overflow = max(km_value - runner.target_km, 0.0)
        underfill = max(runner.target_km - km_value, 0.0)
        rest_gap_segments = (
            int(solver.Value(double_runner_data[r]["rest_gap"]))
            if r in double_runner_data
            else None
        )
        first_block_km = (
            round(solver.Value(double_runner_data[r]["first_block_km"]) / scale, 3)
            if r in double_runner_data and "first_block_km" in double_runner_data[r]
            else None
        )
        first_block_ratio = (
            round(first_block_km / km_value, 4)
            if first_block_km is not None and km_value > 0
            else None
        )
        result_runners.append(
            {
                "name": runner.name,
                "car_id": runner.car_id,
                "target_km": runner.target_km,
                "assigned_km": round(km_value, 3),
                "overflow_km": round(overflow, 3),
                "underfill_km": round(underfill, 3),
                "max_blocks": runner.max_blocks,
                "min_blocks": runner.min_blocks,
                "rest_priority": runner.rest_priority,
                "rest_gap_segments": rest_gap_segments,
                "first_block_km": first_block_km,
                "first_block_ratio": first_block_ratio,
                "block_count": solver.Value(block_count_vars[r]),
                "segments": assigned_segment_ids,
                "ranges": to_contiguous_ranges(assigned_positions, segments),
            }
        )

    car_spans = {
        car_id: int(solver.Value(span_var)) for car_id, span_var in car_span_vars
    }
    car_block_counts = {
        car_id: int(solver.Value(block_var))
        for car_id, block_var in car_block_count_vars
    }

    return {
        "status": status_name,
        "status_code": int(status),
        "objective": int(round(solver.ObjectiveValue())),
        "scale": scale,
        "segment_owner": segment_owner,
        "fixed_owner": {str(k): v for k, v in fixed_owner.items()},
        "runners": result_runners,
        "car_span_by_car_id": car_spans,
        "car_block_count_by_car_id": car_block_counts,
        "car_block_order": list(config.car_block_order),
    }


def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Optimize relay runner assignment with CP-SAT."
    )
    parser.add_argument("--input", required=True, help="Path to input JSON.")
    parser.add_argument(
        "--output",
        default="-",
        help="Path to output JSON (default: stdout).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args(argv)

    if cp_model is None:
        print(
            "Missing dependency: ortools. Install with `pip install ortools`.",
            file=sys.stderr,
        )
        return 2

    try:
        payload = _read_json(Path(args.input))
        segments, runners, current_owner, fixed_owner, config, warnings = parse_input(payload)
        result = solve_runner_assignment(
            segments=segments,
            runners=runners,
            current_owner=current_owner,
            fixed_owner=fixed_owner,
            config=config,
        )
        if warnings:
            result["warnings"] = warnings
    except (InputError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output_text = json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.output == "-":
        print(output_text)
    else:
        out_path = Path(args.output)
        out_path.write_text(output_text + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
