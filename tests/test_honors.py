from tracker import db
from tracker.app import create_app
from tracker.enrich import apply_feed_tables, refresh_honors
from tracker.mlb import MlbClient, parse_award_recipients, parse_game_details
from tracker.reports import build_report, player_page
from tests.conftest import load_fixture


def test_parse_award_recipients_maps_league_awards_and_skips_unknown():
    rows = parse_award_recipients(load_fixture("awards_mixed.json"))
    by_player = {}
    for row in rows:
        by_player.setdefault(row["player_id"], []).append(row)
    judge = by_player[444444]
    assert {row["honor_type"] for row in judge} == {"mvp"}
    assert sorted(row["season"] for row in judge) == [2022, 2024]
    assert by_player[111111][0]["honor_type"] == "hof"


def test_refresh_honors_uses_cache_and_counts_distinct_seen_players(db_conn, tmp_path):
    feed = load_fixture("feed_players_hr.json")
    details = parse_game_details(feed)
    db.insert_attended_game(
        db_conn,
        {
            "date": details["official_date"],
            "home_team": "New York Yankees",
            "away_team": "Toronto Blue Jays",
            "home_team_id": details["home_team_id"],
            "away_team_id": details["away_team_id"],
            "mlb_game_pk": details["mlb_game_pk"],
        },
    )
    apply_feed_tables(db_conn, feed, details)

    mixed = load_fixture("awards_mixed.json")

    def get_json(url, params=None):
        if url.endswith("/awards/ALMVP/recipients"):
            return {"awards": [item for item in mixed["awards"] if item["id"] == "ALMVP"]}
        if url.endswith("/awards/MLBHOF/recipients"):
            return {"awards": [item for item in mixed["awards"] if item["id"] == "MLBHOF"]}
        return {"awards": []}

    client = MlbClient(get_json=get_json, cache_dir=tmp_path / "cache")
    fetched = refresh_honors(db_conn, client=client, force=True)
    assert fetched["ALMVP"] == 2
    assert fetched["MLBHOF"] == 1
    refresh_honors(db_conn, client=client, force=False)

    report = build_report(db_conn)
    assert report["honors"]["loaded"] is True
    by_type = {group["honor_type"]: group for group in report["honors"]["groups"]}
    assert by_type["mvp"]["count"] == 1
    assert by_type["mvp"]["players"][0]["player_name"] == "Aaron Judge"
    assert by_type["hof"]["count"] == 1
    assert by_type["cy_young"]["count"] == 0

    page = player_page(db_conn, 444444)
    assert [honor["honor_type"] for honor in page["honors"]] == ["mvp"]
    assert page["honors"][0]["seasons"] == [2022, 2024]


def test_honors_appear_on_players_and_report_pages(db_conn, tmp_path):
    feed = load_fixture("feed_players_hr.json")
    details = parse_game_details(feed)
    db.insert_attended_game(
        db_conn,
        {
            "date": details["official_date"],
            "home_team": "New York Yankees",
            "away_team": "Toronto Blue Jays",
            "mlb_game_pk": details["mlb_game_pk"],
        },
    )
    apply_feed_tables(db_conn, feed, details)
    db.replace_player_honors(
        db_conn,
        [
            {
                "player_id": 444444,
                "honor_type": "mvp",
                "award_id": "ALMVP",
                "season": 2024,
                "player_name": "Aaron Judge",
            }
        ],
    )
    app = create_app(db_path=tmp_path / "games.db", secret_key="test")
    with app.test_client() as flask_client:
        players = flask_client.get("/players").get_data(as_text=True)
        detail = flask_client.get("/players/444444").get_data(as_text=True)
        report = flask_client.get("/report").get_data(as_text=True)
    assert "Aaron Judge" in players
    assert "MVP" in players
    assert "2024" in detail
    assert "Honors seen" in report
    assert "Aaron Judge" in report
    assert 'data-report-tabs' in report
    assert 'data-tab="honors"' in report
    assert "Teams & parks" in report
    assert "report.js" in report
