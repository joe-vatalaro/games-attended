# Baseball Game Tracker — Plan

Personal tool to log every MLB game attended, attach official game data from the MLB Stats API, and report over the combined dataset. Optimize for fast setup and low maintenance. This document is the source of truth for v1 and the extension points for later work.

---

## 1. Purpose

Track games the user was physically at. Enrich each entry with real boxscore data. Answer questions like “what’s my record at games I’ve seen?” and “which parks are left?”

This is not a product. No auth, no mobile app, no live tracking.

## 2. v1 in one sentence

A local Python app: SQLite + a small web UI to add a game by date and teams, confirm or reject the MLB match, enrich automatically, and view reports. A CLI remains for batch work and re-enrich.

## 3. Non-goals for v1

- Multi-user / accounts
- Mobile app
- Minor league, spring training, or non-MLB games (schema will still allow them later)
- Live / in-progress games
- Charts, maps, photos, Statcast, structured companions
- Google Sheets as the system of record

---

## 4. Architecture

Two kinds of data, never mixed:

| Layer | Owner | Examples |
|---|---|---|
| Personal | you | date you remember, teams you typed, seats, notes, “I was there” |
| Official | MLB | `game_pk`, score, pitchers, attendance, duration, venue, linescore |

Join key: `mlb_game_pk`.

```text
UI / CLI
    │
    ▼
tracker library  (add, resolve, confirm, enrich, report)
    │
    ├── SQLite   data/games.db          (gitignored)
    ├── refs     data/parks.json, data/team_aliases.json
    └── MLB API  statsapi.mlb.com       (no auth)
           │
           └── optional cache  data/cache/{game_pk}.json
```

Rules that keep this future-proof:

- The UI and CLI are thin. All behavior lives in importable functions.
- Store MLB IDs (`team_id`, `venue_id`, player ids when we have them), not only display names.
- Store `game_type` even though v1 only cares about regular-season MLB.
- Cache the raw feed JSON so later features (home runs, walk-off play) are parsers, not new HTTP clients.

### Stack

- Python 3.12+
- `requests` for MLB
- `sqlite3` in the stdlib
- Flask for the local UI (one process, bind to localhost)
- No pandas in v1. Reports are SQL + Python dicts.

Run with:

```text
python -m tracker serve          # UI at http://127.0.0.1:5000
python -m tracker add ...        # optional CLI
python -m tracker enrich
python -m tracker report
```

---

## 5. Data model

### 5.1 `attended_games` (source of truth for “I was there”)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | ISO date as entered |
| `home_team` | TEXT | as typed |
| `away_team` | TEXT | as typed |
| `home_team_id` | INTEGER | resolved on add |
| `away_team_id` | INTEGER | resolved on add |
| `venue` | TEXT | optional; usually overwritten by MLB venue name |
| `seat_section` | TEXT | optional |
| `seat_row` | TEXT | optional |
| `seat_seat` | TEXT | optional |
| `notes` | TEXT | freeform |
| `mlb_game_pk` | INTEGER | null until accepted |
| `needs_review` | INTEGER | 1 if we guessed (rare once UI confirm exists) |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

Unique constraint: one attended row per `mlb_game_pk` when `mlb_game_pk` is not null. Prevents silently logging the same official game twice. A rejected match never writes a `game_pk`.

### 5.2 `game_details` (fetched, keyed by `mlb_game_pk`)

| Column | Type | Notes |
|---|---|---|
| `mlb_game_pk` | INTEGER PK | |
| `official_date` | TEXT | use this, not UTC `gameDate` |
| `season` | INTEGER | |
| `game_type` | TEXT | `R` regular, `F`/`D`/`L`/`W` postseason, `S` spring |
| `venue_id` | INTEGER | stable as parks rename |
| `venue_name` | TEXT | |
| `home_team_id` / `away_team_id` | INTEGER | |
| `home_score` / `away_score` | INTEGER | |
| `winning_team_id` | INTEGER | null on tie |
| `home_starter` / `away_starter` | TEXT | |
| `winning_pitcher` / `losing_pitcher` / `save_pitcher` | TEXT | |
| `attendance` | INTEGER | |
| `duration_minutes` | INTEGER | |
| `innings` | INTEGER | |
| `weather_condition` / `weather_temp` / `weather_wind` | TEXT | |
| `linescore_json` | TEXT | innings payload as JSON |
| `is_walkoff` | INTEGER | inferred at enrich |
| `is_extra_innings` | INTEGER | `innings > 9` |
| `is_no_hitter` | INTEGER | one side had 0 hits |
| `status` | TEXT | persist only Final games |
| `fetched_at` | TEXT | |

