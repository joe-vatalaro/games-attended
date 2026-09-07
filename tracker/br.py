from __future__ import annotations

from datetime import date, datetime
from typing import Any

from tracker.teams import resolve_team, team_by_id

BR_ORIGIN = "https://www.baseball-reference.com"
SAVANT_ORIGIN = "https://baseballsavant.mlb.com"

# Baseball-Reference box codes differ from current MLB abbreviations.
BR_TEAM_CODES = {
    109: "ARI",
    133: "OAK",
    144: "ATL",
    110: "BAL",
    111: "BOS",
    112: "CHN",
    145: "CHA",
    113: "CIN",
    114: "CLE",
    115: "COL",
    116: "DET",
    117: "HOU",
    118: "KCA",
    108: "ANA",
    119: "LAN",
    146: "MIA",
    158: "MIL",
    142: "MIN",
    121: "NYN",
    147: "NYA",
    143: "PHI",
    134: "PIT",
    135: "SDN",
    137: "SFN",
    136: "SEA",
    138: "SLN",
    139: "TBA",
    140: "TEX",
    141: "TOR",
    120: "WAS",
}


def baseball_reference_player_url(player_id: int | None) -> str | None:
    if not player_id:
        return None
    return f"{BR_ORIGIN}/redirect.fcgi?player=1&mlb_ID={int(player_id)}"


def baseball_savant_player_url(player_id: int | None) -> str | None:
    if not player_id:
        return None
    return f"{SAVANT_ORIGIN}/savant-player/{int(player_id)}"


def baseball_savant_game_url(game: dict[str, Any] | None) -> str | None:
    if not game:
        return None
    game_pk = game.get("mlb_game_pk")
    if not game_pk:
        return None
    return f"{SAVANT_ORIGIN}/gamefeed?gamePk={int(game_pk)}"


def baseball_reference_game_url(game: dict[str, Any] | None) -> str | None:
    if not game:
        return None
    parsed = _parse_game_date(game.get("official_date") or game.get("date"))
    if parsed is None:
        return None
    team_id = game.get("home_team_id")
    if not team_id and game.get("home_team"):
        resolved = resolve_team(str(game["home_team"]))
        team_id = resolved.team.id if resolved.team else None
    code = _br_team_code(team_id, parsed.year)
    if not code:
        return None
    suffix = _box_suffix(game.get("game_number"))
    stamp = parsed.strftime("%Y%m%d")
    return f"{BR_ORIGIN}/boxes/{code}/{code}{stamp}{suffix}.shtml"


def _br_team_code(team_id: int | None, year: int) -> str | None:
    if not team_id:
        return None
    if int(team_id) == 146:
        return "FLA" if year < 2012 else "MIA"
    if int(team_id) == 120:
        return "MON" if year < 2005 else "WAS"
    if int(team_id) in BR_TEAM_CODES:
        return BR_TEAM_CODES[int(team_id)]
    team = team_by_id(int(team_id))
    return team.abbreviation if team else None


def _box_suffix(game_number: Any) -> str:
    try:
        number = int(game_number)
    except (TypeError, ValueError):
        number = 1
    return str(max(number, 1) - 1)


def _parse_game_date(raw: Any) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    text = str(raw)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
