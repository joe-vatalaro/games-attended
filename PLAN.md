# Baseball Game Tracker — Plan

Personal tool to log every MLB game attended, attach official game data from the MLB Stats API, and report over the combined dataset. Optimize for fast setup and low maintenance. This document is the source of truth for v1 and the extension points for later work.

---

## 1. Purpose

Track games the user was physically at. Enrich each entry with real boxscore data. Answer questions like “what’s my record at games I’ve seen?” and “which parks are left?”

This is not a product. No auth, no mobile app, no live tracking.

## 2. v1 in one sentence

A local Python app: SQLite + a small web UI to add a game by date (or year) and teams, confirm or reject the MLB match, enrich automatically, and view reports. A CLI remains for batch work and re-enrich.

## 3. Non-goals for v1

- Multi-user / accounts
- Mobile app
- Minor league or non-MLB games (schema still allows a later `sportId`)
- Live / in-progress games
- Charts, maps, photos, Statcast, structured companions
- Google Sheets as the system of record

Spring training and MLB postseason **are** in v1: they resolve like any Final game, get a label on confirm / the game page, and can be included or excluded on `/report` (default: regular season + playoffs).

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
- Store `game_type` and series fields so playoff / spring labels and the report filter do not need another source of truth.
- Cache the raw feed JSON so later features (home runs, walk-off play) are parsers, not new HTTP clients.

### Stack

- Python 3.9+
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
| `game_type` | TEXT | `R` regular, `F`/`D`/`L`/`W` postseason, `S` spring, `E` exhibition, `A` All-Star |
| `series_description` | TEXT | e.g. `World Series`; from schedule, not the live feed |
| `series_game_number` | INTEGER | series game, not doubleheader `gameNumber` |
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

### 5.3 Player tables (schema v3)

`player_game_stats` and `game_events` are in §16. They are official facts keyed by `mlb_game_pk`.

Still deferred: `companions`, `rooting_for`.

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
    [&teamId=]
```

or, when the user typed a year and both teams:

```text
GET /api/v1/schedule?sportId=1&startDate=YYYY-01-01&endDate=YYYY-12-31
    &teamId=&opponentId=&season=YYYY
    &hydrate=decisions,linescore,weather,venue
```

Match Final games only. Date + both teams, or year + both teams, keeps the home/away orientation the user typed. Date + one team matches that club on either side. Do not silently include the swapped home/away series.

Schedule hydrate already returns gamePk, scores, winner, WP/LP/SV, linescore, weather, venue, `doubleHeader`, `gameNumber`, `gameType`, `seriesDescription`, `seriesGameNumber`.

**Fetch full details** (after accept)

```text
GET /api/v1.1/game/{gamePk}/feed/live
```

This is the object the schedule `link` field already points at. It adds attendance, duration, actual starters, and play-by-play. Cache the raw JSON at `data/cache/{gamePk}.json`.

The live feed does **not** include series labels. After a postseason accept (or Refresh), also fetch `schedule?gamePk=` and store `series_description` / `series_game_number`. Spring training is labeled from `game_type=S` alone.

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
| `/add` | Form: date (full day or year), home team, away team, optional notes and seats |
| `/confirm` | Candidate cards: accept or reject each MLB match |
| `/games` | List of attended games |
| `/games/<id>` | One game: personal fields + official details; notes edit; delete; refresh |
| `/games/<id>/notes` | POST notes only |
| `/games/<id>/delete` | POST delete attended row + its `game_details` |
| `/report` | All v1 reports; `?type=` filter for regular / playoffs / spring / other |

No login. Personal machine only.

### 7.2 Add + accept / reject (core v1 flow)

This is the main way games enter the system.

```text
1. User submits a date or year, plus teams (+ optional notes/seats).
      Valid: full date + at least one team, or both teams + a year
      (type 2024 in the date field, or pick a day from the calendar).
2. App resolves team names via aliases.
      - 0 matches → stay on form, “unknown team, try BOS or Red Sox”
      - 2+ for one side → stay on form, pick from the colliding options
3. App queries MLB schedule (that day, or that season’s meetings
   in the typed home/away order).
