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


def load_parks(path: Path | None = None) -> list[dict[str, Any]]:
    parks_path = path or PARKS_PATH
    return json.loads(parks_path.read_text())["parks"]


def parse_report_type_groups(values: list[str] | None, *, explicit: bool = False) -> list[str]:
    if not explicit and not values:
        return list(DEFAULT_REPORT_TYPE_GROUPS)
    return [value for value in values or [] if value in REPORT_TYPE_GROUPS]


def parse_min_pa(value: str | int | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


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
               d.is_walkoff, d.is_extra_innings, d.is_no_hitter
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
        "by_year": _by_year(confirmed),
        "notable": _notable(confirmed),
        "unmatched": unmatched,
        "players": player_highlights(conn, selected),
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
    return item


def event_hit_distance(event: dict[str, Any]) -> float | None:
    raw = event.get("extra_json")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None
    distance = data.get("totalDistance")
    if distance is None or distance == "":
        return None
    return float(distance)


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
               COALESCE(d.official_date, a.date) AS game_date, d.venue_name
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
        item["distance"] = event_hit_distance(item)
        events.append(item)
    return events


def player_highlights(
    conn,
    type_groups: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    summaries = list_player_summaries(conn, type_groups)
    starters = sorted(
        [row for row in summaries if row["games_started_pitching"]],
        key=lambda row: (-row["games_started_pitching"], -row["games_seen"], row["player_name"]),
    )
    home_runs = list_home_runs(conn, type_groups)
    longest = sorted(
        [event for event in home_runs if event.get("distance") is not None],
        key=lambda event: event["distance"],
        reverse=True,
    )
    return {
        "most_seen": summaries[:10],
        "starters": starters[:15],
        "home_runs": home_runs,
        "home_run_count": len(home_runs),
        "longest_home_runs": longest[:5],
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
    for row in rows:
        for key in PLAYER_COUNT_KEYS:
            totals[key] += int(row.get(key) or 0)
    _enrich_player_rates(totals)
    return totals
