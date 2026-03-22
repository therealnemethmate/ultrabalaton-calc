# Runner Assignment Optimizer (CP-SAT)

This project implements a practical version of the optimizer from the shared plan:

- exact segment assignment
- contiguous block count limits per runner (`max_blocks` 1 or 2)
- distance-to-target objective
- optional overflow caps
- optional current-plan stability penalty
- optional same-car cohesion penalty (segment-index span)

## Projekt Célja (UB)

Az alkalmazás célja, hogy egy **UltraBalaton váltócsapat futóbeosztását optimalizálja**:

- ki melyik szakaszt fusson,
- ki hány blokkban fusson,
- hogyan legyen a sorrend és a blokkhatár,
- úgy, hogy a megadott korlátok teljesüljenek és a pihenőidők lehetőleg maximalizálódjanak.

## Igények És Feltételek (Összefoglaló)

### Kemény korlátok (must-have)

- Minden szakaszt pontosan 1 futó fut.
- Futónként a blokkszám a megadott `min_blocks..max_blocks` tartományban van.
- A blokkok mindig folytonosak (nincs szétdarabolt kiosztás).
- `min_blocks=1,max_blocks=1` esetén pontosan 1 blokk.
- `min_blocks=2,max_blocks=2` esetén pontosan 2 blokk.
- Opcionális minimális blokk-hossz: `settings.min_block_km` (pl. 4.0 km).
- Opcionális minimum pihenő 2x futóknál: `settings.min_rest_gap_segments`
  (vagy futó szinten: `runner.min_rest_gap_segments`).
- Opcionális sorrendkorlát 2-blokkosokra:
  `settings.enforce_second_round_order=true` esetén a második kör sorrendje megegyezik az első kör sorrendjével.
- Opcionális km felső korlát:
  globális (`settings.max_overflow_km`) vagy futó-szintű (`runner.max_overflow_km`).

### Puha célok (objective / súlyozott optimalizálás)

- Vállalt km-től eltérés minimalizálása (`overflow`, `underfill` súlyok).
- 2-blokkos futók pihenőrésének maximalizálása (`rest_gap` súly + `runner.rest_priority`).
- Aktuális tervhez való közelség (szakaszcsere büntetés, `change`).
- Azonos autóban lévők összetartása (span minimalizálás, `car_span`).

### UB-specifikus szabályok (aktuális beszélgetés alapján)

- 2x futók: `Dóri, Anna, Nóri, Lilla, Lackó, Bianka, Gábor`
- 1x futók: `Lajek, Peti, Levi, Regi`
- 1x futók nem bonthatók több blokkra.
- 2x futóknál az első blokk aránya az össztávból: minimum 40%, maximum 70%.
- 2x futóknál második kör sorrendje kövesse az elsőt.
- Pihenő maximalizálás minden 2x futónál.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python optimizer.py --input sample_input.json --pretty
```

Write to file:

```bash
python optimizer.py --input sample_input.json --output result.json --pretty
```

## Persistent Data Files

The extracted UB planning inputs are stored as versioned files under `data/`:

- `data/runner_plan_input.json`: optimizer input (segments, runners, constraints)
- `data/final.csv`: final plan table used for static HTML rendering

Run optimizer with persistent input:

```bash
python optimizer.py --input data/runner_plan_input.json --output result.json --pretty
```

## Build Static HTML Report

The publishing flow uses persistent `final.csv` input:

### Final-only mode (recommended)

```bash
python build_static_html.py \
  --final-csv data/final.csv \
  --output docs/index.html \
  --title "UB26 Futóbeosztás" \
  --team-name "Csiga Csillagok" \
  --race-date "2026-04-25"
```

The report includes:

- runner summary (target/assigned/overflow/underfill, runtime, dark minutes)
- block timeline with start/end timestamps
- full segment-level timeline

### Optimizer vs Final compare page

Generate an extra comparison page from optimizer output vs current `final.csv`:

```bash
python optimizer.py --input data/runner_plan_input.json --output data/optimizer_result.json --pretty

python build_optimizer_compare_html.py \
  --final-csv data/final.csv \
  --optimizer-json data/optimizer_result.json \
  --output docs/optimizer_compare.html \
  --title "UB26 összehasonlítás: final vs optimizer"
