from tracker.mlb import (
    game_type_label,
    parse_game_details,
    parse_game_events,
    parse_player_game_stats,
    parse_schedule_candidates,
    playoff_label,
    series_fields_from_schedule,
)

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


def test_schedule_matches_one_team_on_either_side():
    schedule = load_fixture("schedule_2024-06-15.json")
    as_home = parse_schedule_candidates(schedule, home_team_id=111)
    as_away = parse_schedule_candidates(schedule, away_team_id=147)
    either = parse_schedule_candidates(schedule, either_team_id=147)
    assert [game.game_pk for game in as_home] == [746946]
    assert [game.game_pk for game in as_away] == [746946]
    assert [game.game_pk for game in either] == [746946]


def test_schedule_doubleheader_returns_both_games():
    schedule = load_fixture("schedule_2018-07-09_phi.json")
    games = parse_schedule_candidates(schedule, home_team_id=121, away_team_id=143)
    assert [game.game_number for game in games] == [1, 2]
    assert {game.game_pk for game in games} == {530769, 529466}


def test_playoff_label_formats_series_game():
    assert playoff_label("W", series_description="World Series", series_game_number=1) == "World Series Game 1"
    assert playoff_label("L", series_description="AL Championship Series", series_game_number=2) == (
        "AL Championship Series Game 2"
    )
    assert playoff_label("R", series_description="Regular Season", series_game_number=3) is None
    assert playoff_label("W") == "World Series"
    assert game_type_label("S") == "Spring Training"
    assert game_type_label("R") is None


def test_schedule_parses_world_series_game():
    schedule = {
        "dates": [
            {
                "date": "2024-10-25",
                "games": [
                    {
                        "gamePk": 775300,
                        "officialDate": "2024-10-25",
                        "gameType": "W",
                        "gameNumber": 1,
                        "doubleHeader": "N",
                        "seriesDescription": "World Series",
                        "seriesGameNumber": 1,
                        "status": {"detailedState": "Final", "codedGameState": "F"},
                        "teams": {
                            "home": {"team": {"id": 119, "name": "Los Angeles Dodgers"}, "score": 6},
                            "away": {"team": {"id": 147, "name": "New York Yankees"}, "score": 3},
                        },
                        "venue": {"name": "Dodger Stadium"},
                        "decisions": {},
                    }
                ],
            }
        ]
    }
    games = parse_schedule_candidates(schedule)
    assert games[0].series_label == "World Series Game 1"
    assert series_fields_from_schedule(schedule, 775300) == {
        "game_type": "W",
        "series_description": "World Series",
        "series_game_number": 1,
    }


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


def test_parse_player_stats_and_home_run_event():
    feed = load_fixture("feed_players_hr.json")
    players = {row["player_id"]: row for row in parse_player_game_stats(feed)}
    lukes = players[111111]
    assert lukes["player_name"] == "Nathan Lukes"
    assert lukes["side"] == "away"
    assert lukes["batting_order"] == 1
    assert lukes["started_game"] == 1
    assert lukes["hr"] == 1
    assert lukes["h"] == 2
    assert lukes["ab"] == 4
    pinch = players[111113]
    assert pinch["started_game"] == 0
    assert pinch["batting_order"] == 101
    starter = players[222222]
    assert starter["started_pitching"] == 1
    assert starter["outs"] == 18
    assert starter["pitching_decision"] == "W"
    loser = players[333333]
    assert loser["pitching_decision"] == "L"

    events = parse_game_events(feed)
    assert len(events) == 1
    homer = events[0]
    assert homer["event_type"] == "home_run"
    assert homer["batter_name"] == "Nathan Lukes"
    assert homer["inning"] == 3
    assert homer["inning_half"] == "top"
    assert homer["rbi"] == 2
    assert "412" in (homer["extra_json"] or "")


def test_player_parsers_tolerate_pitcher_only_feed():
    feed = load_fixture("feed_746946.json")
    players = parse_player_game_stats(feed)
    by_name = {row["player_name"]: row for row in players}
    assert by_name["Cooper Criswell"]["started_pitching"] == 1
    assert by_name["Justin Slaten"]["started_pitching"] == 0
    assert all(row["hr"] is None for row in players)
    events = parse_game_events(feed)
    assert [event["event_type"] for event in events] == ["home_run"]
    assert "Juan Soto homers" in (events[0]["description"] or "")
