import json

from tracker import db
from tracker.app import create_app
from tracker.enrich import apply_feed_tables, reparse_cache
from tracker.mlb import parse_game_details, parse_game_events, parse_player_game_stats
from tracker.reports import build_report, list_player_summaries, player_page
from tests.conftest import load_fixture


def _seed_player_game(conn, feed=None, *, series_description=None, series_game_number=None):
    feed = feed or load_fixture("feed_players_hr.json")
    details = parse_game_details(feed)
    if series_description:
        details["series_description"] = series_description
        details["series_game_number"] = series_game_number
    game_id = db.insert_attended_game(
        conn,
        {
            "date": details["official_date"],
            "home_team": "New York Yankees",
            "away_team": "Toronto Blue Jays",
            "home_team_id": details["home_team_id"],
            "away_team_id": details["away_team_id"],
            "mlb_game_pk": details["mlb_game_pk"],
        },
    )
    apply_feed_tables(conn, feed, details)
    return game_id, details


def test_reparse_writes_player_tables_and_keeps_series(db_conn, tmp_path):
    feed = load_fixture("feed_players_hr.json")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "900001.json").write_text(json.dumps(feed))
    details = parse_game_details(feed)
    details["series_description"] = "AL Division Series"
    details["series_game_number"] = 2
    db.insert_attended_game(
        db_conn,
        {
            "date": "2024-07-04",
            "home_team": "New York Yankees",
            "away_team": "Toronto Blue Jays",
            "mlb_game_pk": 900001,
        },
    )
    db.upsert_game_details(db_conn, details)

    results = reparse_cache(db_conn, cache_dir=cache)
    assert [row["game_pk"] for row in results] == [900001]
    kept = db.get_game_details(db_conn, 900001)
    assert kept["series_description"] == "AL Division Series"
    assert kept["series_game_number"] == 2
    players = {row["player_id"]: row for row in db.list_player_game_stats(db_conn, 900001)}
    assert players[111111]["hr"] == 1
    events = db.list_game_events(db_conn, 900001, event_type="home_run")
    assert events[0]["batter_name"] == "Nathan Lukes"


def test_reparse_skips_cache_without_attended_game(db_conn, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "900001.json").write_text(json.dumps(load_fixture("feed_players_hr.json")))
    assert reparse_cache(db_conn, cache_dir=cache) == []


def test_player_summaries_and_report_highlights(db_conn):
    _seed_player_game(db_conn)
    summaries = {row["player_id"]: row for row in list_player_summaries(db_conn)}
    assert summaries[111111]["games_seen"] == 1
    assert summaries[111111]["hr"] == 1
    assert summaries[222222]["games_started_pitching"] == 1
    report = build_report(db_conn)
    assert report["players"]["home_run_count"] == 1
    assert report["players"]["longest_home_runs"][0]["distance"] == 412.0
    assert report["players"]["most_seen"][0]["player_id"] in summaries
    page = player_page(db_conn, 111111)
    assert page["totals"]["hr"] == 1
    assert page["totals"]["slash"].startswith(".")


def test_game_page_shows_lineup_and_home_run(db_conn, tmp_path):
    game_id, _ = _seed_player_game(db_conn)
    app = create_app(db_path=tmp_path / "games.db", secret_key="test")
    with app.test_client() as flask_client:
        html = flask_client.get(f"/games/{game_id}").get_data(as_text=True)
    assert "Nathan Lukes" in html
    assert "Aaron Judge" in html
    assert "Nathan Lukes homers (8)" in html
    assert "top 3" in html
    assert "/players/111111" in html


def test_players_and_player_pages(db_conn, tmp_path):
    _seed_player_game(db_conn)
    app = create_app(db_path=tmp_path / "games.db", secret_key="test")
    with app.test_client() as flask_client:
        index = flask_client.get("/players").get_data(as_text=True)
        detail = flask_client.get("/players/111111").get_data(as_text=True)
        report = flask_client.get("/report").get_data(as_text=True)
    assert "Nathan Lukes" in index
    assert "Aaron Judge" in index
    assert "2-4" in detail
    assert "1 HR" in detail
    assert "Most seen players" in report
    assert "Home runs seen" in report
    assert "412" in report


def test_apply_feed_tables_matches_parsers(db_conn):
    feed = load_fixture("feed_players_hr.json")
    details = parse_game_details(feed)
    apply_feed_tables(db_conn, feed, details)
    stored = db.list_player_game_stats(db_conn, 900001)
    assert len(stored) == len(parse_player_game_stats(feed))
    assert len(db.list_game_events(db_conn, 900001)) == len(parse_game_events(feed))