```

## Bike Escort Post-Gen (Előkészítés)

Kerékpáros kísérőket post-process lépésben tudsz kiosztani a `final.csv`-re:
- éjszakai szakaszok kitöltése elsőként,
- prioritás első körben: `Regi`, `Lilla`, `Bianka`,
- vállalt táv célérték alapján,
- nappali szakaszok opcionális feltöltése, ha marad vállalt kapacitás.

Minta konfiguráció:
- `data/bike_escorts.sample.json`
- Fix kivétel (beégetett aktuális terv): `data/bike_escorts.json`
  - `Brigi`: 1-19 (`day_only=true`)
  - `Máté`: 18-29
  - `Lajek`: 29-46
  - `GLackó`: 38-49 (átfedésnél felülír)

Futtatás:

```bash
python assign_bike_escorts.py \
  --final-csv data/final.csv \
  --config data/bike_escorts.json \
  --output data/final.csv
```

Megjegyzés: átfedő fix tartományoknál \"last wins\" szabály van (a későbbi fix tartomány felülír).

## GitHub Pages

Repository includes workflow: [`.github/workflows/pages.yml`](.github/workflows/pages.yml)

Behavior:

- on push to `main`, it regenerates `docs/index.html` from `data/final.csv`
- it also builds `docs/optimizer_compare.html` from `data/runner_plan_input.json` + `data/final.csv`
- deploys the `docs/` folder to GitHub Pages

Setup in GitHub:

1. Repository `Settings` -> `Pages`
2. `Build and deployment` -> `Source`: `GitHub Actions`
3. Commit and push changes to `main`

From this point, every edit to `data/final.csv` (or the script/workflow) republishes the page.

## Input schema

`segments`:

```json
[{ "id": 1, "km": 7.0 }]
```

`runners`:

```json
[
  {
    "name": "Bianka",
    "target_km": 30.0,
    "min_blocks": 1,
    "max_blocks": 2,
    "car_id": "3",
    "max_overflow_km": 4.0,
    "rest_priority": 1,
    "min_rest_gap_segments": 20
  }
]
```

`current_owner` (optional):

```json
{ "1": "Bianka", "2": "Lacko" }
```

`fixed_owner` (optional, hard assignment):

```json
{ "1": "Bianka" }
```

`settings` (optional):

```json
{
  "scale": 10,
  "time_limit_sec": 30,
  "num_workers": 8,
  "max_overflow_km": 4.0,
  "min_block_km": 4.0,
  "min_rest_gap_segments": 20,
  "first_leg_ratio_min": 0.4,
  "first_leg_ratio_max": 0.7,
  "enforce_second_round_order": true,
  "enforce_car_block_grouping": true,
  "car_block_order": ["3", "2", "1", "4", "3", "2"],
  "require_every_runner_used": true,
  "weights": {
    "overflow": 4,
    "underfill": 1,
    "change": 2,
    "car_span": 1,
    "rest_gap": 6
  }
}
```

## Notes

- `max_blocks=1` guarantees one contiguous block.
- `max_blocks=2` allows one or two contiguous blocks.
- `min_block_km>0` forbids too-short standalone blocks.
- `min_rest_gap_segments` enforces a hard minimum rest-gap (in segment count)
  between the two blocks of forced 2-block runners.
- `first_leg_ratio_min/max` enforces first-block ratio for forced 2-block runners
  (e.g. `0.4..0.7` means first block must be 40%-70% of that runner's assigned total km).
- `fixed_owner` forces exact runner ownership for specific segments (hard constraint).
- `enforce_second_round_order=true` keeps first and second-round order aligned for forced 2-block runners.
- `enforce_car_block_grouping=true` enforces car groups as contiguous car-level blocks
  (derived block count from members' `min_blocks`, typically 1 for one-round cars and 2 for two-round cars).
- `car_block_order` enforces a strict car-block sequence on the timeline
  (currently supports up to 2 occurrences per car ID).
- `rest_priority` tunes per-runner rest optimization strength (only relevant for 2-block runners).
- Car cohesion currently uses span minimization in segment index space.
- Strict inserted-runner (<=6 km) car rules are not implemented yet.
