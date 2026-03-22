#!/usr/bin/env python3
"""Build static HTML report for UB planning from final.csv."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


class ReportError(ValueError):
    pass


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


def _pace_to_min_per_km(pace: str) -> float:
    m = re.fullmatch(r"(\d+):(\d{2})", pace)
    if not m:
        raise ReportError(f"Invalid pace format: {pace!r}")
    return int(m.group(1)) + int(m.group(2)) / 60.0


def _parse_duration_minutes(value: str) -> Optional[float]:
    s = _clean(value)
    if not s:
        return None
    m = re.fullmatch(r"(\d+):(\d{2})(?::(\d{2}))?", s)
    if not m:
        return None
    h = int(m.group(1))
    mm = int(m.group(2))
    ss = int(m.group(3) or 0)
    return h * 60 + mm + ss / 60.0


def _format_duration(minutes: float) -> str:
    total = int(round(minutes))
    h = total // 60
    m = total % 60
    return f"{h:02d}:{m:02d}"


def _extract_info_tags(info: str) -> List[str]:
    text = _clean(info).lower()
    rules = [
        ("parkoló", "parkolas"),
        ("vasút", "vasuti atjaro"),
        ("zebra", "zebra"),
        ("emelked", "emelkedo"),
        ("lejt", "lejto"),
        ("sötét", "sotet szakasz"),
        ("bringás", "bringas frissites"),
        ("wc", "wc"),
        ("mosdó", "mosdo"),
        ("friss", "frissites"),
        ("kanyar", "kanyargos"),
    ]
    tags: List[str] = []
    for needle, label in rules:
        if needle in text and label not in tags:
            tags.append(label)
    return tags


def _load_stage_metadata(path: Path) -> Dict[int, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(payload)
    except Exception:
        return {}

    rows = data.get("stages", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("seg_id")
        try:
            sid = int(seg_id)
        except (TypeError, ValueError):
            continue
        lat = _parse_hu_float(item.get("lat"))
        lon = _parse_hu_float(item.get("lon"))
        if lat is None or lon is None:
            continue
        out[sid] = {
            "lat": float(lat),
            "lon": float(lon),
            "google_maps_url": _clean(item.get("google_maps_url")),
            "title": _clean(item.get("title")),
        }
    return out


def _load_runner_car_map(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(payload)
    except Exception:
        return {}
    runners = data.get("runners", []) if isinstance(data, dict) else []
    if not isinstance(runners, list):
        return {}
    out: Dict[str, str] = {}
    for item in runners:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name"))
        car_id = _clean(item.get("car_id"))
        if not name or not car_id:
            continue
        out[name] = car_id
    return out


def _parse_final_csv(final_csv_path: Path) -> Dict[str, Any]:
    rows: List[List[str]] = []
    with final_csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = [list(r) for r in reader]

    if not rows:
        return {"runner_rows": [], "segment_rows": [], "start_time": None}

    # Start time marker row.
    start_time: Optional[time] = None
    for row in rows:
        for idx, cell in enumerate(row):
            if "Válassz rajtidőpontot" in _clean(cell):
                if idx + 1 < len(row):
                    raw = _clean(row[idx + 1])
                    try:
                        start_time = datetime.strptime(raw, "%H:%M:%S").time()
                    except ValueError:
                        pass
                break
        if start_time is not None:
            break

    # Runner summary rows (left table).
    runner_rows: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if len(row) < 6:
            continue
        name = _clean(row[2])
        pace = _clean(row[3])
        target = _parse_hu_float(row[4])
        actual = _parse_hu_float(row[5])
        if not name or name == "CSAPATTAG":
            continue
        if name in seen:
            continue
        if target is None and actual is None:
            continue
        seen.add(name)
        runner_rows.append({
            "name": name,
            "pace": pace,
            "target_km": target,
            "actual_km": actual,
        })

    # Segment table columns.
    header_idx = -1
    km_col = -1
    from_col = -1
    to_col = -1
    runner_col = -1
    biker_col = -1
    pace_col = -1
    run_col = -1
    arr_col = -1
    day_col = -1
    stage_name_col = -1
    info_col = -1

    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            c = _clean(cell)
            if c == "SZAKASZ HOSSZA":
                header_idx = i
                km_col = j
            elif c == "INDULÁS":
                from_col = j
            elif c == "ÉRKEZÉS":
                to_col = j
            elif c == "FUTÓ":
                runner_col = j
            elif c == "KERÉKPÁROS":
                biker_col = j
            elif c == "TEMPÓ":
                pace_col = j
            elif c == "FUTÁSIDŐ":
                run_col = j
            elif c == "VÁLTÓPONTHOZ ÉRKEZÉS IDEJE":
                arr_col = j
            elif c == "Napszak":
                day_col = j
            elif c == "SZAKASZ NÉV":
                stage_name_col = j
            elif c == "Info":
                info_col = j
        if header_idx >= 0 and km_col >= 0 and runner_col >= 0:
            break

    segment_rows: List[Dict[str, Any]] = []
    if header_idx >= 0 and km_col > 0:
        seg_col = km_col - 1
        for row in rows[header_idx + 1 :]:
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
            segment_rows.append({
                "seg_id": seg_id,
                "runner": _clean(row[runner_col]) if runner_col < len(row) else "",
                "biker": _clean(row[biker_col]) if biker_col >= 0 and biker_col < len(row) else "",
                "km": km,
                "pace": _clean(row[pace_col]) if pace_col < len(row) else "",
                "run_time": _clean(row[run_col]) if run_col < len(row) else "",
                "arrival": _clean(row[arr_col]) if arr_col < len(row) else "",
                "day": _clean(row[day_col]) if day_col < len(row) else "",
                "stage_name": _clean(row[stage_name_col]) if stage_name_col >= 0 and stage_name_col < len(row) else "",
                "info": _clean(row[info_col]) if info_col >= 0 and info_col < len(row) else "",
                "stage": (
                    f"{_clean(row[from_col])} -> {_clean(row[to_col])}"
                    if from_col < len(row) and to_col < len(row)
                    else ""
                ),
            })

    segment_rows.sort(key=lambda x: x["seg_id"])
    return {
        "runner_rows": runner_rows,
        "segment_rows": segment_rows,
        "start_time": start_time,
    }


def _make_blocks(segment_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    if not segment_rows:
        return blocks

    cur = dict(segment_rows[0])
    cur["start_seg"] = segment_rows[0]["seg_id"]
    cur["end_seg"] = segment_rows[0]["seg_id"]
    for row in segment_rows[1:]:
        if row["runner"] == cur["runner"] and row["seg_id"] == cur["end_seg"] + 1:
            cur["end_seg"] = row["seg_id"]
            cur["end"] = row["end"]
            cur["km"] += row.get("km", 0.0)
            cur["duration_min"] += row.get("duration_min", 0.0)
            cur["dark_min"] += row.get("dark_min", 0.0)
            cur["stage_to"] = row.get("stage_to", cur.get("stage_to", ""))
        else:
            blocks.append(cur)
            cur = dict(row)
            cur["start_seg"] = row["seg_id"]
            cur["end_seg"] = row["seg_id"]
    blocks.append(cur)
    return blocks


def _report_from_final_csv(final_csv_data: Dict[str, Any], race_date: date, race_start_time: time) -> Dict[str, Any]:
    segs = final_csv_data.get("segment_rows", [])
    if not segs:
        raise ReportError("final.csv does not contain parseable segment rows.")

    start_dt = datetime.combine(race_date, race_start_time)
    cur = start_dt
    segments: List[Dict[str, Any]] = []
    for s in sorted(segs, key=lambda x: x["seg_id"]):
        dur = _parse_duration_minutes(s.get("run_time", ""))
        if dur is None:
            pace_min = _parse_duration_minutes(s.get("pace", ""))
            if pace_min is None:
                pace_min = 6.5
            dur = float(s.get("km", 0.0)) * pace_min
        s0 = cur
        s1 = cur + timedelta(minutes=dur)
        day = _clean(s.get("day", ""))
        dark_min = dur if "🌙" in day else 0.0
        pace_raw = _clean(s.get("pace", ""))
        pace_val = _pace_to_min_per_km(pace_raw) if re.fullmatch(r"\d+:\d{2}", pace_raw) else 0.0
        stage = _clean(s.get("stage", ""))
        part = stage.split("->")
        stage_from = part[0].strip() if part else ""
        stage_to = part[1].strip() if len(part) > 1 else ""
        segments.append({
            "seg_id": int(s["seg_id"]),
            "runner": _clean(s.get("runner", "")),
            "biker": _clean(s.get("biker", "")),
            "km": float(s.get("km", 0.0)),
            "pace": pace_val,
            "pace_raw": pace_raw,
            "stage_from": stage_from,
            "stage_to": stage_to,
            "stage": stage,
            "start": s0,
            "end": s1,
            "duration_min": dur,
            "dark_min": dark_min,
            "day": day,
            "arrival": _clean(s.get("arrival", "")),
            "run_time": _clean(s.get("run_time", "")),
            "stage_name": _clean(s.get("stage_name", "")),
            "info": _clean(s.get("info", "")),
        })
        cur = s1

    blocks = _make_blocks(segments)

    target_by_runner = {}
    pace_by_runner_raw = {}
    for r in final_csv_data.get("runner_rows", []):
        name = _clean(r.get("name", ""))
        if not name:
            continue
        target_by_runner[name] = r.get("target_km")
        pace_by_runner_raw[name] = _clean(r.get("pace", ""))

    totals: Dict[str, Dict[str, float]] = defaultdict(lambda: {"km": 0.0, "dur": 0.0, "dark": 0.0, "blocks": 0.0})
    for b in blocks:
        t = totals[b["runner"]]
        t["km"] += b["km"]
        t["dur"] += b["duration_min"]
        t["dark"] += b["dark_min"]
        t["blocks"] += 1

    runners: List[Dict[str, Any]] = []
    for name, t in totals.items():
        target = target_by_runner.get(name)
        assigned = t["km"]
        overflow = max(0.0, assigned - target) if target is not None else 0.0
        underfill = max(0.0, target - assigned) if target is not None else 0.0
        dark_pct = 0.0 if t["dur"] <= 0 else 100.0 * t["dark"] / t["dur"]
        runners.append({
            "name": name,
            "car_id": "-",
            "target_km": float(target) if target is not None else 0.0,
            "assigned_km": assigned,
            "overflow_km": overflow,
            "underfill_km": underfill,
            "first_block_ratio": None,
            "rest_gap_segments": None,
            "block_count": int(t["blocks"]),
            "duration_min": t["dur"],
            "dark_min": t["dark"],
            "dark_pct": dark_pct,
            "pace_raw": pace_by_runner_raw.get(name, ""),
        })
    runners.sort(key=lambda x: (x["dark_min"] == 0, -x["dark_min"], x["name"]))

    return {
        "mode": "final_only",
        "status": "CSV",
        "objective": "-",
        "start_dt": start_dt,
        "finish_dt": cur,
        "duration_min": (cur - start_dt).total_seconds() / 60.0,
        "runner_rows": runners,
        "blocks": blocks,
        "segments": segments,
    }


def _render_html(
    report: Dict[str, Any],
    title: str,
    team_name: str,
    final_csv_snapshot: Optional[Dict[str, Any]] = None,
    stage_meta: Optional[Dict[int, Dict[str, Any]]] = None,
    runner_car_map: Optional[Dict[str, str]] = None,
) -> str:
    del final_csv_snapshot
    stage_meta = stage_meta or {}
    runner_car_map = runner_car_map or {}
    status = html.escape(str(report.get("status", "-")))
    start_dt = report["start_dt"].strftime("%Y-%m-%d %H:%M")
    finish_dt = report["finish_dt"].strftime("%Y-%m-%d %H:%M")
    total_duration = _format_duration(float(report.get("duration_min", 0.0)))
    coord_count = len(stage_meta)

    segments_sorted = sorted(report["segments"], key=lambda x: int(x["seg_id"]))
    blocks_sorted = sorted(report["blocks"], key=lambda x: int(x["start_seg"]))
    runner_summary = {str(r["name"]): r for r in report["runner_rows"]}

    def _coord_for_seg(seg_id: int) -> Optional[Dict[str, Any]]:
        return stage_meta.get(int(seg_id))

    def _coord_links(meta: Optional[Dict[str, Any]], cls: str = "seg-nav") -> str:
        if not meta:
            return ""
        lat = float(meta["lat"])
        lon = float(meta["lon"])
        coord_text = f"{lat:.6f}, {lon:.6f}"
        google = _clean(meta.get("google_maps_url"))
        if not google:
            google = f"https://www.google.com/maps?q={lat},{lon}"
        waze = f"https://waze.com/ul?ll={lat},{lon}&navigate=yes"
        return (
            f"<div class='{cls}'>"
            f"Koordináta: {html.escape(coord_text)} | "
            f"<a href='{html.escape(google)}' target='_blank' rel='noopener noreferrer'>Google Maps</a> | "
            f"<a href='{html.escape(waze)}' target='_blank' rel='noopener noreferrer'>Waze</a>"
            "</div>"
        )

    point_coords: Dict[str, Dict[str, Any]] = {}
    for s in segments_sorted:
        point = _clean(s.get("stage_to", ""))
        coord = _coord_for_seg(int(s["seg_id"]))
        if point and coord:
            point_coords[point] = coord

    runner_first_seg: Dict[str, int] = {}
    segments_by_runner: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in segments_sorted:
        name = str(s["runner"])
        segments_by_runner[name].append(s)
        if name not in runner_first_seg:
            runner_first_seg[name] = int(s["seg_id"])
    runner_order = [name for name, _ in sorted(runner_first_seg.items(), key=lambda kv: kv[1])]

    def _fmt_km(value: Optional[Any]) -> str:
        if value in (None, ""):
            return "-"
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return "-"

    total_km_all = sum(float(s.get("km", 0.0)) for s in segments_sorted)
    night_km = sum(float(s.get("km", 0.0)) for s in segments_sorted if "🌙" in _clean(s.get("day", "")))
    day_km = max(0.0, total_km_all - night_km)
    biker_segments = [s for s in segments_sorted if _clean(s.get("biker", ""))]
    biker_km = sum(float(s.get("km", 0.0)) for s in biker_segments)
    unique_bikers = sorted({_clean(s.get("biker", "")) for s in biker_segments if _clean(s.get("biker", ""))})

    runner_anchor_by_name: Dict[str, str] = {}
    runner_nav: List[str] = []
    runner_sections: List[str] = []

    for idx, runner_name in enumerate(runner_order, start=1):
        anchor = f"runner-{idx}"
        runner_anchor_by_name[runner_name] = anchor
        runner_nav.append(
            f"<a class='runner-pill' href='#{anchor}' data-open-tab='runners'>{html.escape(runner_name)}</a>"
        )

        r_segments = segments_by_runner.get(runner_name, [])
        total_km = sum(float(s.get("km", 0.0)) for s in r_segments)
        total_min = sum(float(s.get("duration_min", 0.0)) for s in r_segments)
        dark_min = sum(float(s.get("dark_min", 0.0)) for s in r_segments)
        summary = runner_summary.get(runner_name)
        target_txt = "-" if not summary else f"{float(summary.get('target_km', 0.0)):.1f}"
        assigned_txt = "-" if not summary else f"{float(summary.get('assigned_km', 0.0)):.1f}"
        overflow_txt = "-" if not summary else f"{float(summary.get('overflow_km', 0.0)):.1f}"

        seg_cards: List[str] = []
        for s in r_segments:
            day_raw = _clean(s.get("day", ""))
            day_label = "Éjszaka" if "🌙" in day_raw else "Nappal"
            day_icon = "🌙" if "🌙" in day_raw else "☀️"
            sponsor = _clean(s.get("stage_name", ""))
            info_text = _clean(s.get("info", "")) or "Nincs külön leírás ehhez a szakaszhoz."
            tags = _extract_info_tags(info_text)
            tags_html = "".join(f"<span class='mini-tag'>{html.escape(t)}</span>" for t in tags)
            biker = _clean(s.get("biker", "")) or "nincs"
            pace_text = _clean(s.get("pace_raw", "")) or _clean(s.get("pace", ""))
            run_time_text = _clean(s.get("run_time", "")) or _format_duration(float(s.get("duration_min", 0.0)))
            sponsor_html = f"<div class='seg-sponsor'>{html.escape(sponsor)}</div>" if sponsor else ""
            tag_row_html = f"<div class='tag-row'>{tags_html}</div>" if tags_html else ""
            coord_html = _coord_links(_coord_for_seg(int(s["seg_id"])))
            seg_cards.append(
                "<article class='seg-card'>"
                "<div class='seg-top'>"
                f"<div class='seg-id'>Szakasz {int(s['seg_id'])}</div>"
                f"<div class='seg-km'>{float(s.get('km', 0.0)):.1f} km</div>"
                "</div>"
                f"<div class='seg-route'>{html.escape(str(s.get('stage_from', '')))} → {html.escape(str(s.get('stage_to', '')))}</div>"
                f"<div class='seg-time'>{s['start'].strftime('%m.%d %H:%M')} - {s['end'].strftime('%m.%d %H:%M')}</div>"
                "<div class='seg-meta'>"
                f"<span>{day_icon} {day_label}</span>"
                f"<span>Tempó: {html.escape(str(pace_text))}</span>"
                f"<span>Idő: {html.escape(str(run_time_text))}</span>"
                f"<span>Kísérő: {html.escape(str(biker))}</span>"
                "</div>"
                f"{sponsor_html}"
                f"{tag_row_html}"
                f"{coord_html}"
                f"<p class='seg-info'>{html.escape(info_text)}</p>"
                "</article>"
            )

        runner_sections.append(
            f"<section class='panel runner-panel' id='{anchor}'>"
            f"<h2>{html.escape(runner_name)}</h2>"
            "<div class='runner-kpis'>"
            f"<span class='kpi'>Szakasz: {len(r_segments)}</span>"
            f"<span class='kpi'>Összesen: {total_km:.1f} km</span>"
            f"<span class='kpi'>Futásidő: {_format_duration(total_min)}</span>"
            f"<span class='kpi'>Sötét: {dark_min:.1f} perc</span>"
            f"<span class='kpi'>Cél/Kiosztás: {target_txt}/{assigned_txt} km</span>"
            f"<span class='kpi'>Túllépés: {overflow_txt} km</span>"
            "</div>"
            f"<div class='seg-list'>{''.join(seg_cards)}</div>"
            "</section>"
        )

    cumulative_end_km: Dict[int, float] = {}
    cumulative = 0.0
    for s in segments_sorted:
        cumulative += float(s.get("km", 0.0))
        cumulative_end_km[int(s["seg_id"])] = cumulative

    switch_start_seg_ids = {int(b["start_seg"]) for b in blocks_sorted}
    timeline_rows: List[str] = []
    timeline_mobile_cards: List[str] = []
    used_car_ids: set[str] = set()

    def _car_class(car_id: str) -> str:
        cid = _clean(car_id)
        if not cid or cid == "?":
            return "car-na"
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", cid)
        return f"car-{safe}"

    def _car_label(car_id: str) -> str:
        cid = _clean(car_id)
        return f"Autó {cid}" if cid and cid != "?" else "Autó n/a"

    for s in segments_sorted:
        sid = int(s["seg_id"])
        km = float(s.get("km", 0.0))
        km_end = cumulative_end_km.get(sid, 0.0)
        runner_name = _clean(s.get("runner", ""))
        car_id = _clean(runner_car_map.get(runner_name, "")) or "?"
        used_car_ids.add(car_id)
        runner_anchor = runner_anchor_by_name.get(runner_name, "")
        runner_link = (
            f"<a href='#{runner_anchor}' data-open-tab='runners'>{html.escape(runner_name)}</a>"
            if runner_anchor
            else html.escape(runner_name)
        )
        switch_badge = ""
        if sid == 1:
            switch_badge = "<span class='switch-badge'>RAJT</span>"
        elif sid in switch_start_seg_ids:
            switch_badge = "<span class='switch-badge'>VÁLTÁS</span>"
        row_cls = f"timeline-row {_car_class(car_id)}"
        if sid in switch_start_seg_ids and sid != 1:
            row_cls += " switch-row"
        timeline_rows.append(
            f"<tr class='{row_cls}'>"
            f"<td>{sid}</td>"
            f"<td><span class='car-chip {_car_class(car_id)}'>{html.escape(_car_label(car_id))}</span></td>"
            f"<td>{runner_link}</td>"
            f"<td>{html.escape(_clean(s.get('biker', '')) or '-')}</td>"
            f"<td>{switch_badge}</td>"
            f"<td>{km:.1f}</td>"
            f"<td>{km_end:.1f}</td>"
            f"<td>{html.escape(str(s.get('stage_from', '')))} → {html.escape(str(s.get('stage_to', '')))}</td>"
            f"<td>{s['start'].strftime('%m.%d %H:%M')} - {s['end'].strftime('%m.%d %H:%M')}</td>"
            "</tr>"
        )
        timeline_mobile_cards.append(
            "<article class='timeline-card-mobile'>"
            f"<div class='timeline-card-top'><span class='car-chip {_car_class(car_id)}'>{html.escape(_car_label(car_id))}</span><strong>#{sid}</strong></div>"
            f"<div class='timeline-card-line'><span>Futó:</span><span>{runner_link}</span></div>"
            f"<div class='timeline-card-line'><span>Kerékpáros:</span><span>{html.escape(_clean(s.get('biker', '')) or '-')}</span></div>"
            f"<div class='timeline-card-line'><span>Szakasz:</span><span>{km:.1f} km</span></div>"
            f"<div class='timeline-card-line'><span>Eddigi táv:</span><strong>{km_end:.1f} km</strong></div>"
            f"<div class='timeline-card-line'><span>Útvonal:</span><span>{html.escape(str(s.get('stage_from', '')))} → {html.escape(str(s.get('stage_to', '')))}</span></div>"
            f"<div class='timeline-card-line'><span>Idő:</span><span>{s['start'].strftime('%m.%d %H:%M')} - {s['end'].strftime('%m.%d %H:%M')}</span></div>"
            + (f"<div class='timeline-card-badge'>{switch_badge}</div>" if switch_badge else "")
            + "</article>"
        )

    def _car_sort_key(value: str) -> tuple[int, Any]:
        cid = _clean(value)
        if cid.isdigit():
            return (0, int(cid))
        return (1, cid)

    car_legend = "".join(
        f"<span class='car-chip {_car_class(cid)}'>{html.escape(_car_label(cid))}</span>"
        for cid in sorted(used_car_ids, key=_car_sort_key)
    )

    switch_cards: List[str] = []
    for idx, b in enumerate(blocks_sorted):
        prev_runner = "Rajt" if idx == 0 else str(blocks_sorted[idx - 1].get("runner", ""))
        start_point = str(b.get("stage_from", "Rajt")) if idx > 0 else "Rajt"
        stage_name = _clean(b.get("stage_name", ""))
        stage_note_html = f"<div class='switch-note'>{html.escape(stage_name)}</div>" if stage_name else ""
        switch_coord_html = _coord_links(point_coords.get(start_point), cls="switch-nav")
        start_seg = int(b["start_seg"])
        km_at_switch = 0.0 if start_seg <= 1 else cumulative_end_km.get(start_seg - 1, 0.0)
        switch_cards.append(
            "<article class='switch-card'>"
            f"<div class='switch-title'>Váltás {idx + 1}: {html.escape(prev_runner)} → {html.escape(str(b.get('runner', '')))}</div>"
            f"<div class='switch-line'><span>Idő:</span><strong>{b['start'].strftime('%m.%d %H:%M')}</strong></div>"
            f"<div class='switch-line'><span>Hely:</span><strong>{html.escape(start_point)}</strong></div>"
            f"<div class='switch-line'><span>Blokk:</span><strong>{int(b['start_seg'])}-{int(b['end_seg'])}</strong></div>"
            f"<div class='switch-line'><span>Eddigi táv:</span><strong>{km_at_switch:.1f} km</strong></div>"
            f"<div class='switch-line'><span>Következő érkezés:</span><strong>{b['end'].strftime('%m.%d %H:%M')}</strong></div>"
            f"{stage_note_html}"
            f"{switch_coord_html}"
            "</article>"
        )

    return f"""<!doctype html>