### 5.3 Deferred tables (do not create in v1)

- `game_events` — HRs, walk-off play, etc. Keyed by `mlb_game_pk`.
- `companions` — structured “who I went with.”
- `rooting_for` — optional favorite / per-game rooting team.

v1 reports that need “notable games” use the boolean flags on `game_details`.

### 5.4 Checked-in reference files

- `data/team_aliases.json` — `BOS`, `Red Sox`, `Boston`, `Sox` → team id `111`. Ambiguous aliases (`Chicago`, `Sox` if both apply) list multiple ids so the UI can ask.
- `data/parks.json` — current regular-season home parks: `venue_id`, name, tenant `team_id`. There are **30** MLB clubs, not 32. “Still need to visit” is “parks in this file minus distinct `venue_id`s attended.”

Do not hardcode a park count in code.

---

## 6. MLB API

Base: `https://statsapi.mlb.com` (undocumented, stable, no auth).

**Resolve candidates**

```text
GET /api/v1/schedule?sportId=1&date=YYYY-MM-DD
    &hydrate=decisions,linescore,weather,venue
```

Match on `officialDate` + home/away `team.id`. Ignore anything whose status is not Final (postponements show up on the original date).

Schedule hydrate already returns gamePk, scores, winner, WP/LP/SV, linescore, weather, venue, `doubleHeader`, `gameNumber`.

**Fetch full details** (after accept)

```text
GET /api/v1.1/game/{gamePk}/feed/live
```

This is the object the schedule `link` field already points at. It adds attendance, duration, actual starters, and play-by-play. Cache the raw JSON at `data/cache/{gamePk}.json`.

`--force` / a “Refresh from MLB” button re-fetches and overwrites `game_details`.

Skip creating `game_details` when `fetched_at` is already set, unless forced.

---

## 7. v1 interface

### 7.1 Local web UI (primary)

`python -m tracker serve` starts Flask on localhost.

Pages:

| Route | Purpose |
|---|---|
| `/` | Dashboard: recent games, unenriched / unmatched count, link to add and reports |
| `/add` | Form: date, home team, away team, optional notes and seats |
| `/confirm` | Candidate cards: accept or reject each MLB match |
| `/games` | List of attended games |
| `/games/<id>` | One game: personal fields + official details |
| `/report` | All v1 reports on one page |

No login. Personal machine only.

### 7.2 Add + accept / reject (core v1 flow)

This is the main way games enter the system.

```text
1. User submits date + home + away (+ optional notes/seats).
2. App resolves team names via aliases.
      - 0 matches → stay on form, “unknown team, try BOS or Red Sox”
      - 2+ for one side → stay on form, pick from the colliding options
3. App queries MLB schedule for that official date + team pair.
4. Branch:

   A. No Final game
      Show “No completed MLB game for that date and teams.”
      Offer: edit the form, or Save personal-only (mlb_game_pk stays null).
      Personal-only rows appear on the dashboard as “unmatched” so they
      can be confirmed later if the user had the date wrong.

   B. One or more Final games (doubleheader → two cards)
      For each candidate show:
        away @ home, official date, venue, final score,
        WP / LP, doubleheader game number if any
      And one of:
        - “Already in your log” (this game_pk is on an attended_games row)
        - Accept / Reject

   C. Already logged
      Accept is disabled (or labeled “Already saved”) with a link to
      the existing game. Reject just returns to /add.
      No silent duplicates.

5. Accept
      Insert attended_games with mlb_game_pk set.
      Fetch feed/live, upsert game_details, write cache file.
      Redirect to /games/<id>.

6. Reject
      Do not write that game_pk.
      If other candidates remain, stay on /confirm.
      If none remain, return to /add with “rejected — try a different date?”
      Reject does not create a personal-only row unless the user
      explicitly chooses Save personal-only from the no-match screen.
```

