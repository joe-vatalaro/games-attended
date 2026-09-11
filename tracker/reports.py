from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from tracker.mlb import (
    HONOR_LABELS,
    HONOR_ORDER,
    HONOR_SHORT_LABELS,
    OTHER_GAME_TYPES,
    POSTSEASON_TYPES,
    SPRING_TYPES,
    UNKNOWN_COUNTRY,
    country_label,
    is_usa_country,
)
from tracker.paths import PARKS_PATH
from tracker.teams import all_teams, team_by_id

REPORT_TYPE_GROUPS = {
    "regular": frozenset({"R"}),
    "playoffs": POSTSEASON_TYPES,
    "spring": SPRING_TYPES,
    "other": OTHER_GAME_TYPES,
}
DEFAULT_REPORT_TYPE_GROUPS = ("regular", "playoffs")
GROUPED_GAME_TYPES = frozenset().union(*REPORT_TYPE_GROUPS.values())
DEPTH_CHART_POSITIONS = (
    ("CF", "CF"),
    ("LF", "LF"),
    ("RF", "RF"),
    ("SS", "SS"),
    ("2B", "2B"),
    ("3B", "3B"),
    ("1B", "1B"),
    ("C", "C"),
    ("DH", "DH"),
    ("SP", "SP"),
    ("RP", "RP"),
)
DEPTH_CHART_KEYS = {key for key, _label in DEPTH_CHART_POSITIONS}
DEPTH_CHART_ALIASES: dict[str, str] = {}
DEPTH_CHART_SKIP = {"PH", "PR", "OF", "TWP"}
DEPTH_CHART_PER_SLOT = 3
DEPTH_CHART_MAX_PER_SLOT = 10


def load_parks(path: Path | None = None) -> list[dict[str, Any]]:
    parks_path = path or PARKS_PATH
    return json.loads(parks_path.read_text())["parks"]


def parse_report_type_groups(values: list[str] | None, *, explicit: bool = False) -> list[str]:
    if not explicit and not values:
        return list(DEFAULT_REPORT_TYPE_GROUPS)
    return [value for value in values or [] if value in REPORT_TYPE_GROUPS]


