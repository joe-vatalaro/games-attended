import json

from tracker import db
from tracker.app import create_app
from tracker.enrich import apply_feed_tables, reparse_cache
from tracker.mlb import parse_game_details, parse_game_events, parse_player_game_stats
from tracker.reports import build_report, game_boxscore, list_player_summaries, parse_min_count, parse_min_pa, player_page
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
    assert summaries[111111]["batting_games"] == 1
    assert summaries[111111]["avg"] == ".500"
    assert summaries[111111]["fielding_games"] == 1
    assert summaries[111111]["putouts"] == 2
    assert summaries[111111]["fielding_position"] == "RF"
    assert summaries[111111]["fpct"] == "1.000"
    assert summaries[222222]["games_started_pitching"] == 1
    assert summaries[222222]["pitching_games"] == 1
    assert summaries[222222]["wins"] == 1
    assert summaries[222222]["innings_pitched"] == "6.0"
    report = build_report(db_conn)
    assert report["players"]["home_run_count"] == 1
    assert report["players"]["home_runs"][0]["distance"] == 412.0
    assert report["players"]["home_runs"][0]["exit_velo"] == 108.0
    assert report["players"]["home_runs"][0]["launch_angle"] == 28.0
    assert report["players"]["home_runs"][0]["spray_x"] == 200.0
    assert report["players"]["home_runs"][0]["is_walkoff"] is False
    assert report["players"]["longest_home_runs"][0]["distance"] == 412.0
    assert report["players"]["most_seen"][0]["player_id"] in summaries
    assert report["players"]["batting_nights"] == []
    assert report["players"]["pitching_gems"] == []
    assert report["players"]["multiple_uniforms"] == []
    assert report["extremes"]["highest_scoring"]["home_score"] == 5
    assert report["extremes"]["hottest"]["temp_f"] == 82
    assert report["extremes"]["shutouts"] == 0
    page = player_page(db_conn, 111111)
    assert page["totals"]["hr"] == 1
    assert page["totals"]["slash"].startswith(".")
    assert page["totals"]["putouts"] == 2
    assert page["totals"]["fielding_games"] == 1


def test_game_page_shows_lineup_and_home_run(db_conn, tmp_path):
    game_id, _ = _seed_player_game(db_conn)
    app = create_app(db_path=tmp_path / "games.db", secret_key="test")
    with app.test_client() as flask_client:
        html = flask_client.get(f"/games/{game_id}").get_data(as_text=True)
    assert "Nathan Lukes" in html
    assert "Aaron Judge" in html
    assert "Pinch Hitter" in html
    assert "Linescore" in html
    assert "Batting" in html
    assert "Pitching" in html
    assert "6.0" in html
    assert "Nathan Lukes homers (8)" in html
    assert "top 3" in html
    assert "/players/111111" in html
    assert "boxes/NYA/NYA202407040.shtml" in html
    assert "gamefeed?gamePk=900001" in html


def test_game_boxscore_includes_bench_and_linescore(db_conn):
    game_id, details = _seed_player_game(db_conn)
    game = db.get_attended_with_details(db_conn, game_id)
    stats = db.list_player_game_stats(db_conn, details["mlb_game_pk"])
    box = game_boxscore(game, stats)
    away_names = [row["player_name"] for row in box["batting"]["away"]["rows"]]
    assert away_names == ["Nathan Lukes", "Vladimir Guerrero Jr.", "Pinch Hitter"]
    assert box["batting"]["away"]["totals"]["h"] == 3
    assert box["batting"]["away"]["totals"]["hr"] == 1
    assert box["pitching"]["home"]["rows"][0]["player_name"] == "Test Starter"
    assert box["pitching"]["home"]["totals"]["innings_pitched"] == "6.0"
    assert box["linescore"]["away"]["r"] == 4
    assert box["linescore"]["home"]["r"] == 5
    assert box["linescore"]["home"]["e"] == 1


def test_game_boxscore_reads_inning_runs():
    details = parse_game_details(load_fixture("feed_746946.json"))
    box = game_boxscore({**details, "away_score": 4, "home_score": 8}, [])
    assert box["linescore"]["labels"][0] == 1
    assert box["linescore"]["away"]["cells"][0] == "0"
    assert box["linescore"]["home"]["cells"][0] == "3"
    assert box["linescore"]["home"]["cells"][-1] == "X"
    assert box["linescore"]["away"]["h"] > 0


def test_players_and_player_pages(db_conn, tmp_path):
    _seed_player_game(db_conn)
    app = create_app(db_path=tmp_path / "games.db", secret_key="test")
    with app.test_client() as flask_client:
        index = flask_client.get("/players").get_data(as_text=True)
        detail = flask_client.get("/players/111111").get_data(as_text=True)
        report = flask_client.get("/report").get_data(as_text=True)
    assert "Nathan Lukes" in index
    assert "Aaron Judge" in index
    assert 'data-tab="batting"' in index
    assert 'data-tab="pitching"' in index
    assert 'data-tab="fielding"' in index
    assert "data-sort=\"putouts\"" in index
    assert "data-sort=\"pa\"" in index
    assert "ERA" in index
    assert "sort.js" in index
    assert "2-4" in detail
    assert "1 HR" in detail
    assert "mlb_ID=111111" in detail
    assert "baseball-reference.com" in detail
    assert "savant-player/111111" in detail
    assert "Most seen players" in report
    assert "Home runs seen" in report
    assert "412" in report
    assert "108.0" in report
    assert "data-sort=\"distance\"" in report
    assert "data-sort=\"exit_velo\"" in report
    assert "data-sort=\"launch_angle\"" in report
    assert "spray-chart" in report
    assert "spray-hit" in report
    assert "spray-tooltip" in report
    assert "Batting nights" in report
    assert "Pitching gems" in report
    assert "Multiple uniforms" in report
    assert "Extremes" in report
    assert "Longest (time)" in report
    assert "Highest attendance" in report
    assert 'data-extreme="duration"' in report
    assert "extreme-dialog" in report
    assert "extreme-chart-data" in report
    assert "Game length" in report


