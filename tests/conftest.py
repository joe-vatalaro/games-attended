from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracker.db import connect
from tracker.mlb import MlbClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_conn(tmp_path):
    conn = connect(tmp_path / "games.db")
    yield conn
    conn.close()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def client_from_fixtures(tmp_path, schedule_name: str, feeds: dict[int, str] | None = None) -> MlbClient:
    feeds = feeds or {}

    def get_json(url: str, params=None):
        if "/schedule" in url:
            return load_fixture(schedule_name)
        if "/feed/live" in url:
            game_pk = int(url.split("/game/")[1].split("/")[0])
            if game_pk in feeds:
                return load_fixture(feeds[game_pk])
            raise AssertionError(f"No feed fixture for {game_pk}")
        raise AssertionError(url)

    return MlbClient(get_json=get_json, cache_dir=tmp_path / "cache")