4. Branch:

   A. No Final game
      Show “No completed MLB game for that date and teams.”
      Offer: edit the form, or Save personal-only (mlb_game_pk stays null).
      Personal-only rows appear on the dashboard as “unmatched” so they
      can be confirmed later if the user had the date wrong.

   B. One or more Final games (doubleheader or a season of meetings)
      For each candidate show:
        home / away, official date, venue, final score,
        WP / LP, doubleheader game number if any,
        playoff series label or “Spring Training” when it applies
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
python -m tracker add --date 2024 --home "Yankees" --away "Red Sox"
python -m tracker delete --id 12
python -m tracker enrich [--force] [--id 12]
python -m tracker report [--html report.html]
python -m tracker list
```

Non-interactive `add` that finds exactly one Final game may auto-accept and print what it saved. Two games or an existing `game_pk` requires `--game-number` or fails with a message to use the UI. This keeps scripts possible without re-implementing confirm in a TTY unless we want it later.

---

## 8. Reports (v1)

All from `attended_games JOIN game_details`. Unmatched personal-only rows are excluded from W-L / attendance / duration and listed separately as “not yet confirmed.”

`/report` has an Include filter: regular season, playoffs, spring training, other. Default is regular + playoffs. CLI `report` uses that same default. Unmatched rows always list; they have no `game_type`.

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

## 10. v1 status

The original build order (schema → aliases → resolve/parse → confirm → Flask → enrich skip → report → CLI) is done. Later add-flow and report work that landed in v1:

- Partial lookup: date + one team, or both teams + a year in the date field.
- Year search keeps the typed home/away (no swapped-park series).
- Delete a logged game (UI + `python -m tracker delete --id`).
- Edit notes on the game page (`?edit=notes`); seats stay add-time / personal-only for now.
- Playoff series labels and Spring Training labels on confirm + game page.
- Report game-type filter, default regular + playoffs.
- Player lines and home-run events from cached feeds (`reparse`, `/players`, game-page lineups).

Do not start with charts or a public bind address.

---

## 11. Definition of done (v1)

- User can open the local UI, enter a date (or year) and teams, and see MLB candidate(s) if they exist.
- Accept saves the attendance row, fills score / pitchers / attendance / duration / weather / venue, and shows the game page.
- Reject does not attach that `game_pk`. Already-logged games cannot be accepted again.
- No completed MLB match can be saved as personal-only and later confirmed.
- `python -m tracker report` and `/report` show: W-L, parks visited + remaining, longest/shortest, attendance extremes, games by year, notable flags. Report defaults to regular season + playoffs.
- Re-running enrich does not overwrite unless forced.

---

## 12. Later features — how they attach

Each item names the hook that already exists so we do not rewrite the core. Notes edit, playoff/spring labels, and the report type filter are already in v1; they are called out where they used to be “later.”

### 12.1 Player lines and game events

See **§16**. This is the next slice now that ~50 games are logged and cached.

### 12.2 Statcast / pitch-level detail

- Live feeds already include `hitData` on many balls in play (`launchSpeed`, `launchAngle`, `totalDistance`). Store that on `game_events.extra_json` in §16 — no new dependency.
- `pybaseball` / Baseball Savant stays optional later for pitch-by-pitch or missing Statcast on older games.

### 12.3 MiLB (and other sports)

- Playoffs and spring training are already in v1 (labels + report filter).
- MiLB: pass a different `sportId`, keep the same `game_pk` join. Parks list becomes “MLB parks” vs “all venues I’ve visited.”

### 12.4 Rooting interest / “my team when I was there”

- Nullable `attended_games.rooting_for_team_id` and/or a config default favorite team.
- New report: that team’s W-L in games attended. Home-team W-L stays as the v1 definition.

### 12.5 Companions as structured data

- `companions(id, name)` + `game_companions(attended_game_id, companion_id)`.
- Notes stay freeform. UI gets a multi-select on `/add` and `/games/<id>/edit`.
- Report: “games with X.”

### 12.6 Edit seats / more personal fields after the fact

- Notes edit is already on `/games/<id>?edit=notes` and writes only `attended_games.notes`.
- Seats / venue still need a similar personal-only edit. Never let the form overwrite `game_details`.

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
2. Primary input is the local UI: date or year + teams → accept / reject. Home/away order is kept on year search.
3. Already-logged `game_pk` cannot be accepted again.
4. Reports use home-team W-L plus per-team seen breakdown.
5. Park progress uses `venue_id` against a 30-park reference file.
6. One HTTP client (`mlb.py`), one enrich path, raw JSON cached.
7. Flask on localhost; CLI for batch and report snapshots.
8. Pandas waits. Player lines and `game_events` are the v2 slice (§16).
9. Report default is regular season + playoffs; spring / other are opt-in.

## 14. Settled in implementation

- Flask defaults to `127.0.0.1:5000`. `serve --no-browser` skips opening a tab.
- Team aliases live in `data/team_aliases.json`. `Chicago` / `Sox` / `LA` / `NY` stay ambiguous.
- Personal-only save is on the no-match confirm screen and needs a **full date** plus both teams (a year is not enough).
- Schema version is `3` (`player_game_stats`, `game_events`; v2 series columns still migrate on older DBs).

---

## 15. Suggested next slice

Seats edit, rooting-for, companions, and CSV import can wait. Optional next player work: more `game_events` types (walk-off, triple) and multi-HR games on the report. Do not reopen the add/confirm path unless a real lookup is wrong.

---

## 16. Player data (v2)

Goal: answer “who have I seen?”, “how many times has X started in front of me?”, “home runs I’ve witnessed”, and “what did Judge do in games I attended?” without a new HTTP client. Official player facts stay keyed by `mlb_game_pk` + MLB `player_id`. Personal “I was there” stays on `attended_games`.

As of this writing: 50 confirmed attended games, all with cached `data/cache/{game_pk}.json`. Those feeds already have full boxscore batting/pitching lines, batting-order lists, `allPlays` (including home runs with batter/pitcher/description), and often `hitData` (exit velo, angle, distance). Fixture JSON in tests is a slim subset — parsers must tolerate missing batting stats.

### 16.1 What we will not do in this slice

- Live player search against MLB while adding a game.
- Season-long MLB stats (only stats in games you attended).
- Pitch-by-pitch or Statcast beyond what the cached feed already has.
- Rooting-for / “my guy” (that is still §12.4).
- pandas.

### 16.2 Tables (schema v3)

`player_game_stats` — one row per player who appeared in a logged official game.

| Column | Notes |
|---|---|
| `mlb_game_pk`, `player_id` | composite PK |
| `player_name` | display name at parse time |
| `team_id`, `side` | `home` / `away` |
| `batting_order` | 1–9 starter, or 100+ for PH as MLB encodes it; null if pitcher-only |
| `started_game` | in the 9-man batting order |
| `started_pitching` | `gamesStarted` on the pitching line |
| batting ints | `pa`, `ab`, `h`, `doubles`, `triples`, `hr`, `r`, `rbi`, `bb`, `so`, `sb`, `hbp` |
| pitching ints | `outs` (prefer this over IP string), `h_allowed`, `r_allowed`, `er`, `bb_allowed`, `so_pitched`, `hr_allowed` |
| `pitching_decision` | `W` / `L` / `S` / `H` / null |

`game_events` — notable plays, not every PA.

| Column | Notes |
|---|---|
| `id` | PK |
| `mlb_game_pk`, `at_bat_index` | unique per play we store |
| `event_type` | first: `home_run`; next: `walk_off`, `triple`, `stolen_base` |
| `inning`, `inning_half` | `top` / `bottom` |
| `batter_id`, `batter_name`, `pitcher_id`, `pitcher_name` | |
| `description` | MLB play string |
| `rbi` | |
| `extra_json` | `hitData` when present |

No separate `players` table in v2. Name lives on the stat/event row. If a name changes, the next reparse updates it.

Delete of an attended game already removes `game_details`. Cascade-delete (or explicit delete) `player_game_stats` and `game_events` for that `mlb_game_pk` too.

### 16.3 Parse path

- New functions in `tracker/mlb.py`: `parse_player_game_stats(feed)`, `parse_game_events(feed)`.
- `tracker/enrich.py`: after `parse_game_details`, write the two tables. Same skip/`--force` as today.
- `python -m tracker reparse` walks `data/cache/*.json` and upserts `game_details`, `player_game_stats`, and `game_events` **without** hitting MLB. This is how the existing 50 games get player data on day one.
- Refresh from MLB still fetches the feed, then the same parsers run.
- Tests: expand one fixture (or add a second) that includes a batter line and a home-run play. Do not use the live API in CI.

### 16.4 Reports and UI (same game-type filter as `/report`)

Default view: regular season + playoffs, same `?type=` groups.

**Game page** (`/games/<id>`)

- Starting lineups (batting order 1–9) and starting pitchers (already have names on `game_details`; now they link to a player).
- Home runs in this game: “Nathan Lukes homers (8)…” with inning.

**Players**

- `/players` — table: name, games seen, games started (batter), games started (pitcher), HR seen, hits, AB (slash optional). Sort by games seen.
- `/players/<player_id>` — slash line and pitching line **only in games you attended**, plus the list of those games (date, matchup, that day’s line).

**Report additions** (can live on `/report` or the players index)

- Most seen players.
- Starting pitchers I’ve seen (count of `started_pitching`).
- Home runs I’ve seen (count + a short list of the longest if `hitData` exists).
- Multi-HR games / walk-off HR (once `walk_off` events exist).

### 16.5 Cool things that fall out for free

Once the two tables exist, these are queries, not new parsers:

- “How many times have I seen Aaron Judge?”
- Judge’s AVG/OBP/SLG in my games.
- “Have I ever seen a no-hitter start?” (already have `is_no_hitter`; now we know who).
- Longest homer I’ve seen (max `totalDistance` in `extra_json`).
- Players I’ve seen with both teams (same `player_id`, different `team_id`).
- Every starter at a given park.

### 16.6 Build order

1. Schema v3 + parsers + fixture tests + `reparse` over the local cache. **Done.**
2. Game page: lineups + HRs for one known game. **Done.**
3. `/players` and `/players/<id>` with the report type filter. **Done.**
4. Report blocks: most seen, starters seen, HRs seen. **Done.**
5. Optional second event types (`walk_off` is stored when a play is a walk-off and not already a HR).

### 16.7 Decisions locked for v2

1. Cache-first. Reparse the 50 feeds before any new MLB calls.
2. Two tables: per-game player lines, plus a small events table for HRs (and later walk-offs).
3. Same game-type filter as the current report.
4. Player identity is MLB `player_id`.
5. No Statcast client until the feed’s own `hitData` is stored and used.
