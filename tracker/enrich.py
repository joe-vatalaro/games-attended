from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date as Date
from typing import Any

from tracker import db
from tracker.mlb import Candidate, MlbClient, MlbError, parse_game_details, parse_schedule_candidates
from tracker.teams import Team, TeamResolution, resolve_team

EMPTY_TEAM = TeamResolution(query="", matches=())
MIN_SEASON = 1876
FULL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_ONLY_RE = re.compile(r"^\d{4}$")


class AlreadyLoggedError(Exception):
    def __init__(self, game_pk: int, attended_id: int) -> None:
        super().__init__(f"Game {game_pk} is already logged as attended game {attended_id}")
        self.game_pk = game_pk
        self.attended_id = attended_id


class AmbiguousAddError(Exception):
    def __init__(self, home: TeamResolution, away: TeamResolution) -> None:
        super().__init__("Team names were unknown or ambiguous")
        self.home = home
        self.away = away


@dataclass
class PendingAdd:
    date: str = ""
    home_team: str = ""
    away_team: str = ""
    home_team_id: int | None = None
    away_team_id: int | None = None
    notes: str = ""
    venue: str = ""
    seat_section: str = ""
    seat_row: str = ""
    seat_seat: str = ""
    year: int | None = None
    candidates: list[Candidate] = field(default_factory=list)
    attended_game_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingAdd:
        known = {item.name for item in cls.__dataclass_fields__.values()}
        payload = {key: value for key, value in data.items() if key in known}
        payload["candidates"] = [Candidate.from_dict(item) for item in payload.get("candidates") or []]
        return cls(**payload)


@dataclass
class ResolveResult:
    pending: PendingAdd | None
    home: TeamResolution
    away: TeamResolution
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.pending is not None


def parse_year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    max_year = Date.today().year + 1
    if year < MIN_SEASON or year > max_year:
        return None
    return year


def parse_date_input(value: Any) -> tuple[str | None, int | None, str | None]:
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return None, None, None
    if FULL_DATE_RE.match(raw):
        try:
            Date.fromisoformat(raw)
        except ValueError:
            return None, None, "Date must look like 2024-06-15."
        return raw, None, None
    if YEAR_ONLY_RE.match(raw):
        season = parse_year(raw)
        if season is None:
            return None, None, "Year must be a season like 2024."
        return None, season, None
    return None, None, "Use a full date like 2024-06-15, or a year like 2024."


def resolve_add(
    conn,
    date: str,
    home_team: str,
    away_team: str,
    *,
    notes: str = "",
    venue: str = "",
    seat_section: str = "",
    seat_row: str = "",
    seat_seat: str = "",
    attended_game_id: int | None = None,
    client: MlbClient | None = None,
) -> ResolveResult:
    date = (date or "").strip()
    home_team = (home_team or "").strip()
    away_team = (away_team or "").strip()
    full_date, season, date_error = parse_date_input(date)
    has_home = bool(home_team)
    has_away = bool(away_team)
    home = resolve_team(home_team) if has_home else EMPTY_TEAM
    away = resolve_team(away_team) if has_away else EMPTY_TEAM

    if date_error:
        return ResolveResult(pending=None, home=home, away=away, error=date_error)
    if full_date:
        if not has_home and not has_away:
            return ResolveResult(
                pending=None,
                home=home,
                away=away,
                error="Add a home team or away team to go with that date.",
            )
    elif season:
        if not has_home or not has_away:
            return ResolveResult(
                pending=None,
                home=home,
                away=away,
                error="A year needs both teams. Use a full date to search one team.",
            )
    elif has_home and has_away:
        return ResolveResult(
            pending=None,
            home=home,
            away=away,
            error="Add a year (or a full date) to list that season’s matchups.",
        )
    else:
        return ResolveResult(
            pending=None,
            home=home,
            away=away,
            error="Enter a date plus a team, or both teams plus a year.",
        )
    if has_home and not home.unique:
        return ResolveResult(pending=None, home=home, away=away)
    if has_away and not away.unique:
        return ResolveResult(pending=None, home=home, away=away)

    client = client or MlbClient()
    home_id = home.team.id if home.team else None
    away_id = away.team.id if away.team else None

    if full_date and has_home and has_away:
        schedule = client.fetch_schedule(date=full_date, team_id=home_id)
        candidates = parse_schedule_candidates(schedule, home_team_id=home_id, away_team_id=away_id)
    elif full_date:
        team_id = home_id if has_home else away_id
        schedule = client.fetch_schedule(date=full_date, team_id=team_id)
        candidates = parse_schedule_candidates(schedule, either_team_id=team_id)
    else:
        schedule = client.fetch_schedule(
            start_date=f"{season}-01-01",
            end_date=f"{season}-12-31",
            team_id=home_id,
            opponent_id=away_id,
            season=season,
        )
        pair = {home_id, away_id}
        candidates = [
            game
            for game in parse_schedule_candidates(schedule)
            if {game.home_team_id, game.away_team_id} == pair
        ]

    for candidate in candidates:
        existing = db.get_attended_by_pk(conn, candidate.game_pk)
        if existing:
            candidate.already_logged_id = existing["id"]

    pending = PendingAdd(
        date=date,
        home_team=home_team,
        away_team=away_team,
        home_team_id=home_id,
        away_team_id=away_id,
        notes=notes,
        venue=venue,
        seat_section=seat_section,
        seat_row=seat_row,
        seat_seat=seat_seat,
        year=season,
        candidates=candidates,
        attended_game_id=attended_game_id,
    )
    return ResolveResult(pending=pending, home=home, away=away)