Accept/reject covers two “exists” cases:

1. **MLB has a game** for that date + teams → user confirms it is the one they attended (or picks game 1 vs game 2).
2. **The log already has that `game_pk`** → do not insert again; send them to the existing row.

Session/temp state: keep the pending add (typed teams, notes, seats, candidate list) in a signed cookie or a short-lived `pending_adds` table. Do not write `attended_games` until Accept or Save personal-only.

### 7.3 CLI (secondary)

Same library as the UI.

```text
python -m tracker add --date 2024-06-15 --home "Red Sox" --away "Yankees"
python -m tracker enrich [--force] [--id 12]
python -m tracker report [--html report.html]
python -m tracker list
```

Non-interactive `add` that finds exactly one Final game may auto-accept and print what it saved. Two games or an existing `game_pk` requires `--game-number` or fails with a message to use the UI. This keeps scripts possible without re-implementing confirm in a TTY unless we want it later.

---

## 8. Reports (v1)

All from `attended_games JOIN game_details`. Unmatched personal-only rows are excluded from W-L / attendance / duration and listed separately as “not yet confirmed.”

| Report | Definition |
|---|---|
| Overall W-L when attending | Home team won / lost / tied among confirmed games |
| Record by team seen | Each game counts for the home club and the away club, split “seen at home” vs “seen on the road” |
| Longest / shortest | By `duration_minutes` and by `innings` |
| Stadiums | Distinct `venue_id` visited, plus remaining current parks from `parks.json` |
| Attendance | Highest and lowest `attendance` |
| By year | Count (and W-L) by `season` |
| Notable | Rows where `is_walkoff`, `is_extra_innings`, or `is_no_hitter` |

Win/loss for v1 is **the home team’s result in games you saw**, plus the per-team breakdown. “My team’s record when I was there” waits on an optional `rooting_for_team_id` (see §11).

Render in `/report` and also as `python -m tracker report` text. Optional `--html` writes a static snapshot for sharing; the live page is the default.

---

## 9. Project layout

```text
tracker/
  __main__.py       # python -m tracker
  cli.py            # argparse
  app.py            # Flask routes
  db.py             # schema, migrations-by-version, CRUD
  teams.py          # alias → team_id
  mlb.py            # HTTP + parse schedule / feed
  enrich.py         # resolve candidates, upsert details, cache
  reports.py        # queries → dicts
  html.py           # optional static snapshot
data/
  games.db          # gitignored
  cache/            # gitignored raw feeds
  parks.json
  team_aliases.json
tests/
  fixtures/         # saved schedule + feed JSON
  test_teams.py
  test_mlb_parse.py
  test_confirm.py   # accept / reject / already-exists
  test_reports.py
```

Tests use fixtures only. No live API in CI.

`.gitignore` already covers `__pycache__` and venvs. Add:

```text
data/games.db
data/games.db-journal
data/cache/
report.html
```

---

## 10. Build order

1. Package + SQLite schema + `add`/`list` library functions (no UI yet).
2. Team aliases + collision handling.
3. MLB resolve + feed parse + cache, covered by fixture tests (include a doubleheader, a postponement, a night-game `officialDate`).
4. Confirm logic: accept, reject, already-exists, personal-only.
5. Flask UI: `/add` → `/confirm` → `/games/<id>`.
6. Enrich skip/`--force`, dashboard unmatched list.
7. `/report` + CLI `report`.
8. Manual pass: add a real game, reject a wrong doubleheader game, try a rainout date, confirm a duplicate is blocked.

Do not start with `game_events`, charts, or a public bind address.

---

## 11. Definition of done (v1)

- User can open the local UI, enter a date and two teams, and see MLB candidate(s) if they exist.
- Accept saves the attendance row, fills score / pitchers / attendance / duration / weather / venue, and shows the game page.
- Reject does not attach that `game_pk`. Already-logged games cannot be accepted again.
- No completed MLB match can be saved as personal-only and later confirmed.
- `python -m tracker report` and `/report` show: W-L, parks visited + remaining, longest/shortest, attendance extremes, games by year, notable flags.
- Re-running enrich does not overwrite unless forced.

---

## 12. Later features — how they attach

