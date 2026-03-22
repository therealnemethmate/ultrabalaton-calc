#!/usr/bin/env python3
"""Build optimizer-vs-final static HTML comparison from final.csv + optimizer JSON."""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


class CompareError(ValueError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_hu_float(raw: str) -> float:
    s = _clean(raw).replace(" ", "").replace(",", ".")
    if not s:
        raise CompareError("Missing numeric value.")
    return float(s)


def _parse_final_csv(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = [list(r) for r in csv.reader(f)]

    header_idx = -1
    km_col = -1
    from_col = -1
    to_col = -1
    runner_col = -1
    biker_col = -1
    stage_name_col = -1
    arrival_col = -1
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
            elif c == "SZAKASZ NÉV":
                stage_name_col = j
            elif c == "VÁLTÓPONTHOZ ÉRKEZÉS IDEJE":
                arrival_col = j
            elif c == "Napszak":
                day_col = j
        if header_idx >= 0 and km_col >= 0 and runner_col >= 0:
            break

    if header_idx < 0 or km_col <= 0:
        raise CompareError("Could not locate segment table in final.csv.")

    seg_col = km_col - 1
    segments: List[Dict[str, Any]] = []
    final_totals: Dict[str, float] = defaultdict(float)
    for row in rows[header_idx + 1 :]:
        if seg_col >= len(row):
            continue
        seg_raw = _clean(row[seg_col])
        if not seg_raw.isdigit():
            continue
        seg_id = int(seg_raw)
        km_raw = _clean(row[km_col]) if km_col < len(row) else ""
        try:
            km = _parse_hu_float(km_raw)
        except Exception:
            continue

        runner = _clean(row[runner_col]) if runner_col < len(row) else ""
        biker = _clean(row[biker_col]) if biker_col >= 0 and biker_col < len(row) else ""
        stage_from = _clean(row[from_col]) if from_col >= 0 and from_col < len(row) else ""
        stage_to = _clean(row[to_col]) if to_col >= 0 and to_col < len(row) else ""
        stage_name = _clean(row[stage_name_col]) if stage_name_col >= 0 and stage_name_col < len(row) else ""
        arrival = _clean(row[arrival_col]) if arrival_col >= 0 and arrival_col < len(row) else ""
        day = _clean(row[day_col]) if day_col >= 0 and day_col < len(row) else ""

        segments.append(
            {
                "seg_id": seg_id,
                "km": km,
                "from": stage_from,
                "to": stage_to,
                "stage_name": stage_name,
                "runner_final": runner,
                "biker": biker,
                "arrival": arrival,
                "day": day,
            }
        )
        if runner:
            final_totals[runner] += km

    segments.sort(key=lambda s: s["seg_id"])
    return segments, dict(final_totals)


def _parse_optimizer(path: Path) -> Tuple[Dict[int, str], Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    seg_owner_raw = data.get("segment_owner")
    if not isinstance(seg_owner_raw, dict):
        raise CompareError("optimizer result missing 'segment_owner' object.")

    seg_owner: Dict[int, str] = {}
    for k, v in seg_owner_raw.items():
        try:
            seg_id = int(str(k))
        except ValueError as exc:
            raise CompareError(f"Invalid segment id in optimizer output: {k!r}") from exc
        seg_owner[seg_id] = _clean(v)

    meta = {
        "status": _clean(data.get("status")),
        "status_code": data.get("status_code"),
        "objective": data.get("objective"),
    }
    return seg_owner, meta


def _format_km(v: float) -> str:
    return f"{v:.1f}"


def _render_html(
    *,
    title: str,
    segments: List[Dict[str, Any]],
    final_totals: Dict[str, float],
    optimizer_owner: Dict[int, str],
    optimizer_meta: Dict[str, Any],
) -> str:
    optimizer_totals: Dict[str, float] = defaultdict(float)
    changed_rows = 0

    row_html: List[str] = []
    for s in segments:
        seg_id = s["seg_id"]
        runner_final = s["runner_final"]
        runner_opt = optimizer_owner.get(seg_id, "")
        if runner_opt:
            optimizer_totals[runner_opt] += float(s["km"])
        is_changed = bool(runner_final and runner_opt and runner_final != runner_opt)
        if is_changed:
            changed_rows += 1

        css = "changed" if is_changed else ""
        diff = "igen" if is_changed else "nem"
        stage = f"{s['from']} -> {s['to']}"
        stage_name = s["stage_name"] or "-"
        biker = s["biker"] or "-"
        arrival = s["arrival"] or "-"
        day = s["day"] or "-"
        row_html.append(
            f"<tr class='{css}'>"
            f"<td>{seg_id}</td>"
            f"<td>{_format_km(float(s['km']))}</td>"
            f"<td>{html.escape(stage)}</td>"
            f"<td>{html.escape(stage_name)}</td>"
            f"<td>{html.escape(runner_final or '-')}</td>"
            f"<td>{html.escape(runner_opt or '-')}</td>"
            f"<td>{html.escape(biker)}</td>"
            f"<td>{html.escape(arrival)}</td>"
            f"<td>{html.escape(day)}</td>"
            f"<td>{diff}</td>"
            f"</tr>"
        )

    all_runners = sorted(set(final_totals.keys()) | set(optimizer_totals.keys()))
    total_rows: List[str] = []
    for name in all_runners:
        final_km = final_totals.get(name, 0.0)
        opt_km = optimizer_totals.get(name, 0.0)
        delta = opt_km - final_km
        css = "changed" if abs(delta) > 1e-9 else ""
        total_rows.append(
            f"<tr class='{css}'>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{_format_km(final_km)}</td>"
            f"<td>{_format_km(opt_km)}</td>"
            f"<td>{delta:+.1f}</td>"
            f"</tr>"
        )

    total_km = sum(float(s["km"]) for s in segments)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="hu">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg:#f4f6fb;
      --panel:#ffffff;
      --ink:#1f2a44;
      --muted:#5a6b8a;
      --line:#d5dced;
      --accent:#0e7490;
      --warn:#fff1f1;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      font-family: 'Segoe UI', -apple-system, sans-serif;
      background:linear-gradient(180deg,#f8fbff 0%, #eef2fb 100%);
      color:var(--ink);
    }}
    .wrap {{ max-width:1200px; margin:0 auto; padding:16px; }}
    h1 {{ margin:0 0 8px; font-size:1.3rem; }}
    .sub {{ color:var(--muted); margin:0 0 14px; font-size:.95rem; }}
    .cards {{
      display:grid;
      gap:10px;
      grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
      margin-bottom:14px;
    }}
    .card {{
      background:var(--panel);
      border:1px solid var(--line);
      border-radius:12px;
      padding:10px 12px;
    }}
    .k {{ color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.04em; }}
    .v {{ font-size:1.12rem; font-weight:700; margin-top:2px; }}
    .panel {{
      background:var(--panel);
      border:1px solid var(--line);
      border-radius:12px;
      padding:10px;
      margin:0 0 12px;
    }}
    .panel h2 {{ margin:4px 2px 10px; font-size:1.03rem; }}
    .table-wrap {{
      overflow:auto;
      border:1px solid var(--line);
      border-radius:10px;
      background:#fff;
    }}
    table {{ width:100%; border-collapse:collapse; min-width:760px; }}
    th, td {{
      border-bottom:1px solid var(--line);
      padding:8px 9px;
      text-align:left;
      vertical-align:top;
      font-size:.9rem;
    }}
    th {{
      position:sticky;
      top:0;
      background:#f0f4fc;
      z-index:1;
      white-space:nowrap;
    }}
    tr.changed td {{ background:var(--warn); }}
    .note {{ color:var(--muted); font-size:.86rem; margin:8px 2px 2px; }}
    @media (max-width:740px) {{
      .wrap {{ padding:12px; }}
      h1 {{ font-size:1.14rem; }}
      th, td {{ padding:7px; font-size:.84rem; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>{html.escape(title)}</h1>
    <p class="sub">Összehasonlítás: jelenlegi final.csv kiosztás vs optimizer szegmens-tulajdonosok</p>
    <div class="cards">
      <article class="card"><div class="k">Szegmensek</div><div class="v">{len(segments)}</div></article>
      <article class="card"><div class="k">Össztáv</div><div class="v">{_format_km(total_km)} km</div></article>
      <article class="card"><div class="k">Eltérő futó</div><div class="v">{changed_rows}</div></article>
      <article class="card"><div class="k">Optimizer státusz</div><div class="v">{html.escape(str(optimizer_meta.get('status') or '-'))}</div></article>
      <article class="card"><div class="k">Objective</div><div class="v">{html.escape(str(optimizer_meta.get('objective')))}</div></article>
      <article class="card"><div class="k">Generálva</div><div class="v">{html.escape(generated_at)}</div></article>
    </div>

    <section class="panel">
      <h2>Futónkénti táv: final vs optimizer</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Futó</th><th>Final (km)</th><th>Optimizer (km)</th><th>Eltérés (km)</th>
            </tr>
          </thead>
          <tbody>
            {''.join(total_rows)}
          </tbody>
        </table>
      </div>
      <p class="note">Pozitív eltérés: optimizer többet adna az adott futónak.</p>
    </section>

    <section class="panel">
      <h2>Szegmens szintű összehasonlítás</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Km</th><th>Útvonal</th><th>Szakasz név</th><th>Final futó</th><th>Optimizer futó</th><th>Kerékpáros</th><th>Érkezés</th><th>Napszak</th><th>Eltérés</th>
            </tr>
          </thead>
          <tbody>
            {''.join(row_html)}
          </tbody>
        </table>
      </div>
      <p class="note">A piros sorok mutatják, ahol az optimizer más futót javasol a final.csv-hez képest.</p>
    </section>
  </main>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Build optimizer comparison HTML.")
    ap.add_argument("--final-csv", required=True, help="Path to final.csv")
    ap.add_argument("--optimizer-json", required=True, help="Path to optimizer output JSON")
    ap.add_argument("--output", required=True, help="Output HTML path")
    ap.add_argument("--title", default="UB26: Final vs Optimizer", help="HTML title")
    args = ap.parse_args()

    segments, final_totals = _parse_final_csv(Path(args.final_csv))
    optimizer_owner, optimizer_meta = _parse_optimizer(Path(args.optimizer_json))
    html_out = _render_html(
        title=args.title,
        segments=segments,
        final_totals=final_totals,
        optimizer_owner=optimizer_owner,
        optimizer_meta=optimizer_meta,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
