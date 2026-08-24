from tracker import db
from tracker.mlb import parse_game_details
from tracker.reports import build_report, format_record

from tests.conftest import load_fixture


def _seed_confirmed(conn, details, *, date, home, away, home_id, away_id, notes=""):
    game_id = db.insert_attended_game(
        conn,
        {
            "date": date,
            "home_team": home,
            "away_team": away,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "notes": notes,
            "mlb_game_pk": details["mlb_game_pk"],
        },
    )
    db.upsert_game_details(conn, details)
    return game_id


def test_report_record_parks_and_unmatched(db_conn):
    fenway = parse_game_details(load_fixture("feed_746946.json"))
    _seed_confirmed(
        db_conn,
        fenway,
        date="2024-06-15",
        home="Red Sox",
        away="Yankees",
        home_id=111,
        away_id=147,
    )

    extras = {
        **fenway,
        "mlb_game_pk": 99,
        "official_date": "2023-08-01",
        "season": 2023,
        "venue_id": 3313,
        "venue_name": "Yankee Stadium",
        "home_team_id": 147,
        "away_team_id": 111,
        "home_score": 2,
        "away_score": 3,
        "winning_team_id": 111,
        "attendance": 48000,
        "duration_minutes": 210,
        "innings": 11,
        "is_walkoff": 0,
        "is_extra_innings": 1,
        "is_no_hitter": 0,
    }
    _seed_confirmed(
        db_conn,
        extras,
        date="2023-08-01",
        home="Yankees",
        away="Red Sox",
        home_id=147,
        away_id=111,
    )

    db.insert_attended_game(
        db_conn,
        {
            "date": "2020-07-04",
            "home_team": "Red Sox",
            "away_team": "Yankees",
            "home_team_id": 111,
            "away_team_id": 147,
            "mlb_game_pk": None,
            "notes": "wrong date maybe",
        },
    )

    report = build_report(db_conn)
    assert report["totals"]["attended"] == 3
    assert report["totals"]["confirmed"] == 2
    assert report["totals"]["unmatched"] == 1
    assert format_record(report["overall"]["wins"], report["overall"]["losses"], report["overall"]["ties"]) == "1-1"

    by_team = {row["team_id"]: row for row in report["by_team"]}
    assert by_team[111]["seen"] == 2
    assert by_team[111]["home_wins"] == 1
    assert by_team[111]["away_wins"] == 1

    visited_ids = {park["venue_id"] for park in report["stadiums"]["visited"]}
    assert visited_ids == {3, 3313}
    remaining_ids = {park["venue_id"] for park in report["stadiums"]["remaining"]}
    assert 3 not in remaining_ids
    assert 3313 not in remaining_ids
    assert report["stadiums"]["current_park_count"] == 30

    seen_ids = {team["team_id"] for team in report["teams"]["seen"]}
    assert seen_ids == {111, 147}
    remaining_team_ids = {team["team_id"] for team in report["teams"]["remaining"]}
    assert 111 not in remaining_team_ids
    assert 147 not in remaining_team_ids
    assert report["teams"]["current_team_count"] == 30
    assert report["teams"]["seen_count"] == 2

    assert report["longest_shortest"]["longest_duration"]["duration_minutes"] == 210
    assert report["longest_shortest"]["shortest_duration"]["duration_minutes"] == 188
    assert report["attendance"]["highest"]["attendance"] == 48000
    assert report["attendance"]["lowest"]["attendance"] == 36673
    years = {row["year"]: row for row in report["by_year"]}
    assert years[2024]["games"] == 1
    assert years[2023]["games"] == 1
    assert report["notable"][0]["is_extra_innings"] == 1
    assert len(report["unmatched"]) == 1
    assert set(report["type_groups"]) == {"regular", "playoffs"}


def test_report_excludes_spring_training_by_default(db_conn):
    fenway = parse_game_details(load_fixture("feed_746946.json"))
    _seed_confirmed(
        db_conn,
        fenway,
        date="2024-06-15",
        home="Red Sox",
        away="Yankees",
        home_id=111,
        away_id=147,
    )
    spring = {
        **fenway,
        "mlb_game_pk": 88,
        "official_date": "2024-03-12",
        "season": 2024,
        "game_type": "S",
        "venue_id": 2508,
        "venue_name": "Salt River Fields",
        "home_score": 5,
        "away_score": 4,
        "winning_team_id": 111,
    }
    _seed_confirmed(
        db_conn,
        spring,
        date="2024-03-12",
        home="Red Sox",
        away="Yankees",
        home_id=111,
        away_id=147,
    )

    default_report = build_report(db_conn)
    assert default_report["totals"]["confirmed"] == 1
    assert default_report["totals"]["attended"] == 2

    with_spring = build_report(db_conn, type_groups=["regular", "playoffs", "spring"])
    assert with_spring["totals"]["confirmed"] == 2
