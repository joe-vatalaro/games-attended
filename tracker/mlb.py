from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from tracker.paths import CACHE_DIR, ensure_data_dirs

MLB_BASE = "https://statsapi.mlb.com"
SCHEDULE_HYDRATE = "decisions,linescore,weather,venue"
POSTSEASON_TYPES = frozenset({"F", "D", "L", "W"})
POSTSEASON_NAMES = {
    "F": "Wild Card Series",
    "D": "Division Series",
    "L": "League Championship Series",
    "W": "World Series",
}


class MlbError(Exception):
    pass


@dataclass
class Candidate:
    game_pk: int
    official_date: str
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    venue_name: str
    winning_pitcher: str | None
    losing_pitcher: str | None
    game_number: int
    doubleheader: str
    status: str
    already_logged_id: int | None = None
    game_type: str | None = None
    series_description: str | None = None
    series_game_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        known = {item.name for item in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in known})

    @property
    def series_label(self) -> str | None:
        return playoff_label(
            self.game_type,
            series_description=self.series_description,
            series_game_number=self.series_game_number,
        )


def default_get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MlbError(f"MLB API request failed: {exc}") from exc
    return response.json()


class MlbClient:
    def __init__(
        self,
        get_json: Callable[..., dict[str, Any]] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.get_json = get_json or default_get_json
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR

    def fetch_schedule(
        self,
        date: str | None = None,
        team_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        opponent_id: int | None = None,
        season: int | None = None,
        game_pk: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "sportId": 1,
            "hydrate": SCHEDULE_HYDRATE,
        }
        if game_pk is not None:
            params["gamePk"] = game_pk
        if date:
            params["date"] = date
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date
        if team_id is not None:
            params["teamId"] = team_id
        if opponent_id is not None:
            params["opponentId"] = opponent_id
        if season is not None:
            params["season"] = season
        return self.get_json(f"{MLB_BASE}/api/v1/schedule", params=params)

    def fetch_feed(self, game_pk: int, force: bool = False) -> dict[str, Any]:
        cached = self.cache_path(game_pk)
        if cached.exists() and not force:
            return json.loads(cached.read_text())
        payload = self.get_json(f"{MLB_BASE}/api/v1.1/game/{game_pk}/feed/live")
        self.write_cache(game_pk, payload)
        return payload

    def cache_path(self, game_pk: int) -> Path:
        return self.cache_dir / f"{game_pk}.json"

    def write_cache(self, game_pk: int, payload: dict[str, Any]) -> None:
        ensure_data_dirs()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path(game_pk).write_text(json.dumps(payload))


def playoff_label(
    game_type: str | None,
    *,
    series_description: str | None = None,
    series_game_number: int | None = None,
) -> str | None:
    if game_type not in POSTSEASON_TYPES:
        return None
    series = (series_description or "").strip()
    if series and series_game_number:
        return f"{series} Game {series_game_number}"
    if series:
        return series
    name = POSTSEASON_NAMES[game_type]
    if series_game_number:
        return f"{name} Game {series_game_number}"
    return name


def series_fields_from_schedule(schedule: dict[str, Any], game_pk: int) -> dict[str, Any]:
    for day in schedule.get("dates", []):
        for game in day.get("games", []):
            if int(game.get("gamePk") or 0) != game_pk:
                continue
            return {
                "game_type": game.get("gameType"),
                "series_description": (game.get("seriesDescription") or "").strip() or None,
                "series_game_number": _maybe_int(game.get("seriesGameNumber")),
            }
    return {}


def parse_schedule_candidates(
    schedule: dict[str, Any],
    home_team_id: int | None = None,
    away_team_id: int | None = None,
    either_team_id: int | None = None,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for day in schedule.get("dates", []):
        for game in day.get("games", []):
            if not _is_final(game):
                continue
            teams = game.get("teams", {})
            home = teams.get("home", {}).get("team", {})
            away = teams.get("away", {}).get("team", {})
            if home_team_id is not None and home.get("id") != home_team_id:
                continue
            if away_team_id is not None and away.get("id") != away_team_id:
                continue
            if either_team_id is not None and either_team_id not in {home.get("id"), away.get("id")}:
                continue
            decisions = game.get("decisions") or {}
            venue = game.get("venue") or {}
            candidates.append(
                Candidate(
                    game_pk=int(game["gamePk"]),
                    official_date=game.get("officialDate") or day.get("date"),
                    home_team_id=int(home["id"]),
                    away_team_id=int(away["id"]),
                    home_team=home.get("name", ""),
                    away_team=away.get("name", ""),
                    home_score=_maybe_int(teams.get("home", {}).get("score")),
                    away_score=_maybe_int(teams.get("away", {}).get("score")),
                    venue_name=venue.get("name", ""),
                    winning_pitcher=_person_name(decisions.get("winner")),
                    losing_pitcher=_person_name(decisions.get("loser")),
                    game_number=int(game.get("gameNumber") or 1),
                    doubleheader=game.get("doubleHeader") or "N",
                    status=(game.get("status") or {}).get("detailedState", "Final"),
                    game_type=game.get("gameType"),
                    series_description=(game.get("seriesDescription") or "").strip() or None,
                    series_game_number=_maybe_int(game.get("seriesGameNumber")),
                )
            )
    candidates.sort(key=lambda item: (item.official_date or "", item.game_number))
    return candidates


def parse_game_details(feed: dict[str, Any]) -> dict[str, Any]:
    game_data = feed.get("gameData") or {}
    live = feed.get("liveData") or {}
    game = game_data.get("game") or {}
    datetime_info = game_data.get("datetime") or {}
    status = game_data.get("status") or {}
    teams = game_data.get("teams") or {}
    venue = game_data.get("venue") or {}
    weather = game_data.get("weather") or {}
    game_info = game_data.get("gameInfo") or {}
    flags = game_data.get("flags") or {}
    linescore = live.get("linescore") or {}
    decisions = live.get("decisions") or {}
    boxscore = live.get("boxscore") or {}

    home_team = teams.get("home") or {}
    away_team = teams.get("away") or {}
    home_id = _maybe_int(home_team.get("id"))
    away_id = _maybe_int(away_team.get("id"))
    home_score = _maybe_int((linescore.get("teams") or {}).get("home", {}).get("runs"))
    away_score = _maybe_int((linescore.get("teams") or {}).get("away", {}).get("runs"))
    innings = _inning_count(linescore)
    home_hits = _maybe_int((linescore.get("teams") or {}).get("home", {}).get("hits"))
    away_hits = _maybe_int((linescore.get("teams") or {}).get("away", {}).get("hits"))

    winning_team_id = None
    if home_score is not None and away_score is not None:
        if home_score > away_score:
            winning_team_id = home_id
        elif away_score > home_score:
            winning_team_id = away_id

    no_hitter = bool(
        flags.get("noHitter")
        or flags.get("homeTeamNoHitter")
        or flags.get("awayTeamNoHitter")
        or home_hits == 0
        or away_hits == 0
    )

    return {
        "mlb_game_pk": int(game["pk"]),
        "official_date": datetime_info.get("officialDate"),
        "season": _maybe_int(game.get("season")),
        "game_type": game.get("type"),
        "series_description": None,
        "series_game_number": None,
        "venue_id": _maybe_int(venue.get("id")),
        "venue_name": venue.get("name"),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_score": home_score,
        "away_score": away_score,
        "winning_team_id": winning_team_id,
        "home_starter": _starting_pitcher(boxscore, "home"),
        "away_starter": _starting_pitcher(boxscore, "away"),
        "winning_pitcher": _person_name(decisions.get("winner")),
        "losing_pitcher": _person_name(decisions.get("loser")),
        "save_pitcher": _person_name(decisions.get("save")),
        "attendance": _maybe_int(game_info.get("attendance")),
        "duration_minutes": _maybe_int(game_info.get("gameDurationMinutes")),
        "innings": innings,
        "weather_condition": weather.get("condition"),
        "weather_temp": weather.get("temp"),
        "weather_wind": weather.get("wind"),
        "linescore_json": json.dumps(linescore.get("innings") or []),
        "is_walkoff": int(_is_walkoff(live, winning_team_id, home_id)),
        "is_extra_innings": int(bool(innings and innings > 9)),
        "is_no_hitter": int(no_hitter),
        "status": status.get("detailedState"),
    }


def _is_final(game: dict[str, Any]) -> bool:
    status = game.get("status") or {}
    return status.get("detailedState") == "Final" or status.get("codedGameState") == "F"


def _person_name(person: Any) -> str | None:
    if not isinstance(person, dict):
        return None
    return person.get("fullName")


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _inning_count(linescore: dict[str, Any]) -> int | None:
    innings = linescore.get("innings") or []
    if innings:
        return len(innings)
    return _maybe_int(linescore.get("currentInning"))


def _starting_pitcher(boxscore: dict[str, Any], side: str) -> str | None:
    team = (boxscore.get("teams") or {}).get(side) or {}
    players = team.get("players") or {}
    for pitcher_id in team.get("pitchers") or []:
        player = players.get(f"ID{pitcher_id}") or {}
        games_started = (
            (player.get("stats") or {}).get("pitching") or {}
        ).get("gamesStarted")
        if games_started:
            return (player.get("person") or {}).get("fullName")
    if team.get("pitchers"):
        first = team["pitchers"][0]
        player = players.get(f"ID{first}") or {}
        return (player.get("person") or {}).get("fullName")
    return None


def _is_walkoff(
    live: dict[str, Any],
    winning_team_id: int | None,
    home_id: int | None,
) -> bool:
    if winning_team_id is None or home_id is None or winning_team_id != home_id:
        return False
    plays = ((live.get("plays") or {}).get("allPlays")) or []
    for play in plays:
        about = play.get("about") or {}
        result = play.get("result") or {}
        if about.get("isWalkOff"):
            return True
        description = str(result.get("description") or "").lower()
        event = str(result.get("event") or "").lower()
        if "walk-off" in description or "walk-off" in event:
            return True
    scoring = [play for play in plays if (play.get("about") or {}).get("isScoringPlay")]
    if not scoring:
        return False
    last = scoring[-1].get("about") or {}
    return last.get("halfInning") == "bottom" and int(last.get("inning") or 0) >= 9
