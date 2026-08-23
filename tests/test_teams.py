from tracker.teams import resolve_team


def test_abbreviation_and_full_name_resolve():
    bos = resolve_team("BOS")
    assert bos.unique
    assert bos.team.id == 111
    assert resolve_team("Red Sox").team.id == 111
    assert resolve_team("Boston").team.id == 111


def test_ambiguous_nicknames():
    sox = resolve_team("Sox")
    assert {team.id for team in sox.matches} == {111, 145}

    chicago = resolve_team("Chicago")
    assert {team.id for team in chicago.matches} == {112, 145}

    la = resolve_team("LA")
    assert {team.id for team in la.matches} == {108, 119}


def test_unknown_team():
    result = resolve_team("Springfield Isotopes")
    assert result.matches == ()


def test_athletics_aliases():
    assert resolve_team("ATH").team.id == 133
    assert resolve_team("Oakland").team.id == 133
    assert resolve_team("A's").team.id == 133