<html lang=\"hu\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg:#f4f7fb;
      --card:#ffffff;
      --line:#d8dfeb;
      --ink:#1b2333;
      --muted:#5c6780;
      --accent:#1f4c9a;
      --accent-soft:#e9f0ff;
      --tab:#f3f6ff;
      --tab-active:#1f4c9a;
      --tab-active-ink:#ffffff;
    }}
    * {{ box-sizing:border-box; }}
    html, body {{ margin:0; }}
    body {{
      color:var(--ink);
      font-family:"IBM Plex Sans","Segoe UI",Roboto,Arial,sans-serif;
      background:
        radial-gradient(1000px 520px at -10% -20%, #dce9ff 0%, rgba(220,233,255,0) 60%),
        radial-gradient(900px 440px at 120% -10%, #ffe8cd 0%, rgba(255,232,205,0) 58%),
        var(--bg);
    }}
    main {{ max-width:1040px; margin:0 auto; padding:12px 10px 78px; }}
    .panel {{
      background:var(--card);
      border:1px solid var(--line);
      border-radius:14px;
      padding:12px;
      margin-bottom:10px;
      box-shadow:0 2px 8px rgba(17,34,68,.04);
    }}
    h1 {{ margin:0; font-size:1.3rem; }}
    h2 {{ margin:0 0 8px; font-size:1.06rem; }}
    .sub {{ margin:6px 0 0; color:var(--muted); font-size:.92rem; }}
    .meta-grid {{
      margin-top:10px;
      display:grid;
      gap:8px;
      grid-template-columns:repeat(2,minmax(0,1fr));
    }}
    .meta {{
      border:1px solid var(--line);
      border-radius:10px;
      padding:8px;
      background:#fcfdff;
    }}
    .meta .k {{ display:block; color:var(--muted); font-size:.76rem; }}
    .meta .v {{ display:block; font-weight:700; margin-top:2px; font-size:.92rem; }}
    .summary-grid {{
      margin-top:8px;
      display:grid;
      gap:8px;
      grid-template-columns:repeat(2,minmax(0,1fr));
    }}
    .summary-box {{
      border:1px solid var(--line);
      border-radius:10px;
      padding:8px;
      background:#fbfdff;
    }}
    .summary-box .k {{ color:var(--muted); font-size:.76rem; display:block; }}
    .summary-box .v {{ font-size:.96rem; font-weight:700; display:block; margin-top:2px; }}
    .legend-row {{
      margin-top:8px;
      display:flex;
      flex-wrap:wrap;
      gap:6px;
    }}
    .timeline-wrap {{
      margin-top:10px;
      overflow:auto;
      border:1px solid var(--line);
      border-radius:10px;
      background:#fff;
    }}
    .timeline-desktop {{ display:none; }}
    .timeline-mobile {{
      margin-top:10px;
      display:grid;
      gap:8px;
    }}
    .timeline-card-mobile {{
      border:1px solid var(--line);
      border-radius:10px;
      background:#fff;
      padding:9px;
    }}
    .timeline-card-top {{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:8px;
      margin-bottom:5px;
    }}
    .timeline-card-line {{
      display:flex;
      justify-content:space-between;
      gap:8px;
      font-size:.82rem;
      margin-bottom:2px;
    }}
    .timeline-card-line span:first-child {{
      color:var(--muted);
      white-space:nowrap;
      margin-right:8px;
    }}
    .timeline-card-line span:last-child {{
      text-align:right;
    }}
    .timeline-card-badge {{
      margin-top:5px;
    }}
    .timeline-table {{
      width:100%;
      min-width:920px;
      border-collapse:collapse;
      font-size:.82rem;
    }}
    .timeline-table th, .timeline-table td {{
      border-bottom:1px solid var(--line);
      padding:7px 8px;
      text-align:left;
      vertical-align:top;
    }}
    .timeline-table th {{
      background:#eef3ff;
      color:#30486e;
      position:sticky;
      top:0;
      z-index:1;
      white-space:nowrap;
    }}
    .timeline-table td a {{
      color:var(--accent);
      text-decoration:none;
      font-weight:600;
    }}
    .switch-badge {{
      display:inline-block;
      border:1px solid #d8d1b0;
      background:#fff6d9;
      color:#725d18;
      border-radius:999px;
      padding:1px 7px;
      font-size:.72rem;
      font-weight:700;
      letter-spacing:.02em;
    }}
    .car-chip {{
      display:inline-block;
      border-radius:999px;
      padding:2px 8px;
      font-size:.74rem;
      font-weight:700;
      border:1px solid #ccd5ea;
      background:#f5f7fc;
      color:#2f466f;
      white-space:nowrap;
    }}
    .car-chip.car-1 {{ background:#e8f2ff; border-color:#b8d2ff; color:#1f4c9a; }}
    .car-chip.car-2 {{ background:#ebf9f1; border-color:#bde6ce; color:#1b6b46; }}
    .car-chip.car-3 {{ background:#fff4e7; border-color:#f3d2a6; color:#855525; }}
    .car-chip.car-4 {{ background:#f9eef9; border-color:#e5c6e5; color:#6f3f6f; }}
    .car-chip.car-na {{ background:#f2f4f8; border-color:#d8deea; color:#5f6d83; }}
    .timeline-row.car-1 td {{ background:#f7fbff; }}
    .timeline-row.car-2 td {{ background:#f7fdf9; }}
    .timeline-row.car-3 td {{ background:#fffaf5; }}
    .timeline-row.car-4 td {{ background:#fcf8fc; }}
    .timeline-row.car-na td {{ background:#fafbfc; }}
    .timeline-row.switch-row td {{
      border-top:2px solid #c8a85d;
    }}
    .tab-nav {{
      display:flex;
      gap:8px;
      overflow-x:auto;
      padding:4px;
      border:1px solid var(--line);
      border-radius:12px;
      background:#f8faff;
    }}
    .tab-nav.top {{ margin-top:12px; }}
    .tab-btn {{
      border:1px solid #c9d7f3;
      background:var(--tab);
      color:#24426f;
      border-radius:10px;
      padding:7px 11px;
      font-size:.85rem;
      white-space:nowrap;
      font-weight:700;
      cursor:pointer;
    }}
    .tab-btn.is-active {{
      background:var(--tab-active);
      color:var(--tab-active-ink);
      border-color:var(--tab-active);
    }}
    .tab-page {{ display:none; }}
    .tab-page.is-active {{ display:block; }}
    .runner-nav {{
      display:flex;
      gap:7px;
      overflow-x:auto;
      padding-bottom:2px;
    }}
    .runner-pill {{
      display:inline-block;
      white-space:nowrap;
      text-decoration:none;
      color:var(--accent);
      border:1px solid #b8caef;
      background:var(--accent-soft);
      border-radius:999px;
      padding:5px 10px;
      font-size:.86rem;
      font-weight:600;
    }}
    .runner-kpis {{
      display:flex;
      flex-wrap:wrap;
      gap:6px;
      margin-bottom:10px;
    }}
    .kpi {{
      border:1px solid #c9d7f3;
      background:#f2f6ff;
      color:#28416e;
      border-radius:999px;
      padding:3px 8px;
      font-size:.8rem;
      white-space:nowrap;
    }}
    .seg-list {{ display:grid; gap:8px; }}
    .seg-card {{
      border:1px solid var(--line);
      border-radius:12px;
      padding:10px;
      background:#ffffff;
    }}
    .seg-top {{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:8px;
      margin-bottom:5px;
    }}
    .seg-id {{ font-weight:700; font-size:.92rem; }}
    .seg-km {{ font-weight:700; color:#173f84; font-size:.92rem; }}
    .seg-route {{ font-weight:600; font-size:.9rem; line-height:1.3; }}
    .seg-time {{ margin-top:3px; color:var(--muted); font-size:.82rem; }}
    .seg-meta {{
      margin-top:6px;
      display:flex;
      flex-wrap:wrap;
      gap:6px 12px;
      color:#2f3b52;
      font-size:.8rem;
    }}
    .seg-sponsor {{
      margin-top:7px;
      display:inline-block;
      border:1px dashed #b4c4e7;
      border-radius:8px;
      padding:3px 7px;
      font-size:.78rem;
      color:#2c4677;
      background:#f4f8ff;
    }}
    .tag-row {{ margin-top:7px; }}
    .mini-tag {{
      display:inline-block;
      margin:0 5px 5px 0;
      border:1px solid #d0d9eb;
      border-radius:999px;
      padding:2px 7px;
      font-size:.74rem;
      color:#44506b;
      background:#f8fbff;
    }}
    .seg-nav {{
      margin-top:7px;
      font-size:.78rem;
      color:#334768;
      line-height:1.35;
    }}
    .seg-nav a {{
      color:#1f4c9a;
      text-decoration:none;
      font-weight:600;
    }}
    .seg-info {{ margin:7px 0 0; line-height:1.45; font-size:.87rem; }}
    .switch-grid {{ display:grid; gap:8px; }}
    .switch-card {{
      border:1px solid var(--line);
      border-radius:10px;
      padding:9px;
      background:#fff;
    }}
    .switch-title {{ font-weight:700; font-size:.9rem; margin-bottom:5px; }}
    .switch-line {{
      display:flex;
      justify-content:space-between;
      gap:8px;
      font-size:.82rem;
      margin-bottom:2px;
    }}
    .switch-line span {{ color:var(--muted); }}
    .switch-note {{
      margin-top:4px;
      font-size:.76rem;
      color:#3b5382;
    }}
    .switch-nav {{
      margin-top:6px;
      font-size:.76rem;
      color:#334768;
      line-height:1.35;
    }}
    .switch-nav a {{
      color:#1f4c9a;
      text-decoration:none;
      font-weight:600;
    }}
    .foot {{ color:var(--muted); font-size:.76rem; }}
    .mobile-tabs {{
      position:fixed;
      left:0;
      right:0;
      bottom:0;
      padding:8px 10px calc(8px + env(safe-area-inset-bottom));
      background:rgba(244,247,251,.92);
      backdrop-filter:blur(8px);
      border-top:1px solid #d5dced;
      z-index:12;
    }}
    .mobile-tabs .tab-nav {{ border-radius:14px; }}
    @media (min-width: 840px) {{
      main {{ padding:18px 16px 40px; }}
      .panel {{ padding:14px; margin-bottom:12px; }}
      .meta-grid {{ grid-template-columns:repeat(6,minmax(0,1fr)); }}
      .summary-grid {{ grid-template-columns:repeat(5,minmax(0,1fr)); }}
      .switch-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .timeline-desktop {{ display:block; }}
      .timeline-mobile {{ display:none; }}
      .mobile-tabs {{ display:none; }}
    }}
  </style>
</head>
<body>
<main>
  <section class=\"panel\">
    <h1>{html.escape(title)}</h1>
    <p class=\"sub\">Csapat: {html.escape(team_name)} | Státusz: {status}</p>
    <div class=\"meta-grid\">
      <div class=\"meta\"><span class=\"k\">Rajt</span><span class=\"v\">{start_dt}</span></div>
      <div class=\"meta\"><span class=\"k\">Befutás</span><span class=\"v\">{finish_dt}</span></div>
      <div class=\"meta\"><span class=\"k\">Összidő</span><span class=\"v\">{total_duration}</span></div>
      <div class=\"meta\"><span class=\"k\">Futók</span><span class=\"v\">{len(runner_order)} fő</span></div>
      <div class=\"meta\"><span class=\"k\">Szakaszok</span><span class=\"v\">{len(segments_sorted)} db</span></div>
      <div class=\"meta\"><span class=\"k\">Koordináták</span><span class=\"v\">{coord_count}/{len(segments_sorted)}</span></div>
    </div>
    <nav class=\"tab-nav top\" aria-label=\"Oldalfülek\">
      <button type=\"button\" class=\"tab-btn is-active\" data-tab=\"overview\">Áttekintés</button>
      <button type=\"button\" class=\"tab-btn\" data-tab=\"switches\">Váltások</button>
      <button type=\"button\" class=\"tab-btn\" data-tab=\"runners\">Futók</button>
    </nav>
  </section>

  <section class=\"tab-page is-active\" id=\"tab-overview\" data-tab-page=\"overview\">
    <section class=\"panel\">
      <h2>Gyors Összefoglaló</h2>
      <div class=\"summary-grid\">
        <div class=\"summary-box\"><span class=\"k\">Össztáv</span><span class=\"v\">{_fmt_km(total_km_all)} km</span></div>
        <div class=\"summary-box\"><span class=\"k\">Nappali táv</span><span class=\"v\">{_fmt_km(day_km)} km</span></div>
        <div class=\"summary-box\"><span class=\"k\">Éjszakai táv</span><span class=\"v\">{_fmt_km(night_km)} km</span></div>
        <div class=\"summary-box\"><span class=\"k\">Váltások száma</span><span class=\"v\">{len(blocks_sorted)} db</span></div>
        <div class=\"summary-box\"><span class=\"k\">Bringás lefedettség</span><span class=\"v\">{len(biker_segments)}/{len(segments_sorted)} szakasz, {_fmt_km(biker_km)} km</span></div>
      </div>
      <p class=\"sub\">Bringások: {html.escape(', '.join(unique_bikers)) if unique_bikers else 'nincs megadva'}</p>
      <div class=\"legend-row\">{car_legend}</div>
      <div class=\"timeline-wrap timeline-desktop\">
        <table class=\"timeline-table\">
          <thead>
            <tr>
              <th>#</th>
              <th>Autó</th>
              <th>Futó</th>
              <th>Kerékpáros</th>
              <th>Jel</th>
              <th>Szakasz km</th>
              <th>Eddigi táv (km)</th>
              <th>Útvonal</th>
              <th>Idő</th>
            </tr>
          </thead>
          <tbody>{''.join(timeline_rows)}</tbody>
        </table>
      </div>
      <div class=\"timeline-mobile\">{''.join(timeline_mobile_cards)}</div>
      <p class=\"foot\">A részletes, futónkénti bontás a Futók fülön található.</p>
    </section>
  </section>

  <section class=\"tab-page\" id=\"tab-switches\" data-tab-page=\"switches\">
    <section class=\"panel\">
      <h2>Autós Váltási Lista</h2>
      <p class=\"sub\">A váltások blokkhatáron történnek. A \"Hely\" a következő futó rajtpontja.</p>
      <div class=\"switch-grid\">{''.join(switch_cards)}</div>
    </section>
  </section>

  <section class=\"tab-page\" id=\"tab-runners\" data-tab-page=\"runners\">
    <section class=\"panel\">
      <h2>Futók Gyors Elérése</h2>
      <div class=\"runner-nav\">{''.join(runner_nav)}</div>
    </section>
    {''.join(runner_sections)}
  </section>

  <section class=\"panel\">
    <div class=\"foot\">Tipp: a tabokkal lépdelve külön nézetben látod a futókat és a váltásokat.</div>
    <div class=\"foot\">Koordináta forrás: UB hivatalos szakaszoldal.</div>
  </section>
</main>

<div class=\"mobile-tabs\" aria-hidden=\"false\">
  <nav class=\"tab-nav\" aria-label=\"Mobil oldalfülek\">
    <button type=\"button\" class=\"tab-btn is-active\" data-tab=\"overview\">Áttekintés</button>
    <button type=\"button\" class=\"tab-btn\" data-tab=\"switches\">Váltások</button>
    <button type=\"button\" class=\"tab-btn\" data-tab=\"runners\">Futók</button>
  </nav>
</div>

<script>
  (function() {{
    const pages = Array.from(document.querySelectorAll('[data-tab-page]'));
    const buttons = Array.from(document.querySelectorAll('.tab-btn[data-tab]'));

    function activate(tabId, updateHash) {{
      pages.forEach((el) => {{
        const on = el.getAttribute('data-tab-page') === tabId;
        el.classList.toggle('is-active', on);
      }});
      buttons.forEach((btn) => {{
        const on = btn.getAttribute('data-tab') === tabId;
        btn.classList.toggle('is-active', on);
      }});
      if (updateHash) {{
        history.replaceState(null, '', '#tab-' + tabId);
      }}
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }}

    buttons.forEach((btn) => {{
      btn.addEventListener('click', () => activate(btn.getAttribute('data-tab'), true));
    }});

    document.querySelectorAll('[data-open-tab=\"runners\"]').forEach((a) => {{
      a.addEventListener('click', (ev) => {{
        const href = a.getAttribute('href') || '';
        if (!href.startsWith('#runner-')) {{
          return;
        }}
        ev.preventDefault();
        activate('runners', false);
        const target = document.querySelector(href);
        if (target) {{
          setTimeout(() => target.scrollIntoView({{ behavior: 'smooth', block: 'start' }}), 80);
        }}
      }});
    }});

    const hash = window.location.hash || '';
    if (hash.startsWith('#tab-')) {{
      const tab = hash.replace('#tab-', '');
      if (['overview', 'switches', 'runners'].includes(tab)) {{
        activate(tab, false);
      }}
    }} else if (hash.startsWith('#runner-')) {{
      activate('runners', false);
      const target = document.querySelector(hash);
      if (target) {{
        setTimeout(() => target.scrollIntoView({{ behavior: 'smooth', block: 'start' }}), 80);
      }}
    }}
  }})();
</script>
</body>
</html>
"""


def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build static UB HTML report from final.csv.")
    parser.add_argument("--final-csv", required=True, help="Path to final.csv")
    parser.add_argument("--output", required=True, help="Output HTML path")
    parser.add_argument("--title", default="UB Futóbeosztás", help="Report title")
    parser.add_argument("--team-name", default="Csiga Csillagok", help="Team name displayed in report")
    parser.add_argument("--race-date", default="2026-04-25", help="Race start date (YYYY-MM-DD)")
    parser.add_argument("--race-start", default="", help="Override race start HH:MM:SS")
    parser.add_argument(
        "--stage-meta",
        default="data/stage_metadata.json",
        help="Optional stage metadata JSON with coordinates",
    )
    parser.add_argument(
        "--runner-meta",
        default="data/runner_plan_input.json",
        help="Optional optimizer input JSON to colorize runners by car_id",
    )
    args = parser.parse_args(argv)

    race_date = datetime.strptime(args.race_date, "%Y-%m-%d").date()
    final_data = _parse_final_csv(Path(args.final_csv))
    if args.race_start:
        start_t = datetime.strptime(args.race_start, "%H:%M:%S").time()
    elif final_data.get("start_time") is not None:
        start_t = final_data["start_time"]
    else:
        start_t = datetime.strptime("12:15:00", "%H:%M:%S").time()
    stage_meta = _load_stage_metadata(Path(args.stage_meta))
    runner_car_map = _load_runner_car_map(Path(args.runner_meta))
    report = _report_from_final_csv(final_data, race_date=race_date, race_start_time=start_t)
    output_html = _render_html(
        report,
        args.title,
        args.team_name,
        final_csv_snapshot=final_data,
        stage_meta=stage_meta,
        runner_car_map=runner_car_map,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(output_html, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
