from tracker import db
from tracker.app import create_app
from tracker.enrich import apply_feed_tables, refresh_player_profiles
from tracker.mlb import MlbClient, parse_game_details, parse_people
from tracker.reports import birthplace_label, build_report, player_page
from tests.conftest import load_fixture


def test_parse_people_maps_birth_country_and_skips_missing_ids():
    rows = parse_people(
        {
            "people": [
                {
                    "id": 592450,
                    "fullName": "Aaron Judge",
                    "birthCity": "Linden",
                    "birthStateProvince": "CA",
                    "birthCountry": "USA",
                },
                {"fullName": "No Id", "birthCountry": "Japan"},
            ]
        }
    )
    assert len(rows) == 1
    assert rows[0]["player_id"] == 592450
    assert rows[0]["birth_country"] == "USA"
    assert rows[0]["birth_city"] == "Linden"
    assert rows[0]["birth_state_province"] == "CA"


def test_refresh_profiles_uses_cache_and_groups_report(db_conn, tmp_path):
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

    people = load_fixture("people_mixed.json")
    calls = {"count": 0}

    def get_json(url, params=None):
        calls["count"] += 1
        assert "/people" in url
        requested = {int(item) for item in (params or {}).get("personIds", "").split(",") if item}
        return {"people": [person for person in people["people"] if person["id"] in requested]}

    client = MlbClient(get_json=get_json, cache_dir=tmp_path / "cache")
    first = refresh_player_profiles(db_conn, client=client, force=True)
    assert first["seen"] == 6
    assert first["fetched"] == 6
    assert first["stored"] == 6
    assert calls["count"] == 1

    refresh_player_profiles(db_conn, client=client, force=False)
    assert calls["count"] == 1

    report = build_report(db_conn)
    nations = report["players"]["nationalities"]
    assert nations["loaded"] is True
    assert nations["country_count"] == 4
    assert nations["usa_count"] == 2
    assert nations["international_count"] == 4
    by_country = {row["country"]: row for row in nations["countries"]}
    assert by_country["Canada"]["player_count"] == 2
    assert by_country["Canada"]["game_count"] == 1
    assert by_country["Japan"]["players"][0]["player_name"] == "Test Starter"
    assert [row["country"] for row in nations["countries"]][0] == "Canada"

    page = player_page(db_conn, 444444)
    assert page["birthplace"] == "Linden, CA, USA"


def test_nationalities_appear_on_report_and_player_pages(db_conn, tmp_path):
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
    db.upsert_player_profiles(
        db_conn,
        [
            {
                "player_id": 444444,
                "player_name": "Aaron Judge",
                "birth_country": "USA",
                "birth_city": "Linden",
                "birth_state_province": "CA",
            },
            {
                "player_id": 222222,
                "player_name": "Test Starter",
                "birth_country": "Japan",
                "birth_city": "Oshu",
                "birth_state_province": None,
            },
        ],
    )
    app = create_app(db_path=tmp_path / "games.db", secret_key="test")
    with app.test_client() as flask_client:
        report = flask_client.get("/report").get_data(as_text=True)
        detail = flask_client.get("/players/444444").get_data(as_text=True)
    assert "Nationalities represented" in report
    assert "Japan" in report
    assert "Born in Linden, CA, USA" in detail


def test_birthplace_label_skips_empty_parts():
    assert birthplace_label(None) is None
    assert birthplace_label({"birth_country": "Japan", "birth_city": "Oshu"}) == "Oshu, Japan"
    assert birthplace_label({"birth_country": "USA"}) == "USA"
