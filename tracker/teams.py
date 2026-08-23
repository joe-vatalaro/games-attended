from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tracker.paths import ALIASES_PATH

_PUNCT = re.compile(r"[.']+")
_SPACES = re.compile(r"\s+")


def normalize_team_query(value: str) -> str:
    cleaned = _PUNCT.sub("", value.strip().lower())
    return _SPACES.sub(" ", cleaned)


@dataclass(frozen=True)
class Team:
    id: int
    name: str
    abbreviation: str


@dataclass(frozen=True)
class TeamResolution:
    query: str
    matches: tuple[Team, ...]

    @property
    def unique(self) -> bool:
        return len(self.matches) == 1

    @property
    def team(self) -> Team | None:
        if not self.unique:
            return None
        return self.matches[0]


@lru_cache(maxsize=1)
def load_team_catalog(path: str | None = None) -> dict:
    catalog_path = Path(path) if path else ALIASES_PATH
    return json.loads(catalog_path.read_text())


def all_teams(path: str | None = None) -> list[Team]:
    catalog = load_team_catalog(path)
    return [
        Team(id=row["id"], name=row["name"], abbreviation=row["abbreviation"])
        for row in catalog["teams"]
    ]


def team_by_id(team_id: int, path: str | None = None) -> Team | None:
    for team in all_teams(path):
        if team.id == team_id:
            return team
    return None


def resolve_team(query: str, path: str | None = None) -> TeamResolution:
    normalized = normalize_team_query(query)
    if not normalized:
        return TeamResolution(query=query, matches=())

    catalog = load_team_catalog(path)
    teams_by_id = {
        row["id"]: Team(id=row["id"], name=row["name"], abbreviation=row["abbreviation"])
        for row in catalog["teams"]
    }

    ambiguous = catalog.get("ambiguous", {})
    if normalized in ambiguous:
        matches = tuple(teams_by_id[team_id] for team_id in ambiguous[normalized] if team_id in teams_by_id)
        return TeamResolution(query=query, matches=matches)

    matches: list[Team] = []
    for row in catalog["teams"]:
        aliases = {normalize_team_query(alias) for alias in row["aliases"]}
        aliases.add(normalize_team_query(row["name"]))
        aliases.add(normalize_team_query(row["abbreviation"]))
        if normalized in aliases:
            matches.append(teams_by_id[row["id"]])

    return TeamResolution(query=query, matches=tuple(matches))
