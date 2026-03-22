#!/usr/bin/env python3
"""Build static HTML report for UB planning from final.csv."""

from __future__ import annotations

import argparse
import csv
import html
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
            if not (1 <= seg_id <= 56):
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
) -> str:
    status = html.escape(str(report.get("status", "-")))
    objective = html.escape(str(report.get("objective", "-")))
    start_dt = report["start_dt"].strftime("%Y-%m-%d %H:%M")
    finish_dt = report["finish_dt"].strftime("%Y-%m-%d %H:%M")
    total_duration = _format_duration(float(report.get("duration_min", 0.0)))

    runner_rows_html: List[str] = []
    for r in report["runner_rows"]:
        ratio = "-" if r.get("first_block_ratio") is None else f"{100.0 * float(r['first_block_ratio']):.1f}%"
        rest_gap = "-" if r.get("rest_gap_segments") is None else str(r["rest_gap_segments"])
        runner_rows_html.append(
            "<tr>"
            f"<td>{html.escape(r['name'])}</td>"
            f"<td>{html.escape(str(r.get('car_id', '-') or '-'))}</td>"
            f"<td class='num'>{r['target_km']:.1f}</td>"
            f"<td class='num'>{r['assigned_km']:.1f}</td>"
            f"<td class='num'>{r['overflow_km']:.1f}</td>"
            f"<td class='num'>{r['underfill_km']:.1f}</td>"
            f"<td class='num'>{_format_duration(float(r['duration_min']))}</td>"
            f"<td class='num'>{float(r['dark_min']):.1f}</td>"
            f"<td class='num'>{float(r['dark_pct']):.1f}%</td>"
            f"<td class='num'>{ratio}</td>"
            f"<td class='num'>{rest_gap}</td>"
            f"<td class='num'>{int(r['block_count'])}</td>"
            "</tr>"
        )

    block_rows_html: List[str] = []
    for idx, b in enumerate(report["blocks"], start=1):
        dark_flag = "sötét" if float(b.get("dark_min", 0.0)) > 0 else "világos"
        block_rows_html.append(
            "<tr>"
            f"<td class='num'>{idx}</td>"
            f"<td>{html.escape(b['runner'])}</td>"
            f"<td class='num'>{b['start_seg']}-{b['end_seg']}</td>"
            f"<td class='num'>{float(b['km']):.1f}</td>"
            f"<td>{b['start'].strftime('%m-%d %H:%M')}</td>"
            f"<td>{b['end'].strftime('%m-%d %H:%M')}</td>"
            f"<td class='num'>{_format_duration(float(b['duration_min']))}</td>"
            f"<td class='num'>{float(b['dark_min']):.1f}</td>"
            f"<td>{dark_flag}</td>"
            f"<td>{html.escape(str(b.get('stage_from', '')))} -> {html.escape(str(b.get('stage_to', '')))}</td>"
            "</tr>"
        )

    segment_rows_html: List[str] = []
    for s in report["segments"]:
        pace_text = s.get("pace_raw") if s.get("pace_raw") else (f"{float(s.get('pace', 0.0)):.2f}" if float(s.get('pace', 0.0)) > 0 else "-")
        stage_text = s.get("stage") or f"{s.get('stage_from', '')} -> {s.get('stage_to', '')}"
        segment_rows_html.append(
            "<tr>"
            f"<td class='num'>{s['seg_id']}</td>"
            f"<td>{html.escape(s['runner'])}</td>"
            f"<td>{html.escape(str(s.get('biker', '')))}</td>"
            f"<td class='num'>{float(s['km']):.1f}</td>"
            f"<td class='num'>{html.escape(str(pace_text))}</td>"
            f"<td>{s['start'].strftime('%m-%d %H:%M:%S')}</td>"
            f"<td>{s['end'].strftime('%m-%d %H:%M:%S')}</td>"
            f"<td class='num'>{float(s['duration_min']):.1f}</td>"
            f"<td class='num'>{float(s['dark_min']):.1f}</td>"
            f"<td>{html.escape(str(stage_text))}</td>"
            "</tr>"
        )

    segments_sorted = sorted(report["segments"], key=lambda x: int(x["seg_id"]))
    blocks_sorted = sorted(report["blocks"], key=lambda x: int(x["start_seg"]))
    runner_first_seg: Dict[str, int] = {}
    for s in segments_sorted:
        name = s["runner"]
        if name not in runner_first_seg:
            runner_first_seg[name] = int(s["seg_id"])
    runner_order = [name for name, _ in sorted(runner_first_seg.items(), key=lambda kv: kv[1])]

    segments_by_runner: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in segments_sorted:
        segments_by_runner[s["runner"]].append(s)
    blocks_by_runner: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in blocks_sorted:
        blocks_by_runner[b["runner"]].append(b)

    runner_breakdown_cards: List[str] = []
    for runner_name in runner_order:
        r_segments = segments_by_runner.get(runner_name, [])
        r_blocks = blocks_by_runner.get(runner_name, [])
        total_km = sum(float(s.get("km", 0.0)) for s in r_segments)
        total_min = sum(float(s.get("duration_min", 0.0)) for s in r_segments)
        dark_min = sum(float(s.get("dark_min", 0.0)) for s in r_segments)

        block_rows: List[str] = []
        for b in r_blocks:
            dark_flag = "sötét" if float(b.get("dark_min", 0.0)) > 0 else "világos"
            block_rows.append(
                "<tr>"
                f"<td class='num'>{int(b['start_seg'])}-{int(b['end_seg'])}</td>"
                f"<td class='num'>{float(b.get('km', 0.0)):.1f}</td>"
                f"<td>{html.escape(str(b.get('stage_from', '')))} -> {html.escape(str(b.get('stage_to', '')))}</td>"
                f"<td>{b['start'].strftime('%m-%d %H:%M')}</td>"
                f"<td>{b['end'].strftime('%m-%d %H:%M')}</td>"
                f"<td class='num'>{_format_duration(float(b.get('duration_min', 0.0)))}</td>"
                f"<td>{dark_flag}</td>"
                "</tr>"
            )

        runner_breakdown_cards.append(
            "<article class='runner-card'>"
            "<div class='runner-head'>"
            f"<h3>{html.escape(runner_name)}</h3>"
            "<div class='runner-metrics'>"
            f"<span class='pill'>{len(r_blocks)} blokk</span>"
            f"<span class='pill'>{len(r_segments)} szakasz</span>"
            f"<span class='pill'>{total_km:.1f} km</span>"
            f"<span class='pill'>{_format_duration(total_min)}</span>"
            f"<span class='pill'>Sötét: {dark_min:.1f} perc</span>"
            "</div>"
            "</div>"
            "<div class='table-wrap runner-table'>"
            "<table>"
            "<thead><tr>"
            "<th class='num'>Szegmens</th><th class='num'>Km</th><th>Útvonal</th><th>Indulás</th><th>Érkezés</th><th class='num'>Időtartam</th><th>Fény</th>"
            "</tr></thead>"
            f"<tbody>{''.join(block_rows)}</tbody>"
            "</table>"
            "</div>"
            "</article>"
        )

    final_section = ""
    if final_csv_snapshot:
        f_runner_rows = []
        for r in final_csv_snapshot.get("runner_rows", []):
            target = "-" if r.get("target_km") is None else f"{float(r['target_km']):.1f}"
            actual = "-" if r.get("actual_km") is None else f"{float(r['actual_km']):.1f}"
            f_runner_rows.append(
                "<tr>"
                f"<td>{html.escape(str(r.get('name', '')))}</td>"
                f"<td>{html.escape(str(r.get('pace', '')))}</td>"
                f"<td class='num'>{target}</td>"
                f"<td class='num'>{actual}</td>"
                "</tr>"
            )
        f_seg_rows = []
        for s in final_csv_snapshot.get("segment_rows", []):
            f_seg_rows.append(
                "<tr>"
                f"<td class='num'>{s.get('seg_id', '')}</td>"
                f"<td>{html.escape(str(s.get('runner', '')))}</td>"
                f"<td>{html.escape(str(s.get('biker', '')))}</td>"
                f"<td class='num'>{float(s.get('km', 0.0)):.1f}</td>"
                f"<td>{html.escape(str(s.get('pace', '')))}</td>"
                f"<td>{html.escape(str(s.get('run_time', '')))}</td>"
                f"<td>{html.escape(str(s.get('arrival', '')))}</td>"
                f"<td>{html.escape(str(s.get('day', '')))}</td>"
                f"<td>{html.escape(str(s.get('stage', '')))}</td>"
                "</tr>"
            )

        final_section = f"""
  <section class=\"panel\"> 
    <h2>final.csv Kivonat</h2>
    <div class=\"table-wrap\" style=\"margin-top:8px;\"> 
      <table>
        <thead><tr><th>Futó</th><th>Tempó</th><th class=\"num\">Vállalt km</th><th class=\"num\">Futó távja km</th></tr></thead>
        <tbody>{''.join(f_runner_rows)}</tbody>
      </table>
    </div>
    <div class=\"table-wrap\" style=\"margin-top:10px;\"> 
      <table>
        <thead><tr><th class=\"num\">Szakasz</th><th>Futó</th><th>Kerékpáros</th><th class=\"num\">Km</th><th>Tempó</th><th>Futásidő</th><th>Érkezés</th><th>Napszak</th><th>Útvonal</th></tr></thead>
        <tbody>{''.join(f_seg_rows)}</tbody>
      </table>
    </div>
  </section>
"""

    return f"""<!doctype html>
<html lang=\"hu\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --bg:#f6f8fb; --card:#fff; --line:#d5dbe5; --ink:#182133; --muted:#5e6a80; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:"IBM Plex Sans","Segoe UI",Roboto,Arial,sans-serif; color:var(--ink);
      background: radial-gradient(1200px 500px at -10% -20%, #ddeafe 0%, rgba(221,234,254,0) 60%),
                  radial-gradient(1000px 450px at 110% -10%, #ffe7cd 0%, rgba(255,231,205,0) 55%), var(--bg); }}
    main {{ max-width:1200px; margin:0 auto; padding:20px 14px 42px; }}
    .panel {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; margin-bottom:14px; }}
    h1 {{ margin:0 0 8px; font-size:1.45rem; }} h2 {{ margin:4px 0 10px; font-size:1.1rem; }}
    .team {{ margin:0 0 8px; color:var(--muted); font-weight:600; }}
    .meta {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:8px; margin-top:8px; }}
    .kv {{ border:1px solid var(--line); border-radius:10px; padding:8px 10px; background:#fcfdff; }}
    .k {{ color:var(--muted); font-size:.8rem; }} .v {{ font-weight:700; font-size:1.02rem; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:10px; }}
    table {{ width:100%; border-collapse:collapse; min-width:900px; background:#fff; }}
    th,td {{ border-bottom:1px solid var(--line); padding:7px 8px; white-space:nowrap; font-size:.9rem; }}
    th {{ position:sticky; top:0; z-index:1; background:#f2f6fd; color:#233352; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .runner-grid {{ display:grid; grid-template-columns:1fr; gap:12px; }}
    .runner-card {{ border:1px solid var(--line); border-radius:12px; padding:10px; background:#fbfdff; }}
    .runner-head {{ display:flex; gap:8px; align-items:center; justify-content:space-between; flex-wrap:wrap; margin-bottom:8px; }}
    .runner-head h3 {{ margin:0; font-size:1rem; }}
    .runner-metrics {{ display:flex; gap:6px; flex-wrap:wrap; }}
    .pill {{ border:1px solid #c8d7ee; border-radius:999px; padding:3px 9px; font-size:.8rem; background:#eef4ff; color:#29416c; }}
    .runner-table table {{ min-width:740px; }}
    @media (max-width: 720px) {{
      main {{ padding:14px 10px 28px; }}
      .panel {{ padding:10px; border-radius:12px; }}
      th,td {{ padding:6px 6px; font-size:.82rem; }}
    }}
  </style>
</head>
<body>
<main>
  <section class=\"panel\"> 
    <h1>{html.escape(title)}</h1>
    <p class=\"team\">Csapat: {html.escape(team_name)}</p>
    <div class=\"meta\">
      <div class=\"kv\"><div class=\"k\">Státusz</div><div class=\"v\">{status}</div></div>
      <div class=\"kv\"><div class=\"k\">Célfüggvény</div><div class=\"v\">{objective}</div></div>
      <div class=\"kv\"><div class=\"k\">Rajt</div><div class=\"v\">{start_dt}</div></div>
      <div class=\"kv\"><div class=\"k\">Befutás</div><div class=\"v\">{finish_dt}</div></div>
      <div class=\"kv\"><div class=\"k\">Összidő</div><div class=\"v\">{total_duration}</div></div>
    </div>
  </section>

  <section class=\"panel\"> 
    <h2>Futó Összefoglaló</h2>
    <div class=\"table-wrap\"><table>
      <thead><tr>
        <th>Futó</th><th>Autó</th><th class=\"num\">Cél km</th><th class=\"num\">Kiosztott km</th>
        <th class=\"num\">Túllépés</th><th class=\"num\">Hiány</th><th class=\"num\">Futásidő</th>
        <th class=\"num\">Sötét perc</th><th class=\"num\">Sötét %</th><th class=\"num\">1. blokk</th>
        <th class=\"num\">Pihenő (szegmens)</th><th class=\"num\">Blokkok</th>
      </tr></thead>
      <tbody>{''.join(runner_rows_html)}</tbody>
    </table></div>
  </section>

  <section class=\"panel\"> 
    <h2>Blokk Idővonal</h2>
    <div class=\"table-wrap\"><table>
      <thead><tr>
        <th class=\"num\">#</th><th>Futó</th><th class=\"num\">Szakaszok</th><th class=\"num\">Km</th>
        <th>Indulás</th><th>Érkezés</th><th class=\"num\">Időtartam</th><th class=\"num\">Sötét perc</th><th>Fényviszony</th><th>Útvonal</th>
      </tr></thead>
      <tbody>{''.join(block_rows_html)}</tbody>
    </table></div>
  </section>

  <section class=\"panel\"> 
    <h2>Szakasz Idővonal</h2>
    <div class=\"table-wrap\"><table>
      <thead><tr>
        <th class=\"num\">Szakasz</th><th>Futó</th><th>Kerékpáros</th><th class=\"num\">Km</th><th class=\"num\">Tempó perc/km</th>
        <th>Indulás</th><th>Érkezés</th><th class=\"num\">Időtartam perc</th><th class=\"num\">Sötét perc</th><th>Útvonal</th>
      </tr></thead>
      <tbody>{''.join(segment_rows_html)}</tbody>
    </table></div>
  </section>

  <section class=\"panel\">
    <h2>Futónkénti Bontás (Sorrendben)</h2>
    <div class=\"runner-grid\">{''.join(runner_breakdown_cards)}</div>
  </section>
{final_section}
</main>
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
    args = parser.parse_args(argv)

    race_date = datetime.strptime(args.race_date, "%Y-%m-%d").date()
    final_data = _parse_final_csv(Path(args.final_csv))
    if args.race_start:
        start_t = datetime.strptime(args.race_start, "%H:%M:%S").time()
    elif final_data.get("start_time") is not None:
        start_t = final_data["start_time"]
    else:
        start_t = datetime.strptime("12:15:00", "%H:%M:%S").time()
    report = _report_from_final_csv(final_data, race_date=race_date, race_start_time=start_t)
    output_html = _render_html(report, args.title, args.team_name, final_csv_snapshot=final_data)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(output_html, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
