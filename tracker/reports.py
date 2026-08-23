from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from tracker.mlb import OTHER_GAME_TYPES, POSTSEASON_TYPES, SPRING_TYPES
from tracker.paths import PARKS_PATH
from tracker.teams import team_by_id

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
        "longest_shortest": _longest_shortest(confirmed),
        "stadiums": _stadiums(confirmed, parks_path),
        "attendance": _attendance(confirmed),
        "by_year": _by_year(confirmed),
        "notable": _notable(confirmed),
        "unmatched": unmatched,
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
