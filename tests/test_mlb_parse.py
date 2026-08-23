from tracker.mlb import parse_game_details, parse_schedule_candidates

from tests.conftest import load_fixture


def test_schedule_finds_final_and_skips_postponed():
    schedule = load_fixture("schedule_2024-06-15.json")
    yankees_at_sox = parse_schedule_candidates(schedule, home_team_id=111, away_team_id=147)
    assert len(yankees_at_sox) == 1
    game = yankees_at_sox[0]
    assert game.game_pk == 746946
    assert game.home_score == 8
    assert game.away_score == 4
    assert game.venue_name == "Fenway Park"
    assert game.winning_pitcher == "Justin Slaten"

    rained_out = parse_schedule_candidates(schedule, home_team_id=142, away_team_id=133)
    assert rained_out == []


def test_schedule_doubleheader_returns_both_games():
    schedule = load_fixture("schedule_2018-07-09_phi.json")
    games = parse_schedule_candidates(schedule, home_team_id=121, away_team_id=143)
    assert [game.game_number for game in games] == [1, 2]
    assert {game.game_pk for game in games} == {530769, 529466}


def test_parse_feed_details():
    details = parse_game_details(load_fixture("feed_746946.json"))
    assert details["mlb_game_pk"] == 746946
    assert details["home_team_id"] == 111
    assert details["away_team_id"] == 147
    assert details["home_score"] == 8
    assert details["away_score"] == 4
    assert details["winning_team_id"] == 111
    assert details["home_starter"] == "Cooper Criswell"
    assert details["away_starter"] == "Carlos Rodón"
    assert details["winning_pitcher"] == "Justin Slaten"
    assert details["losing_pitcher"] == "Carlos Rodón"
    assert details["save_pitcher"] == "Kenley Jansen"
    assert details["attendance"] == 36673
    assert details["duration_minutes"] == 188
    assert details["innings"] == 9
    assert details["venue_name"] == "Fenway Park"
    assert details["weather_temp"] == "76"
    assert details["is_walkoff"] == 0
    assert details["is_extra_innings"] == 0
    assert details["is_no_hitter"] == 0


def test_walkoff_extra_innings_and_no_hitter_flags():
    feed = {
        "gameData": {
            "game": {"pk": 1, "type": "R", "season": "2024"},
            "datetime": {"officialDate": "2024-06-01"},
            "status": {"detailedState": "Final"},
            "teams": {
                "home": {"id": 111, "name": "Boston Red Sox"},
                "away": {"id": 147, "name": "New York Yankees"},
            },
            "venue": {"id": 3, "name": "Fenway Park"},
            "weather": {},
            "gameInfo": {},
            "flags": {"noHitter": True},
        },
        "liveData": {
            "decisions": {},
            "boxscore": {"teams": {"home": {"pitchers": []}, "away": {"pitchers": []}}},
            "linescore": {
                "currentInning": 10,
                "innings": [{}] * 10,
                "teams": {"home": {"runs": 1, "hits": 3}, "away": {"runs": 0, "hits": 0}},
            },
            "plays": {
                "allPlays": [
                    {
                        "about": {
                            "inning": 10,
                            "halfInning": "bottom",
                            "isScoringPlay": True,
                            "isWalkOff": True,
                        },
                        "result": {"description": "Walk-off single."},
                    }
                ]
            },
        },
    }
    details = parse_game_details(feed)
    assert details["is_walkoff"] == 1
    assert details["is_extra_innings"] == 1
    assert details["is_no_hitter"] == 1
    assert details["winning_team_id"] == 111