def personal_fields(pending: PendingAdd, candidate: Candidate | None = None) -> dict[str, Any]:
    full_date, _, _ = parse_date_input(pending.date)
    return {
        "date": full_date or (candidate.official_date if candidate else ""),
        "home_team": pending.home_team or (candidate.home_team if candidate else ""),
        "away_team": pending.away_team or (candidate.away_team if candidate else ""),
        "home_team_id": pending.home_team_id or (candidate.home_team_id if candidate else None),
        "away_team_id": pending.away_team_id or (candidate.away_team_id if candidate else None),
        "venue": pending.venue or (candidate.venue_name if candidate else None) or None,
        "seat_section": pending.seat_section or None,
        "seat_row": pending.seat_row or None,
        "seat_seat": pending.seat_seat or None,
        "notes": pending.notes or None,
        "needs_review": 0,
    }


def accept_candidate(
    conn,
    pending: PendingAdd,
    game_pk: int,
    *,
    client: MlbClient | None = None,
    force: bool = True,
) -> int:
    candidate = next((item for item in pending.candidates if item.game_pk == game_pk), None)
    if candidate is None:
        raise ValueError(f"game_pk {game_pk} is not in the pending candidate list")

    existing = db.get_attended_by_pk(conn, game_pk)
    if existing and existing["id"] != pending.attended_game_id:
        raise AlreadyLoggedError(game_pk, existing["id"])
    if candidate.already_logged_id and candidate.already_logged_id != pending.attended_game_id:
        raise AlreadyLoggedError(game_pk, candidate.already_logged_id)

    fields = personal_fields(pending, candidate)
    fields["mlb_game_pk"] = game_pk
    fields["date"] = candidate.official_date or fields["date"]
    fields["home_team"] = candidate.home_team
    fields["away_team"] = candidate.away_team
    fields["home_team_id"] = candidate.home_team_id
    fields["away_team_id"] = candidate.away_team_id

    if pending.attended_game_id:
        db.update_attended_game(conn, pending.attended_game_id, fields)
        game_id = pending.attended_game_id
    else:
        game_id = db.insert_attended_game(conn, fields)

    enrich_game(conn, game_pk, client=client, force=force)
    return game_id


def reject_candidate(pending: PendingAdd, game_pk: int) -> PendingAdd:
    remaining = [item for item in pending.candidates if item.game_pk != game_pk]
    pending.candidates = remaining
    return pending


def save_personal_only(conn, pending: PendingAdd) -> int:
    fields = personal_fields(pending)
    fields["mlb_game_pk"] = None
    if pending.attended_game_id:
        db.update_attended_game(conn, pending.attended_game_id, fields)
        return pending.attended_game_id
    return db.insert_attended_game(conn, fields)


def enrich_game(
    conn,
    game_pk: int,
    *,
    client: MlbClient | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    existing = db.get_game_details(conn, game_pk)
    if existing and existing.get("fetched_at") and not force:
        return existing
    client = client or MlbClient()
    feed = client.fetch_feed(game_pk, force=force)
    details = parse_game_details(feed)
    db.upsert_game_details(conn, details)
    attended = db.get_attended_by_pk(conn, game_pk)
    if attended and details.get("venue_name") and not attended.get("venue"):
        db.update_attended_game(conn, attended["id"], {"venue": details["venue_name"]})
    return details


def enrich_all(
    conn,
    *,
    client: MlbClient | None = None,
    force: bool = False,
    game_id: int | None = None,
) -> list[dict[str, Any]]:
    client = client or MlbClient()
    if game_id is not None:
        game = db.get_attended_game(conn, game_id)
        rows = [game] if game else []
    else:
        rows = db.list_attended_games(conn)

    results = []
    for row in rows:
        game_pk = row.get("mlb_game_pk")
        if not game_pk:
            results.append({"id": row["id"], "status": "skipped", "reason": "unmatched"})
            continue
        before = db.get_game_details(conn, game_pk)
        if before and before.get("fetched_at") and not force:
            results.append({"id": row["id"], "game_pk": game_pk, "status": "skipped", "reason": "already enriched"})
            continue
        try:
            enrich_game(conn, game_pk, client=client, force=force)
            results.append({"id": row["id"], "game_pk": game_pk, "status": "enriched"})
        except MlbError as exc:
            results.append({"id": row["id"], "game_pk": game_pk, "status": "error", "reason": str(exc)})
    return results


def format_team_choices(resolution: TeamResolution) -> list[Team]:
    return list(resolution.matches)