def test_report_nights_gems_uniforms_and_walkoff(db_conn):
    _seed_player_game(db_conn)
    db.upsert_game_details(
        db_conn,
        {
            "mlb_game_pk": 900002,
            "official_date": "2024-08-01",
            "season": 2024,
            "game_type": "R",
            "venue_id": 3313,
            "venue_name": "Yankee Stadium",
            "home_team_id": 147,
            "away_team_id": 111,
            "home_score": 5,
            "away_score": 4,
            "winning_team_id": 147,
            "is_walkoff": 1,
            "innings": 9,
            "weather_temp": "90",
            "weather_condition": "Clear",
        },
    )
    db.insert_attended_game(
        db_conn,
        {
            "date": "2024-08-01",
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "home_team_id": 147,
            "away_team_id": 111,
            "mlb_game_pk": 900002,
        },
    )
    db.replace_player_game_stats(
        db_conn,
        900002,
        [
            {
                "player_id": 111111,
                "player_name": "Nathan Lukes",
                "team_id": 147,
                "side": "home",
                "started_game": 1,
                "started_pitching": 0,
                "h": 4,
                "ab": 5,
                "hr": 2,
                "rbi": 5,
            },
            {
                "player_id": 222222,
                "player_name": "Test Starter",
                "team_id": 111,
                "side": "away",
                "started_game": 1,
                "started_pitching": 1,
                "outs": 18,
                "h_allowed": 1,
                "so_pitched": 11,
            },
        ],
    )
    db.replace_game_events(
        db_conn,
        900002,
        [
            {
                "at_bat_index": 70,
                "event_type": "home_run",
                "inning": 9,
                "inning_half": "bottom",
                "batter_id": 111111,
                "batter_name": "Nathan Lukes",
                "rbi": 4,
                "description": "Nathan Lukes hits a grand slam",
                "extra_json": json.dumps(
                    {
                        "totalDistance": 400,
                        "launchSpeed": 110,
                        "launchAngle": 30,
                        "coordinates": {"coordX": 80, "coordY": 40},
                    }
                ),
            }
        ],
    )
    report = build_report(db_conn)
    nights = report["players"]["batting_nights"]
    assert len(nights) == 1
    assert nights[0]["player_name"] == "Nathan Lukes"
    assert "grand slam" in nights[0]["flags"]
    assert "2 HR" in nights[0]["flags"]
    gems = report["players"]["pitching_gems"]
    assert gems[0]["player_name"] == "Test Starter"
    assert "11 K" in gems[0]["flags"]
    uniforms = {row["player_id"]: row for row in report["players"]["multiple_uniforms"]}
    assert uniforms[111111]["team_count"] == 2
    walkoffs = report["players"]["walkoff_home_runs"]
    assert len(walkoffs) == 1
    assert walkoffs[0]["batter_name"] == "Nathan Lukes"
    assert report["extremes"]["hottest"]["temp_f"] == 90


def test_players_page_filters_by_min_pa(db_conn, tmp_path):
    _seed_player_game(db_conn)
    assert parse_min_count("") == 0
    assert parse_min_pa("4") == 4
    assert parse_min_pa("-2") == 0
    app = create_app(db_path=tmp_path / "games.db", secret_key="test")
    with app.test_client() as flask_client:
        everyone = flask_client.get("/players").get_data(as_text=True)
        qualified = flask_client.get("/players?min_pa=5").get_data(as_text=True)
        pitchers = flask_client.get("/players?min_bf=30").get_data(as_text=True)
    assert "Nathan Lukes" in everyone
    assert "Min PA" in everyone
    assert "Min BF" in everyone
    assert "Test Starter" in everyone
    assert "No batters with at least 5 PA" in qualified
    batting_panel = qualified.split('data-report-panel="fielding"')[0]
    assert "Nathan Lukes" not in batting_panel
    assert "Nathan Lukes" in qualified
    assert "No pitchers with at least 30 BF" in pitchers
    pitching_panel = pitchers.split('data-report-panel="pitching"')[1]
    pitching_panel = pitching_panel.split('data-report-panel="fielding"')[0]
    assert "Test Starter" not in pitching_panel


def test_apply_feed_tables_matches_parsers(db_conn):
    feed = load_fixture("feed_players_hr.json")
    details = parse_game_details(feed)
    apply_feed_tables(db_conn, feed, details)
    stored = db.list_player_game_stats(db_conn, 900001)
    assert len(stored) == len(parse_player_game_stats(feed))
    assert len(db.list_game_events(db_conn, 900001)) == len(parse_game_events(feed))