def parse_min_count(value: str | int | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_min_pa(value: str | int | None) -> int:
    return parse_min_count(value)


def parse_depth_per_slot(value: str | int | None) -> int:
    if value is None or value == "":
        return DEPTH_CHART_PER_SLOT
    try:
        return max(1, min(DEPTH_CHART_MAX_PER_SLOT, int(value)))
    except (TypeError, ValueError):
        return DEPTH_CHART_PER_SLOT


def allowed_game_types(type_groups: list[str] | tuple[str, ...] | None) -> set[str]:
    groups = type_groups if type_groups is not None else DEFAULT_REPORT_TYPE_GROUPS
    allowed: set[str] = set()
    for group in groups:
        allowed.update(REPORT_TYPE_GROUPS.get(group, ()))
    return allowed


def _matches_type_filter(game: dict[str, Any], allowed: set[str], include_other: bool) -> bool:
    game_type = game.get("game_type") or "R"
    if game_type in allowed:
        return True
    return include_other and game_type not in GROUPED_GAME_TYPES


def build_report(
    conn,
    parks_path: Path | None = None,
    type_groups: list[str] | tuple[str, ...] | None = None,
    *,
    depth_per_slot: int | None = None,
) -> dict[str, Any]:
    selected = list(type_groups) if type_groups is not None else list(DEFAULT_REPORT_TYPE_GROUPS)
    allowed = allowed_game_types(selected)
    include_other = "other" in selected
    rows = conn.execute(
        """
        SELECT a.id, a.date, a.home_team, a.away_team, a.home_team_id, a.away_team_id,
               a.notes, a.mlb_game_pk,
               d.official_date, d.season, d.game_type, d.venue_id, d.venue_name, d.home_score, d.away_score,
               d.winning_team_id, d.attendance, d.duration_minutes, d.innings,
               d.is_walkoff, d.is_extra_innings, d.is_no_hitter,
               d.weather_condition, d.weather_temp, d.weather_wind
        FROM attended_games a
        LEFT JOIN game_details d ON d.mlb_game_pk = a.mlb_game_pk
        ORDER BY COALESCE(d.official_date, a.date) DESC, a.id DESC
        """
    ).fetchall()
    games = [dict(row) for row in rows]
    confirmed = [
        game
        for game in games
        if game["mlb_game_pk"]
        and game["home_score"] is not None
        and _matches_type_filter(game, allowed, include_other)
    ]
    unmatched = [game for game in games if game["mlb_game_pk"] is None]

    return {
        "type_groups": selected,
        "totals": {
            "attended": len(games),
            "confirmed": len(confirmed),
            "unmatched": len(unmatched),
        },
        "overall": _overall_record(confirmed),
        "by_team": _record_by_team(confirmed),
        "teams": _teams_checklist(confirmed),
        "longest_shortest": _longest_shortest(confirmed),
        "stadiums": _stadiums(confirmed, parks_path),
        "attendance": _attendance(confirmed),
        "extremes": _score_weather_extremes(confirmed),
        "extreme_charts": _extreme_charts(confirmed),
        "by_year": _by_year(confirmed),
        "notable": _notable(confirmed),
        "unmatched": unmatched,
        "players": player_highlights(
            conn,
            selected,
            depth_per_slot=depth_per_slot,
        ),
        "honors": seen_honors(conn, selected),
    }


def _overall_record(games: list[dict[str, Any]]) -> dict[str, int]:
    wins = losses = ties = 0
    for game in games:
        result = _home_result(game)
        if result == "W":
            wins += 1
        elif result == "L":
            losses += 1
        elif result == "T":
            ties += 1
    return {"wins": wins, "losses": losses, "ties": ties, "games": len(games)}


def _home_result(game: dict[str, Any]) -> str | None:
    home = game.get("home_score")
    away = game.get("away_score")
    if home is None or away is None:
        return None
    if home > away:
        return "W"
    if home < away:
        return "L"
    return "T"


def _record_by_team(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats: dict[int, dict[str, int]] = defaultdict(
        lambda: {
            "home_wins": 0,
            "home_losses": 0,
            "home_ties": 0,
            "away_wins": 0,
            "away_losses": 0,
            "away_ties": 0,
            "seen": 0,
        }
    )

    def add(team_id: int | None, side: str, result: str | None) -> None:
        if not team_id or not result:
            return
        bucket = stats[team_id]
        bucket["seen"] += 1
        if result == "W":
            bucket[f"{side}_wins"] += 1
        elif result == "L":
            bucket[f"{side}_losses"] += 1
        else:
            bucket[f"{side}_ties"] += 1

    for game in games:
        home_result = _home_result(game)
        away_result = {"W": "L", "L": "W", "T": "T"}.get(home_result or "")
        add(game.get("home_team_id"), "home", home_result)
        add(game.get("away_team_id"), "away", away_result)

    rows = []
    for team_id, bucket in stats.items():
        team = team_by_id(team_id)
        wins = bucket["home_wins"] + bucket["away_wins"]
        losses = bucket["home_losses"] + bucket["away_losses"]
        ties = bucket["home_ties"] + bucket["away_ties"]
        rows.append(
            {
                "team_id": team_id,
                "team": team.name if team else str(team_id),
                "abbreviation": team.abbreviation if team else "",
                **bucket,
                "wins": wins,
                "losses": losses,
                "ties": ties,
            }
        )
    rows.sort(key=lambda row: (-row["seen"], row["team"]))
    return rows


def _teams_checklist(games: list[dict[str, Any]]) -> dict[str, Any]:
    catalog = all_teams()
    seen_ids: set[int] = set()
    for game in games:
        for key in ("home_team_id", "away_team_id"):
            team_id = game.get(key)
            if team_id:
                seen_ids.add(team_id)
    seen = []
    for team in catalog:
        if team.id not in seen_ids:
            continue
        seen.append(
            {
                "team_id": team.id,
                "name": team.name,
                "games": sum(
                    1
                    for game in games
                    if game.get("home_team_id") == team.id or game.get("away_team_id") == team.id
                ),
            }
        )
    seen.sort(key=lambda row: row["name"])
    remaining = [
        {"team_id": team.id, "name": team.name}
        for team in catalog
        if team.id not in seen_ids
    ]
    remaining.sort(key=lambda row: row["name"])
    return {
        "seen_count": len(seen),
        "current_team_count": len(catalog),
        "seen": seen,
        "remaining": remaining,
    }


def _longest_shortest(games: list[dict[str, Any]]) -> dict[str, Any]:
    by_duration = [game for game in games if game.get("duration_minutes")]
    by_innings = [game for game in games if game.get("innings")]
    return {
        "longest_duration": max(by_duration, key=lambda g: g["duration_minutes"], default=None),
        "shortest_duration": min(by_duration, key=lambda g: g["duration_minutes"], default=None),
        "longest_innings": max(by_innings, key=lambda g: g["innings"], default=None),
        "shortest_innings": min(by_innings, key=lambda g: g["innings"], default=None),
    }


def _stadiums(games: list[dict[str, Any]], parks_path: Path | None) -> dict[str, Any]:
    parks = load_parks(parks_path)
    visited_ids = {game["venue_id"] for game in games if game.get("venue_id")}
    visited = []
    seen_venues: set[int] = set()
    for game in games:
        venue_id = game.get("venue_id")
        if not venue_id or venue_id in seen_venues:
            continue
        seen_venues.add(venue_id)
        visited.append(
            {
                "venue_id": venue_id,
                "name": game.get("venue_name"),
                "games": sum(1 for item in games if item.get("venue_id") == venue_id),
            }
        )
    visited.sort(key=lambda row: row["name"] or "")
    remaining = [park for park in parks if park["venue_id"] not in visited_ids]
    remaining.sort(key=lambda park: park["name"])
    return {
        "visited_count": len(visited),
        "current_park_count": len(parks),
        "visited": visited,
        "remaining": remaining,
    }


def _attendance(games: list[dict[str, Any]]) -> dict[str, Any]:
    with_attendance = [game for game in games if game.get("attendance")]
    return {
        "highest": max(with_attendance, key=lambda g: g["attendance"], default=None),
        "lowest": min(with_attendance, key=lambda g: g["attendance"], default=None),
    }


def _by_year(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, int]] = defaultdict(lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0})
    for game in games:
        year = game.get("season")
        if not year:
            date = game.get("official_date") or game.get("date") or ""
            year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
        if not year:
            continue
        result = _home_result(game)
        buckets[year]["games"] += 1
        if result == "W":
            buckets[year]["wins"] += 1
        elif result == "L":
            buckets[year]["losses"] += 1
        elif result == "T":
            buckets[year]["ties"] += 1
    return [{"year": year, **stats} for year, stats in sorted(buckets.items(), reverse=True)]