Nothing below is built in v1. Each item names the hook that already exists so we do not rewrite the core.

### 12.1 Game events (home runs, final play, walk-off description)

- New table `game_events(id, mlb_game_pk, event_type, inning, inning_half, player_id, player_name, description, extra_json)`.
- Parser reads `data/cache/{game_pk}.json` (or re-fetches with `--force`).
- UI: a section on `/games/<id>`. Report: “home runs I’ve seen.”
- Walk-off flag already on `game_details`; this adds the batter and the play string.

### 12.2 Statcast / pitch-level detail

- Add `pybaseball` (or raw Baseball Savant) keyed by `mlb_game_pk` + player id.
- New table or columns on `game_events` (`launch_speed`, `hit_distance`, etc.).
- Do not pull this into the v1 dependency list.

### 12.3 Spring training, playoffs filter, MiLB

- `game_details.game_type` is already stored. Playoffs work in v1 if the user accepts a postseason candidate (`sportId=1` still applies).
- Spring: allow `gameType=S` (or drop the Final-only regular-season assumption) in the resolver; same confirm UI.
- MiLB: pass a different `sportId`, keep the same `game_pk` join. Parks list becomes “MLB parks” vs “all venues I’ve visited.”

### 12.4 Rooting interest / “my team when I was there”

- Nullable `attended_games.rooting_for_team_id` and/or a config default favorite team.
- New report: that team’s W-L in games attended. Home-team W-L stays as the v1 definition.

### 12.5 Companions as structured data

- `companions(id, name)` + `game_companions(attended_game_id, companion_id)`.
- Notes stay freeform. UI gets a multi-select on `/add` and `/games/<id>/edit`.
- Report: “games with X.”

### 12.6 Edit seats / notes after the fact

- `/games/<id>/edit` writes only `attended_games` personal columns. Never let the form overwrite `game_details`.

### 12.7 Photos / ticket stubs

- Files on disk: `data/media/{attended_game_id}/...`
- `attended_games.media_dir` or a `game_media` table with paths.
- UI: upload on the game page. Gitignore `data/media/`.

### 12.8 Richer local UI (filters, search, maps)

- Same Flask app. `/games?year=&team=&park=`.
- Map: geocode once per `venue_id`, store lat/lon on a `venues` table or in `parks.json`.
- Charts: add a JS chart library on `/report` or a notebook that opens the same SQLite file.

### 12.9 CSV backfill / import

- `python -m tracker import games.csv` with columns `date,home,away,notes`.
- Each row runs the same resolve → candidate path. Ambiguous or already-exists rows write a review file instead of guessing.
- Useful once historical memory is being entered in bulk.

### 12.10 Re-derive columns after a parser bug

- Raw feed already cached. A `python -m tracker reparse` walks `data/cache/*.json` and upserts `game_details` / later `game_events` without hitting MLB.

### 12.11 Optional favorite-park progress beyond “current 30”

- Version `parks.json` (or add `active_from` / `active_to`).
- Historical parks (Olympic Stadium, old Yankee Stadium, Oakland Coliseum) become an optional second list: “current tour” vs “parks I have ever sat in.”

---

## 13. Decisions locked for v1

1. Personal rows and MLB facts are separate tables joined on `mlb_game_pk`.
2. Primary input is the local UI: date + teams → accept / reject.
3. Already-logged `game_pk` cannot be accepted again.
4. Reports use home-team W-L plus per-team seen breakdown.
5. Park progress uses `venue_id` against a 30-park reference file.
6. One HTTP client (`mlb.py`), one enrich path, raw JSON cached.
7. Flask on localhost; CLI for batch and report snapshots.
8. `game_events` and pandas wait.

## 14. Open until implement-time (small)

- Flask port (default 5000) and whether `serve` auto-opens a browser.
- Exact alias list for the 30 clubs (include common nicknames; keep `Chicago` / `Sox` / `LA` ambiguous on purpose).
- Whether personal-only save is on the no-match screen in the first UI slice, or added right after Accept/Reject works.

---

## 15. Suggested first implementation slice

Schema + aliases + resolve/parse tests + `/add` → `/confirm` → save one accepted game. Reports and CLI can follow immediately after a real game is in the database.
