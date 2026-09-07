from tracker.br import (
    baseball_reference_game_url,
    baseball_reference_player_url,
    baseball_savant_game_url,
    baseball_savant_player_url,
)


def test_player_url_uses_mlb_id_redirect():
    assert baseball_reference_player_url(592450) == (
        "https://www.baseball-reference.com/redirect.fcgi?player=1&mlb_ID=592450"
    )
    assert baseball_reference_player_url(None) is None


def test_game_url_uses_home_team_box_code():
    url = baseball_reference_game_url(
        {
            "official_date": "2024-06-15",
            "home_team_id": 147,
            "home_team": "New York Yankees",
        }
    )
    assert url == "https://www.baseball-reference.com/boxes/NYA/NYA202406150.shtml"


def test_game_url_uses_historical_franchise_codes():
    marlins = baseball_reference_game_url({"date": "2011-07-04", "home_team_id": 146})
    nationals = baseball_reference_game_url({"date": "2004-09-01", "home_team_id": 120})
    assert marlins.endswith("/FLA/FLA201107040.shtml")
    assert nationals.endswith("/MON/MON200409010.shtml")


def test_savant_player_url_uses_mlb_id():
    assert baseball_savant_player_url(518692) == (
        "https://baseballsavant.mlb.com/savant-player/518692"
    )
    assert baseball_savant_player_url(None) is None


def test_savant_game_url_uses_game_pk():
    assert baseball_savant_game_url({"mlb_game_pk": 746946}) == (
        "https://baseballsavant.mlb.com/gamefeed?gamePk=746946"
    )
    assert baseball_savant_game_url({"home_team": "Yankees"}) is None


def test_game_url_marks_doubleheader_game_two():
    url = baseball_reference_game_url(
        {"official_date": "2018-07-09", "home_team_id": 144, "game_number": 2}
    )
    assert url.endswith("/ATL/ATL201807091.shtml")