def _notable(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flagged = []
    for game in games:
        flags = []
        if game.get("is_walkoff"):
            flags.append("walk-off")
        if game.get("is_extra_innings"):
            flags.append("extras")
        if game.get("is_no_hitter"):
            flags.append("no-hitter")
        if flags:
            flagged.append({**game, "flags": flags})
    return flagged


def format_score(game: dict[str, Any]) -> str:
    away = game.get("away_score")
    home = game.get("home_score")
    if away is None or home is None:
        return "—"
    return f"{game.get('away_team')} {away}, {game.get('home_team')} {home}"


def format_record(wins: int, losses: int, ties: int = 0) -> str:
    if ties:
        return f"{wins}-{losses}-{ties}"
    return f"{wins}-{losses}"


def format_avg(hits: int | None, at_bats: int | None) -> str:
    if not at_bats:
        return "—"
    value = (hits or 0) / at_bats
    formatted = f"{value:.3f}"
    if formatted.startswith("0"):
        return formatted[1:]
    return formatted


def format_slash(
    hits: int | None = 0,
    at_bats: int | None = 0,
    walks: int | None = 0,
    hbp: int | None = 0,
    pa: int | None = 0,
    doubles: int | None = 0,
    triples: int | None = 0,
    hr: int | None = 0,
) -> str:
    obp_denom = pa or ((at_bats or 0) + (walks or 0) + (hbp or 0))
    total_bases = (hits or 0) + (doubles or 0) + 2 * (triples or 0) + 3 * (hr or 0)
    return (
        f"{format_avg(hits, at_bats)}/"
        f"{format_avg((hits or 0) + (walks or 0) + (hbp or 0), obp_denom)}/"
        f"{format_avg(total_bases, at_bats)}"
    )


def format_innings_pitched(outs: int | None) -> str:
    if outs is None:
        return "—"
    return f"{outs // 3}.{outs % 3}"


def game_boxscore(game: dict[str, Any], stats: list[dict[str, Any]]) -> dict[str, Any]:
    by_side = {"away": [], "home": []}
    for row in stats:
        side = row.get("side")
        if side in by_side:
            by_side[side].append(row)
    batting = {side: _box_batters(rows) for side, rows in by_side.items()}
    pitching = {side: _box_pitchers(rows) for side, rows in by_side.items()}
    return {
        "linescore": _box_linescore(game, batting, by_side),
        "batting": batting,
        "pitching": pitching,
    }


def _box_batters(rows: list[dict[str, Any]]) -> dict[str, Any]:
    batters = [
        {**row, "box_position": _box_position(row)}
        for row in rows
        if row.get("started_game") or row.get("pa") or row.get("ab")
    ]
    batters.sort(
        key=lambda row: (
            row.get("batting_order") is None,
            row.get("batting_order") or 99,
            row.get("player_name") or "",
        )
    )
    return {
        "rows": batters,
        "totals": _sum_box_stats(batters, ("ab", "r", "h", "rbi", "bb", "so", "hr")),
    }


def _box_pitchers(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pitchers = [
        row
        for row in rows
        if row.get("started_pitching") or row.get("outs") or row.get("bf")
    ]
    pitchers.sort(
        key=lambda row: (
            0 if row.get("started_pitching") else 1,
            row.get("player_name") or "",
        )
    )
    totals = _sum_box_stats(
        pitchers,
        ("outs", "h_allowed", "r_allowed", "er", "bb_allowed", "so_pitched", "hr_allowed"),
    )
    totals["innings_pitched"] = format_innings_pitched(totals.get("outs") or 0)
    return {"rows": pitchers, "totals": totals}


def _box_position(row: dict[str, Any]) -> str:
    position = row.get("fielding_position")
    if position and position != "—":
        return position
    if row.get("started_pitching") or row.get("outs"):
        return "P"
    if not row.get("started_game"):
        return "PH"
    return ""


def _sum_box_stats(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, int]:
    return {key: sum(row.get(key) or 0 for row in rows) for key in keys}


def _box_linescore(
    game: dict[str, Any],
    batting: dict[str, dict[str, Any]],
    by_side: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    innings = _parse_linescore_innings(game.get("linescore_json"))
    if not innings and game.get("home_score") is None:
        return None
    if not innings:
        innings = [{} for _ in range(game.get("innings") or 9)]
    labels = [inning.get("num") or index + 1 for index, inning in enumerate(innings)]
    has_runs = any(_inning_runs(inning, "away") is not None or _inning_runs(inning, "home") is not None for inning in innings)
    away_cells = [_inning_cell(inning, "away") for inning in innings]
    home_cells = [_inning_cell(inning, "home") for inning in innings]
    if has_runs and innings:
        last = innings[-1]
        last_num = last.get("num") or len(innings)
        home_winning = (game.get("home_score") or 0) > (game.get("away_score") or 0)
        if (
            home_winning
            and last_num >= 9
            and _inning_runs(last, "home") is None
            and _inning_runs(last, "away") is not None
        ):
            home_cells[-1] = "X"
    return {
        "labels": labels,
        "away": {
            "cells": away_cells,
            "r": game.get("away_score"),
            "h": _linescore_or_stat_total(innings, "away", "hits", batting["away"]["totals"].get("h") or 0),
            "e": _linescore_or_stat_total(
                innings,
                "away",
                "errors",
                sum(row.get("fielding_errors") or 0 for row in by_side["away"]),
            ),
        },
        "home": {
            "cells": home_cells,
            "r": game.get("home_score"),
            "h": _linescore_or_stat_total(innings, "home", "hits", batting["home"]["totals"].get("h") or 0),
            "e": _linescore_or_stat_total(
                innings,
                "home",
                "errors",
                sum(row.get("fielding_errors") or 0 for row in by_side["home"]),
            ),
        },
    }


def _parse_linescore_innings(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [inning if isinstance(inning, dict) else {} for inning in payload]


def _inning_runs(inning: dict[str, Any], side: str) -> int | None:
    half = inning.get(side)
    if not isinstance(half, dict) or "runs" not in half:
        return None
    return half.get("runs")


def _inning_cell(inning: dict[str, Any], side: str) -> str:
    runs = _inning_runs(inning, side)
    return str(runs) if runs is not None else ""


def _linescore_or_stat_total(
    innings: list[dict[str, Any]],
    side: str,
    key: str,
    fallback: int,
) -> int:
    seen = False
    total = 0
    for inning in innings:
        half = inning.get(side)
        if not isinstance(half, dict) or key not in half:
            continue
        seen = True
        total += half.get(key) or 0
    return total if seen else fallback


def format_rate(value: float | None, digits: int = 3, *, leading_zero: bool = False) -> str:
    if value is None:
        return "—"
    formatted = f"{value:.{digits}f}"
    if not leading_zero and formatted.startswith("0"):
        return formatted[1:]
    return formatted


def _ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


PLAYER_COUNT_KEYS = (
    "pa",
    "ab",
    "h",
    "doubles",
    "triples",
    "hr",
    "r",
    "rbi",
    "bb",
    "so",
    "sb",
    "hbp",
    "ibb",
    "cs",
    "sf",
    "sac",
    "gidp",
    "outs",
    "h_allowed",
    "r_allowed",
    "er",
    "bb_allowed",
    "so_pitched",
    "hr_allowed",
    "hbp_allowed",
    "ibb_allowed",
    "wp",
    "bk",
    "bf",
    "pitches",
    "strikes",
    "blown_saves",
    "complete_games",
    "shutouts",
    "inherited_runners",
    "inherited_runners_scored",
    "putouts",
    "assists",
    "fielding_errors",
    "chances",
    "passed_balls",
    "pickoffs",
    "stolen_bases_against",
    "caught_stealing_against",
    "fielding_games_started",
)


def _stat_column(key: str, label: str, value_key: str | None = None) -> dict[str, str]:
    return {"key": key, "label": label, "value_key": value_key or key}


BATTING_TABLE_COLUMNS = [
    _stat_column("batting_games", "G"),
    _stat_column("games_started", "GS"),
    _stat_column("pa", "PA"),
    _stat_column("ab", "AB"),
    _stat_column("r", "R"),
    _stat_column("h", "H"),
    _stat_column("doubles", "2B"),
    _stat_column("triples", "3B"),
    _stat_column("hr", "HR"),
    _stat_column("rbi", "RBI"),
    _stat_column("bb", "BB"),
    _stat_column("ibb", "IBB"),
    _stat_column("so", "SO"),
    _stat_column("hbp", "HBP"),
    _stat_column("sf", "SF"),
    _stat_column("sac", "SAC"),
    _stat_column("gidp", "GIDP"),
    _stat_column("sb", "SB"),
    _stat_column("cs", "CS"),
    _stat_column("avg", "AVG", "avg_value"),
    _stat_column("obp", "OBP", "obp_value"),
    _stat_column("slg", "SLG", "slg_value"),
    _stat_column("ops", "OPS", "ops_value"),
]

PITCHING_TABLE_COLUMNS = [
    _stat_column("pitching_games", "G"),
    _stat_column("games_started_pitching", "GS"),
    _stat_column("wins", "W"),
    _stat_column("losses", "L"),
    _stat_column("saves", "SV"),
    _stat_column("holds", "HLD"),
    _stat_column("blown_saves", "BS"),
    _stat_column("innings_pitched", "IP", "outs"),
    _stat_column("h_allowed", "H"),
    _stat_column("r_allowed", "R"),
    _stat_column("er", "ER"),
    _stat_column("bb_allowed", "BB"),
    _stat_column("ibb_allowed", "IBB"),
    _stat_column("so_pitched", "SO"),
    _stat_column("hr_allowed", "HR"),
    _stat_column("hbp_allowed", "HBP"),
    _stat_column("wp", "WP"),
    _stat_column("bk", "BK"),
    _stat_column("bf", "BF"),
    _stat_column("pitches", "Pitches"),
    _stat_column("strikes", "Strikes"),
    _stat_column("complete_games", "CG"),
    _stat_column("shutouts", "SHO"),
    _stat_column("inherited_runners", "IR"),
    _stat_column("inherited_runners_scored", "IRS"),
    _stat_column("era", "ERA", "era_value"),
    _stat_column("whip", "WHIP", "whip_value"),
    _stat_column("k9", "K/9", "k9_value"),
    _stat_column("bb9", "BB/9", "bb9_value"),
]

FIELDING_TABLE_COLUMNS = [
    _stat_column("fielding_games", "G"),
    _stat_column("fielding_games_started", "GS"),
    _stat_column("fielding_position", "POS"),
    _stat_column("putouts", "PO"),
    _stat_column("assists", "A"),
    _stat_column("fielding_errors", "E"),
    _stat_column("chances", "TC"),
    _stat_column("fpct", "FPCT", "fpct_value"),
    _stat_column("passed_balls", "PB"),
    _stat_column("caught_stealing_against", "CS"),
    _stat_column("stolen_bases_against", "SBA"),
    _stat_column("pickoffs", "PK"),
    _stat_column("cs_pct", "CS%", "cs_pct_value"),
]


def _enrich_player_rates(item: dict[str, Any]) -> dict[str, Any]:
    hits = item.get("h") or 0
    at_bats = item.get("ab") or 0
    walks = item.get("bb") or 0
    hbp = item.get("hbp") or 0
    sac_flies = item.get("sf") or 0
    doubles = item.get("doubles") or 0
    triples = item.get("triples") or 0
    hr = item.get("hr") or 0
    total_bases = hits + doubles + 2 * triples + 3 * hr
    avg_value = _ratio(hits, at_bats)
    obp_value = _ratio(hits + walks + hbp, at_bats + walks + hbp + sac_flies)
    slg_value = _ratio(total_bases, at_bats)
    ops_value = None if obp_value is None or slg_value is None else obp_value + slg_value
    item["avg_value"] = avg_value
    item["obp_value"] = obp_value
    item["slg_value"] = slg_value
    item["ops_value"] = ops_value
    item["avg"] = format_rate(avg_value)
    item["obp"] = format_rate(obp_value)
    item["slg"] = format_rate(slg_value)
    item["ops"] = format_rate(ops_value)
    item["slash"] = format_slash(
        hits, at_bats, walks, hbp, item.get("pa"), doubles, triples, hr
    )
    outs = item.get("outs") or 0
    item["innings_pitched"] = format_innings_pitched(outs if outs else None)
    er = item.get("er") or 0
    hits_allowed = item.get("h_allowed") or 0
    walks_allowed = item.get("bb_allowed") or 0
    strikeouts = item.get("so_pitched") or 0
    era_value = _ratio(er * 27, outs)
    whip_value = _ratio((hits_allowed + walks_allowed) * 3, outs)
    k9_value = _ratio(strikeouts * 27, outs)
    bb9_value = _ratio(walks_allowed * 27, outs)
    item["era_value"] = era_value
    item["whip_value"] = whip_value
    item["k9_value"] = k9_value
    item["bb9_value"] = bb9_value
    item["era"] = format_rate(era_value, 2, leading_zero=True)
    item["whip"] = format_rate(whip_value, 2, leading_zero=True)
    item["k9"] = format_rate(k9_value, 1, leading_zero=True)
    item["bb9"] = format_rate(bb9_value, 1, leading_zero=True)
    putouts = item.get("putouts") or 0
    assists = item.get("assists") or 0
    fielding_errors = item.get("fielding_errors") or 0
    fpct_value = _ratio(putouts + assists, putouts + assists + fielding_errors)
    caught = item.get("caught_stealing_against") or 0
    stolen = item.get("stolen_bases_against") or 0
    cs_pct_value = _ratio(caught, caught + stolen)
    item["fpct_value"] = fpct_value
    item["cs_pct_value"] = cs_pct_value
    item["fpct"] = format_rate(fpct_value)
    item["cs_pct"] = format_rate(cs_pct_value)
    item["fielding_position"] = _primary_fielding_position(
        item.get("fielding_positions") or item.get("fielding_position")
    )
    return item


SKIP_FIELDING_POSITIONS = {"DH", "PH", "PR"}


def _primary_fielding_position(raw: str | None) -> str:
    if not raw:
        return "—"
    counts: dict[str, int] = {}
    for part in str(raw).split(","):
        position = part.strip()
        if not position or position in SKIP_FIELDING_POSITIONS:
            continue
        counts[position] = counts.get(position, 0) + 1
    if not counts:
        return "—"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _fielded(row: dict[str, Any]) -> bool:
    return any(
        int(row.get(key) or 0)
        for key in (
            "putouts",
            "assists",
            "fielding_errors",
            "chances",
            "fielding_games_started",
            "passed_balls",
            "pickoffs",
        )
    )


def event_hit_data(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("extra_json")
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def event_hit_stat(event: dict[str, Any], key: str) -> float | None:
    value = event_hit_data(event).get(key)
    if value is None or value == "":
        return None
    return float(value)


def event_hit_distance(event: dict[str, Any]) -> float | None:
    return event_hit_stat(event, "totalDistance")


def event_exit_velo(event: dict[str, Any]) -> float | None:
    return event_hit_stat(event, "launchSpeed")


def event_launch_angle(event: dict[str, Any]) -> float | None:
    return event_hit_stat(event, "launchAngle")


def event_spray_point(event: dict[str, Any]) -> tuple[float, float] | None:
    coords = event_hit_data(event).get("coordinates") or {}
    if not isinstance(coords, dict):
        return None
    x = coords.get("coordX")
    y = coords.get("coordY")
    if x is None or y is None or x == "" or y == "":
        return None
    return float(x), float(y)


def event_is_walkoff_hr(event: dict[str, Any]) -> bool:
    if not event.get("game_is_walkoff"):
        return False
    if event.get("inning_half") != "bottom":
        return False
    inning = event.get("inning")
    if inning is None:
        return False
    game_innings = event.get("game_innings")
    if game_innings:
        return inning == game_innings
    return inning >= 9


def _weather_temp_f(game: dict[str, Any]) -> int | None:
    raw = game.get("weather_temp")
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).split()[0])
    except ValueError:
        return None


def _combined_runs(game: dict[str, Any]) -> int | None:
    home = game.get("home_score")
    away = game.get("away_score")
    if home is None or away is None:
        return None
    return home + away


def _run_margin(game: dict[str, Any]) -> int | None:
    home = game.get("home_score")
    away = game.get("away_score")
    if home is None or away is None:
        return None
    return abs(home - away)


def _score_weather_extremes(games: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [game for game in games if _combined_runs(game) is not None]
    with_temp = []
    for game in games:
        temp = _weather_temp_f(game)
        if temp is None:
            continue
        with_temp.append({**game, "temp_f": temp})
    return {
        "highest_scoring": max(scored, key=_combined_runs, default=None),
        "lowest_scoring": min(scored, key=_combined_runs, default=None),
        "biggest_margin": max(scored, key=_run_margin, default=None),
        "hottest": max(with_temp, key=lambda game: game["temp_f"], default=None),
        "coldest": min(with_temp, key=lambda game: game["temp_f"], default=None),
        "shutouts": sum(
            1
            for game in scored
            if game.get("home_score") == 0 or game.get("away_score") == 0
        ),
    }


def _extreme_charts(games: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "duration": _metric_chart("Game length", "min", games, lambda game: game.get("duration_minutes")),
        "innings": _metric_chart("Innings", "inn", games, lambda game: game.get("innings")),
        "attendance": _metric_chart("Attendance", "fans", games, lambda game: game.get("attendance")),
        "runs": _metric_chart("Runs scored", "runs", games, _combined_runs),
        "margin": _metric_chart("Margin", "runs", games, _run_margin),
        "temp": _metric_chart("Temperature", "°F", games, _weather_temp_f),
        "shutouts": _metric_chart("Shutouts", "runs", games, _combined_runs, mark_shutouts=True),
    }


def _metric_chart(
    title: str,
    unit: str,
    games: list[dict[str, Any]],
    value_of,
    *,
    mark_shutouts: bool = False,
) -> dict[str, Any]:
    points = []
    for game in games:
        value = value_of(game)
        if value is None:
            continue
        home = game.get("home_score")
        away = game.get("away_score")
        point = {
            "id": game.get("id"),
            "date": game.get("official_date") or game.get("date"),
            "value": value,
            "score": format_score(game),
            "venue": game.get("venue_name"),
        }
        if mark_shutouts or unit == "runs":
            point["shutout"] = home == 0 or away == 0
        if unit == "°F" and game.get("weather_condition"):
            point["detail"] = game["weather_condition"]
        points.append(point)
    points.sort(key=lambda row: (row.get("date") or "", row.get("id") or 0))
    return {"title": title, "unit": unit, "points": points}


def _type_filter_sql(type_groups: list[str] | tuple[str, ...] | None) -> tuple[str, list[Any]]:
    selected = list(type_groups) if type_groups is not None else list(DEFAULT_REPORT_TYPE_GROUPS)
    allowed = allowed_game_types(selected)
    include_other = "other" in selected
    clauses: list[str] = []
    params: list[Any] = []
    if allowed:
        placeholders = ", ".join("?" * len(allowed))
        clauses.append(f"COALESCE(d.game_type, 'R') IN ({placeholders})")
        params.extend(sorted(allowed))
    if include_other:
        grouped = sorted(GROUPED_GAME_TYPES)
        placeholders = ", ".join("?" * len(grouped))
        clauses.append(f"COALESCE(d.game_type, 'R') NOT IN ({placeholders})")
        params.extend(grouped)
    if not clauses:
        return "1 = 0", []
    return f"({' OR '.join(clauses)})", params


def list_player_summaries(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    where, params = _type_filter_sql(type_groups)
    count_sql = ",\n            ".join(
        f"SUM(COALESCE(p.{key}, 0)) AS {key}" for key in PLAYER_COUNT_KEYS
    )
    rows = conn.execute(
        f"""
        SELECT
            p.player_id,
            MAX(p.player_name) AS player_name,
            COUNT(*) AS games_seen,
            SUM(p.started_game) AS games_started,
            SUM(p.started_pitching) AS games_started_pitching,
            SUM(CASE WHEN COALESCE(p.pa, 0) > 0 THEN 1 ELSE 0 END) AS batting_games,
            SUM(CASE WHEN COALESCE(p.outs, 0) > 0 OR p.started_pitching = 1
                OR p.pitching_decision IS NOT NULL THEN 1 ELSE 0 END) AS pitching_games,
            SUM(CASE WHEN COALESCE(p.putouts, 0) + COALESCE(p.assists, 0)
                + COALESCE(p.fielding_errors, 0) + COALESCE(p.chances, 0)
                + COALESCE(p.fielding_games_started, 0) + COALESCE(p.passed_balls, 0)
                + COALESCE(p.pickoffs, 0) > 0 THEN 1 ELSE 0 END) AS fielding_games,
            GROUP_CONCAT(p.fielding_position) AS fielding_positions,
            SUM(CASE WHEN p.pitching_decision = 'W' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN p.pitching_decision = 'L' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN p.pitching_decision = 'S' THEN 1 ELSE 0 END) AS saves,
            SUM(CASE WHEN p.pitching_decision = 'H' THEN 1 ELSE 0 END) AS holds,
            {count_sql}
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        WHERE {where}
        GROUP BY p.player_id
        ORDER BY games_seen DESC, player_name
        """,
        params,
    ).fetchall()
    summaries = []
    for row in rows:
        item = _enrich_player_rates(dict(row))
        summaries.append(item)
    honor_map = honor_types_by_player(conn)
    for item in summaries:
        item["honors"] = honor_map.get(item["player_id"], [])
    return summaries


def list_player_games(
    conn,
    player_id: int,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    where, params = _type_filter_sql(type_groups)
    rows = conn.execute(
        f"""
        SELECT p.*, a.id AS attended_id, a.date, a.home_team, a.away_team,
               d.official_date, d.home_score, d.away_score, d.game_type,
               d.series_description, d.series_game_number, d.venue_name
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        WHERE p.player_id = ? AND {where}
        ORDER BY COALESCE(d.official_date, a.date) DESC, a.id DESC
        """,
        [player_id, *params],
    ).fetchall()
    return [dict(row) for row in rows]


def player_page(
    conn,
    player_id: int,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    from tracker import db

    games = list_player_games(conn, player_id, type_groups)
    name = games[0]["player_name"] if games else db.get_player_name(conn, player_id)
    if name is None:
        return None
    totals = _sum_player_lines(games)
    totals["player_id"] = player_id
    totals["player_name"] = name
    return {
        "player_id": player_id,
        "player_name": name,
        "totals": totals,
        "games": games,
        "honors": honors_for_player(conn, player_id),
        "birthplace": birthplace_label(db.get_player_profile(conn, player_id)),
        "type_groups": list(type_groups) if type_groups is not None else list(DEFAULT_REPORT_TYPE_GROUPS),
    }


def list_home_runs(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    where, params = _type_filter_sql(type_groups)
    rows = conn.execute(
        f"""
        SELECT e.*, a.id AS attended_id, a.home_team, a.away_team,
               COALESCE(d.official_date, a.date) AS game_date, d.venue_name,
               d.is_walkoff AS game_is_walkoff, d.innings AS game_innings
        FROM game_events e
        JOIN attended_games a ON a.mlb_game_pk = e.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = e.mlb_game_pk
        WHERE e.event_type = 'home_run' AND {where}
        ORDER BY game_date DESC, e.at_bat_index
        """,
        params,
    ).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        hit = event_hit_data(item)
        item["distance"] = event_hit_stat(item, "totalDistance")
        item["exit_velo"] = event_hit_stat(item, "launchSpeed")
        item["launch_angle"] = event_hit_stat(item, "launchAngle")
        item["trajectory"] = hit.get("trajectory")
        point = event_spray_point(item)
        item["spray_x"] = point[0] if point else None
        item["spray_y"] = point[1] if point else None
        item["is_walkoff"] = event_is_walkoff_hr(item)
        events.append(item)
    return events


def list_batting_nights(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    where, params = _type_filter_sql(type_groups)
    slam_rows = conn.execute(
        f"""
        SELECT e.mlb_game_pk, e.batter_id
        FROM game_events e
        JOIN attended_games a ON a.mlb_game_pk = e.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = e.mlb_game_pk
        WHERE e.event_type = 'home_run' AND e.rbi = 4 AND {where}
        """,
        params,
    ).fetchall()
    slams = {(row["mlb_game_pk"], row["batter_id"]) for row in slam_rows}
    rows = conn.execute(
        f"""
        SELECT p.mlb_game_pk, p.player_id, p.player_name, p.h, p.ab, p.hr, p.rbi,
               p.doubles, p.triples, a.id AS attended_id, a.home_team, a.away_team,
               COALESCE(d.official_date, a.date) AS game_date
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        WHERE {where}
          AND (
            COALESCE(p.hr, 0) >= 2
            OR COALESCE(p.h, 0) >= 4
            OR COALESCE(p.rbi, 0) >= 4
          )
        ORDER BY COALESCE(p.hr, 0) DESC, COALESCE(p.rbi, 0) DESC,
                 COALESCE(p.h, 0) DESC, game_date DESC
        """,
        params,
    ).fetchall()
    nights = []
    for row in rows:
        item = dict(row)
        item["grand_slam"] = (item["mlb_game_pk"], item["player_id"]) in slams
        item["flags"] = _batting_night_flags(item)
        nights.append(item)
    return nights


def _batting_night_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if row.get("grand_slam"):
        flags.append("grand slam")
    hr = row.get("hr") or 0
    if hr >= 2:
        flags.append(f"{hr} HR")
    hits = row.get("h") or 0
    at_bats = row.get("ab")
    if hits >= 4:
        flags.append(f"{hits}-{at_bats}" if at_bats is not None else f"{hits} H")
    rbi = row.get("rbi") or 0
    if rbi >= 4:
        flags.append(f"{rbi} RBI")
    return flags


def list_pitching_gems(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    where, params = _type_filter_sql(type_groups)
    rows = conn.execute(
        f"""
        SELECT p.player_id, p.player_name, p.outs, p.h_allowed, p.so_pitched,
               p.er, p.complete_games, p.shutouts, p.started_pitching,
               a.id AS attended_id, a.home_team, a.away_team,
               COALESCE(d.official_date, a.date) AS game_date
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        WHERE {where}
          AND (
            COALESCE(p.so_pitched, 0) >= 9
            OR (
              COALESCE(p.outs, 0) >= 15
              AND p.h_allowed IS NOT NULL
              AND p.h_allowed <= 2
            )
            OR COALESCE(p.complete_games, 0) > 0
            OR COALESCE(p.shutouts, 0) > 0
          )
        ORDER BY COALESCE(p.so_pitched, 0) DESC, COALESCE(p.h_allowed, 99) ASC,
                 game_date DESC
        """,
        params,
    ).fetchall()
    gems = []
    for row in rows:
        item = dict(row)
        item["innings_pitched"] = format_innings_pitched(item.get("outs"))
        item["flags"] = _pitching_gem_flags(item)
        gems.append(item)
    return gems


def _pitching_gem_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    strikeouts = row.get("so_pitched") or 0
    if strikeouts >= 9:
        flags.append(f"{strikeouts} K")
    outs = row.get("outs") or 0
    hits = row.get("h_allowed")
    if outs >= 15 and hits is not None and hits <= 2:
        flags.append(f"{hits} H in {format_innings_pitched(outs)}")
    if row.get("complete_games"):
        flags.append("CG")
    if row.get("shutouts"):
        flags.append("SHO")
    return flags


def list_depth_chart(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
    *,
    per_slot: int = DEPTH_CHART_PER_SLOT,
) -> list[dict[str, Any]]:
    where, params = _type_filter_sql(type_groups)
    rows = conn.execute(
        f"""
        SELECT p.player_id, p.player_name, p.fielding_position,
               p.started_pitching, p.outs, p.fielding_games_started
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        WHERE {where}
        """,
        params,
    ).fetchall()
    counts: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        item = dict(row)
        for position in _positions_played(item):
            slot = counts[position]
            player_id = item["player_id"]
            current = slot.get(player_id)
            if current is None:
                current = {
                    "player_id": player_id,
                    "player_name": item["player_name"],
                    "games": 0,
                    "starts": 0,
                }
                slot[player_id] = current
            current["games"] += 1
            if _started_at_position(item, position):
                current["starts"] += 1
    chart = []
    for key, label in DEPTH_CHART_POSITIONS:
        players = sorted(
            counts.get(key, {}).values(),
            key=lambda row: (-row["games"], -row["starts"], row["player_name"]),
        )
        chart.append(
            {
                "key": key,
                "label": label,
                "players": players[:per_slot],
            }
        )
    return chart


def _positions_played(row: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for part in str(row.get("fielding_position") or "").split(","):
        position = DEPTH_CHART_ALIASES.get(part.strip().upper(), part.strip().upper())
        if position == "P":
            position = _pitching_role(row)
        if not position or position in DEPTH_CHART_SKIP or position not in DEPTH_CHART_KEYS:
            continue
        if position not in found:
            found.append(position)
    role = _pitching_role(row)
    if role and role not in found:
        found.append(role)
    return found


def _pitching_role(row: dict[str, Any]) -> str | None:
    if row.get("started_pitching"):
        return "SP"
    if (row.get("outs") or 0) > 0:
        return "RP"
    return None


def _started_at_position(row: dict[str, Any], position: str) -> bool:
    if position == "SP":
        return bool(row.get("started_pitching"))
    if position == "RP":
        return bool((row.get("outs") or 0) > 0 and not row.get("started_pitching"))
    return bool(row.get("fielding_games_started") or row.get("started_game"))


def list_multiple_uniforms(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    where, params = _type_filter_sql(type_groups)
    rows = conn.execute(
        f"""
        SELECT p.player_id, MAX(p.player_name) AS player_name,
               COUNT(DISTINCT p.team_id) AS team_count,
               COUNT(*) AS games_seen
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        WHERE {where} AND p.team_id IS NOT NULL
        GROUP BY p.player_id
        HAVING team_count >= 2
        ORDER BY team_count DESC, games_seen DESC, player_name
        """,
        params,
    ).fetchall()
    team_rows = conn.execute(
        f"""
        SELECT DISTINCT p.player_id, p.team_id
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        WHERE {where} AND p.team_id IS NOT NULL
        ORDER BY p.player_id, p.team_id
        """,
        params,
    ).fetchall()
    teams_by_player: dict[int, list[str]] = defaultdict(list)
    for row in team_rows:
        team = team_by_id(row["team_id"])
        label = team.abbreviation if team else str(row["team_id"])
        if label not in teams_by_player[row["player_id"]]:
            teams_by_player[row["player_id"]].append(label)
    uniforms = []
    for row in rows:
        item = dict(row)
        item["teams"] = teams_by_player.get(item["player_id"], [])
        item["team_labels"] = ", ".join(item["teams"])
        uniforms.append(item)
    return uniforms


def birthplace_label(profile: dict[str, Any] | None) -> str | None:
    if not profile:
        return None
    city = (profile.get("birth_city") or "").strip()
    state = (profile.get("birth_state_province") or "").strip()
    country = (profile.get("birth_country") or "").strip()
    locality = f"{city}, {state}" if city and state else city
    parts = [part for part in (locality, country) if part]
    return ", ".join(parts) if parts else None


def list_nationalities_seen(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    from tracker import db

    where, params = _type_filter_sql(type_groups)
    loaded = db.profiles_loaded(conn)
    rows = conn.execute(
        f"""
        SELECT p.player_id,
               MAX(p.player_name) AS player_name,
               pp.birth_country AS birth_country,
               COUNT(DISTINCT p.mlb_game_pk) AS games_seen
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        LEFT JOIN player_profiles pp ON pp.player_id = p.player_id
        WHERE {where}
        GROUP BY p.player_id, pp.birth_country
        ORDER BY player_name
        """,
        params,
    ).fetchall()
    game_rows = conn.execute(
        f"""
        SELECT pp.birth_country AS birth_country,
               COUNT(DISTINCT p.mlb_game_pk) AS game_count
        FROM player_game_stats p
        JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
        JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
        LEFT JOIN player_profiles pp ON pp.player_id = p.player_id
        WHERE {where}
        GROUP BY pp.birth_country
        """,
        params,
    ).fetchall()
    games_by_country = {
        country_label(row["birth_country"]): row["game_count"] for row in game_rows
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        item["country"] = country_label(item.get("birth_country"))
        grouped[item["country"]].append(item)
    countries = []
    for country, players in grouped.items():
        countries.append(
            {
                "country": country,
                "player_count": len(players),
                "game_count": games_by_country.get(country, 0),
                "is_usa": is_usa_country(country),
                "is_unknown": country == UNKNOWN_COUNTRY,
                "players": sorted(
                    players,
                    key=lambda player: (-player["games_seen"], player["player_name"]),
                ),
            }
        )
    countries.sort(
        key=lambda item: (
            item["is_unknown"],
            -item["player_count"],
            item["country"],
        )
    )
    known = [item for item in countries if not item["is_unknown"]]
    usa_count = sum(item["player_count"] for item in known if item["is_usa"])
    international_count = sum(item["player_count"] for item in known if not item["is_usa"])
    unknown_count = sum(item["player_count"] for item in countries if item["is_unknown"])
    return {
        "loaded": loaded,
        "country_count": len(known),
        "player_count": usa_count + international_count,
        "usa_count": usa_count,
        "international_count": international_count,
        "unknown_count": unknown_count,
        "countries": countries,
    }


def player_highlights(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
    *,
    depth_per_slot: int | None = None,
) -> dict[str, Any]:
    summaries = list_player_summaries(conn, type_groups)
    starters = sorted(
        [row for row in summaries if row["games_started_pitching"]],
        key=lambda row: (-row["games_started_pitching"], -row["games_seen"], row["player_name"]),
    )
    home_runs = list_home_runs(conn, type_groups)
    by_distance = sorted(
        home_runs,
        key=lambda event: (event.get("distance") is None, -(event.get("distance") or 0)),
    )
    per_slot = parse_depth_per_slot(depth_per_slot)
    depth_chart = list_depth_chart(conn, type_groups, per_slot=per_slot)
    return {
        "most_seen": summaries[:10],
        "starters": starters[:15],
        "home_runs": by_distance,
        "home_run_count": len(home_runs),
        "longest_home_runs": by_distance,
        "spray_count": sum(1 for event in home_runs if event.get("spray_x") is not None),
        "walkoff_home_runs": [event for event in by_distance if event.get("is_walkoff")],
        "batting_nights": list_batting_nights(conn, type_groups),
        "pitching_gems": list_pitching_gems(conn, type_groups),
        "multiple_uniforms": list_multiple_uniforms(conn, type_groups),
        "nationalities": list_nationalities_seen(conn, type_groups),
        "depth_chart": depth_chart,
        "depth_chart_count": sum(len(slot["players"]) for slot in depth_chart),
        "depth_chart_per_slot": per_slot,
    }


def honor_types_by_player(conn) -> dict[int, list[dict[str, str]]]:
    from tracker import db

    grouped: dict[int, list[str]] = defaultdict(list)
    for row in db.list_all_player_honors(conn):
        honor_type = row["honor_type"]
        if honor_type not in grouped[row["player_id"]]:
            grouped[row["player_id"]].append(honor_type)
    result: dict[int, list[dict[str, str]]] = {}
    for player_id, types in grouped.items():
        ordered = [honor for honor in HONOR_ORDER if honor in types]
        result[player_id] = [
            {"honor_type": honor, "label": HONOR_SHORT_LABELS[honor]}
            for honor in ordered
        ]
    return result


def honors_for_player(conn, player_id: int) -> list[dict[str, Any]]:
    from tracker import db

    grouped: dict[str, list[int]] = defaultdict(list)
    for row in db.list_player_honors(conn, player_id):
        grouped[row["honor_type"]].append(row["season"])
    honors = []
    for honor_type in HONOR_ORDER:
        seasons = sorted(set(grouped.get(honor_type) or []))
        if not seasons:
            continue
        honors.append(
            {
                "honor_type": honor_type,
                "label": HONOR_LABELS[honor_type],
                "seasons": seasons,
            }
        )
    return honors


def seen_honors(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    from tracker import db

    where, params = _type_filter_sql(type_groups)
    loaded = db.honors_loaded(conn)
    groups = []
    for honor_type in HONOR_ORDER:
        rows = conn.execute(
            f"""
            SELECT p.player_id,
                   MAX(p.player_name) AS player_name,
                   COUNT(DISTINCT p.mlb_game_pk) AS games_seen
            FROM player_honors h
            JOIN player_game_stats p ON p.player_id = h.player_id
            JOIN attended_games a ON a.mlb_game_pk = p.mlb_game_pk
            JOIN game_details d ON d.mlb_game_pk = p.mlb_game_pk
            WHERE h.honor_type = ? AND {where}
            GROUP BY p.player_id
            ORDER BY games_seen DESC, player_name
            """,
            [honor_type, *params],
        ).fetchall()
        players = [dict(row) for row in rows]
        groups.append(
            {
                "honor_type": honor_type,
                "label": HONOR_LABELS[honor_type],
                "count": len(players),
                "players": players,
            }
        )
    return {"loaded": loaded, "groups": groups}


def _sum_player_lines(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {key: 0 for key in PLAYER_COUNT_KEYS}
    totals["games_seen"] = len(rows)
    totals["games_started"] = sum(int(row.get("started_game") or 0) for row in rows)
    totals["games_started_pitching"] = sum(int(row.get("started_pitching") or 0) for row in rows)
    totals["fielding_games"] = sum(1 for row in rows if _fielded(row))
    totals["fielding_positions"] = ",".join(
        row["fielding_position"] for row in rows if row.get("fielding_position")
    )
    for row in rows:
        for key in PLAYER_COUNT_KEYS:
            totals[key] += int(row.get(key) or 0)
    _enrich_player_rates(totals)
    return totals
