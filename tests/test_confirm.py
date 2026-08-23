import pytest

from tracker import db
from tracker.enrich import AlreadyLoggedError, accept_candidate, reject_candidate, resolve_add, save_personal_only
from tests.conftest import client_from_fixtures, load_fixture


def test_accept_saves_and_enriches(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2024-06-15.json", {746946: "feed_746946.json"})
    result = resolve_add(db_conn, "2024-06-15", "Red Sox", "Yankees", notes="with Dad", client=client)
    assert result.ok
    assert len(result.pending.candidates) == 1

    game_id = accept_candidate(db_conn, result.pending, 746946, client=client)
    row = db.get_attended_with_details(db_conn, game_id)
    assert row["mlb_game_pk"] == 746946
    assert row["notes"] == "with Dad"
    assert row["home_score"] == 8
    assert row["away_score"] == 4
    assert row["home_starter"] == "Cooper Criswell"
    assert row["attendance"] == 36673
    assert (tmp_path / "cache" / "746946.json").exists()


def test_date_and_one_team_finds_the_game(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2024-06-15.json", {746946: "feed_746946.json"})
    by_home = resolve_add(db_conn, "2024-06-15", "Red Sox", "", client=client)
    by_away = resolve_add(db_conn, "2024-06-15", "", "Yankees", client=client)
    assert [item.game_pk for item in by_home.pending.candidates] == [746946]
    assert [item.game_pk for item in by_away.pending.candidates] == [746946]

    game_id = accept_candidate(db_conn, by_away.pending, 746946, client=client)
    row = db.get_attended_game(db_conn, game_id)
    assert row["home_team"] == "Boston Red Sox"
    assert row["away_team"] == "New York Yankees"
    assert row["date"] == "2024-06-15"


def test_both_teams_need_a_year(db_conn):
    result = resolve_add(db_conn, "", "Red Sox", "Yankees")
    assert not result.ok
    assert "year" in (result.error or "").lower()


def test_both_teams_with_year_lists_matchups(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2024-06-15.json")
    result = resolve_add(db_conn, "2024", "Red Sox", "Yankees", client=client)
    assert result.ok
    assert result.pending.year == 2024
    assert result.pending.date == "2024"
    assert [item.game_pk for item in result.pending.candidates] == [746946]


def test_year_alone_needs_both_teams(db_conn):
    result = resolve_add(db_conn, "2024", "Red Sox", "")
    assert not result.ok
    assert "both teams" in (result.error or "").lower()


def test_one_field_is_not_enough(db_conn):
    result = resolve_add(db_conn, "2024-06-15", "", "")
    assert not result.ok
    assert result.error


def test_reject_removes_candidate_and_does_not_write(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2018-07-09_phi.json")
    result = resolve_add(db_conn, "2018-07-09", "Mets", "Phillies", client=client)
    assert len(result.pending.candidates) == 2
    first_pk = result.pending.candidates[0].game_pk
    remaining = reject_candidate(result.pending, first_pk)
    assert [item.game_pk for item in remaining.candidates] == [
        item.game_pk for item in result.pending.candidates if item.game_pk != first_pk
    ]
    assert db.list_attended_games(db_conn) == []


def test_already_logged_blocks_second_accept(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2024-06-15.json", {746946: "feed_746946.json"})
    first = resolve_add(db_conn, "2024-06-15", "BOS", "NYY", client=client)
    accept_candidate(db_conn, first.pending, 746946, client=client)

    second = resolve_add(db_conn, "2024-06-15", "Red Sox", "Yankees", client=client)
    assert second.pending.candidates[0].already_logged_id is not None
    with pytest.raises(AlreadyLoggedError):
        accept_candidate(db_conn, second.pending, 746946, client=client)
    assert len(db.list_attended_games(db_conn)) == 1


def test_personal_only_and_later_confirm(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2024-06-15.json", {746946: "feed_746946.json"})
    result = resolve_add(db_conn, "2024-06-15", "Red Sox", "Yankees", notes="maybe this date", client=client)
    game_id = save_personal_only(db_conn, result.pending)
    row = db.get_attended_game(db_conn, game_id)
    assert row["mlb_game_pk"] is None

    rematch = resolve_add(
        db_conn,
        "2024-06-15",
        "Red Sox",
        "Yankees",
        notes=row["notes"],
        attended_game_id=game_id,
        client=client,
    )
    accept_candidate(db_conn, rematch.pending, 746946, client=client)
    updated = db.get_attended_with_details(db_conn, game_id)
    assert updated["mlb_game_pk"] == 746946
    assert updated["home_score"] == 8
    assert len(db.list_attended_games(db_conn)) == 1


def test_enrich_skips_unless_forced(db_conn, tmp_path):
    calls = {"feed": 0}

    def get_json(url, params=None):
        if "/schedule" in url:
            return load_fixture("schedule_2024-06-15.json")
        calls["feed"] += 1
        return load_fixture("feed_746946.json")

    from tracker.mlb import MlbClient
    from tracker.enrich import enrich_game

    client = MlbClient(get_json=get_json, cache_dir=tmp_path / "cache")
    result = resolve_add(db_conn, "2024-06-15", "BOS", "NYY", client=client)
    accept_candidate(db_conn, result.pending, 746946, client=client, force=True)
    assert calls["feed"] == 1

    enrich_game(db_conn, 746946, client=client, force=False)
    assert calls["feed"] == 1
    enrich_game(db_conn, 746946, client=client, force=True)
    assert calls["feed"] == 2


def test_update_notes(db_conn):
    game_id = db.insert_attended_game(
        db_conn,
        {
            "date": "2024-06-15",
            "home_team": "Red Sox",
            "away_team": "Yankees",
            "home_team_id": 111,
            "away_team_id": 147,
            "notes": None,
        },
    )
    db.update_attended_game(db_conn, game_id, {"notes": "with Dad"})
    assert db.get_attended_game(db_conn, game_id)["notes"] == "with Dad"
    db.update_attended_game(db_conn, game_id, {"notes": None})
    assert db.get_attended_game(db_conn, game_id)["notes"] is None


def test_delete_removes_attended_row_and_details(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2024-06-15.json", {746946: "feed_746946.json"})
    result = resolve_add(db_conn, "2024-06-15", "Red Sox", "Yankees", client=client)
    game_id = accept_candidate(db_conn, result.pending, 746946, client=client)
    deleted = db.delete_attended_game(db_conn, game_id)
    assert deleted["mlb_game_pk"] == 746946
    assert db.get_attended_game(db_conn, game_id) is None
    assert db.get_game_details(db_conn, 746946) is None
    assert db.delete_attended_game(db_conn, game_id) is None


def test_no_final_game_has_empty_candidates(db_conn, tmp_path):
    client = client_from_fixtures(tmp_path, "schedule_2024-06-15.json")
    result = resolve_add(db_conn, "2024-06-15", "Twins", "Athletics", client=client)
    assert result.ok
    assert result.pending.candidates == []
