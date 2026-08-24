from __future__ import annotations

from tracker.reports import format_record, format_score


def render_report_html(report: dict) -> str:
    overall = report["overall"]
    stadiums = report["stadiums"]
    longest = report["longest_shortest"]
    attendance = report["attendance"]

    sections = [
        _section(
            "Overall",
            f"<p>Home-team record in games you attended: "
            f"<strong>{format_record(overall['wins'], overall['losses'], overall['ties'])}</strong> "
            f"({overall['games']} confirmed of {report['totals']['attended']} logged)</p>",
        ),
        _section("By team seen", _team_table(report["by_team"])),
        _section("Teams", _teams_html(report["teams"])),
        _section(
            "Longest and shortest",
            _kv_table(
                [
                    ("Longest (time)", _game_line(longest.get("longest_duration"), "duration_minutes", "min")),
                    ("Shortest (time)", _game_line(longest.get("shortest_duration"), "duration_minutes", "min")),
                    ("Longest (innings)", _game_line(longest.get("longest_innings"), "innings", "inn")),
                    ("Shortest (innings)", _game_line(longest.get("shortest_innings"), "innings", "inn")),
                ]
            ),
        ),
        _section(
            "Stadiums",
            _stadiums_html(stadiums),
        ),
        _section(
            "Attendance",
            _kv_table(
                [
                    ("Highest", _game_line(attendance.get("highest"), "attendance", "fans")),
                    ("Lowest", _game_line(attendance.get("lowest"), "attendance", "fans")),
                ]
            ),
        ),
        _section("By year", _year_table(report["by_year"])),
        _section("Notable", _notable_list(report["notable"])),
        _section("Most seen players", _player_seen_table(report["players"]["most_seen"])),
        _section("Starting pitchers seen", _starter_table(report["players"]["starters"])),
        _section("Home runs seen", _home_run_block(report["players"])),
    ]
    if report["unmatched"]:
        sections.append(
            _section(
                "Not yet confirmed",
                _simple_list(
                    report["unmatched"],
                    lambda game: f"{game['date']}: {game['away_team']} @ {game['home_team']}",
                ),
            )
        )

    body = "\n".join(sections)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Games attended</title>
  <style>
    body {{ font-family: Georgia, serif; max-width: 880px; margin: 2rem auto; padding: 0 1rem; color: #1b1b1b; }}
    h1, h2, h3 {{ font-family: "Iowan Old Style", Georgia, serif; }}
    table {{ border-collapse: collapse; width: 100%; margin: 0.75rem 0; }}
    th, td {{ border-bottom: 1px solid #ddd; text-align: left; padding: 0.4rem 0.5rem; }}
    th {{ font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em; }}
  </style>
</head>
<body>
  <h1>Games attended</h1>
  {body}
</body>
</html>
"""


def _stadiums_html(stadiums: dict) -> str:
    visited = _simple_list(stadiums["visited"], lambda park: f"{park['name']} ({park['games']})")
    remaining = _simple_list(stadiums["remaining"], lambda park: park["name"])
    return (
        f"<p>Visited {stadiums['visited_count']} of {stadiums['current_park_count']} current MLB parks.</p>"
        f"<h3>Visited</h3>{visited}"
        f"<h3>Still need to visit</h3>{remaining}"
    )


def _teams_html(teams: dict) -> str:
    seen = _simple_list(teams["seen"], lambda team: f"{team['name']} ({team['games']})")
    remaining = _simple_list(teams["remaining"], lambda team: team["name"])
    return (
        f"<p>Seen {teams['seen_count']} of {teams['current_team_count']} current MLB teams.</p>"
        f"<h3>Seen</h3>{seen}"
        f"<h3>Still need to see</h3>{remaining}"
    )


def _section(title: str, inner: str) -> str:
    return f"<section><h2>{title}</h2>{inner}</section>"


def _team_table(rows: list[dict]) -> str:
    if not rows:
        return "<p>No confirmed games yet.</p>"
    cells = []
    for row in rows:
        cells.append(
            "<tr>"
            f"<td>{row['team']}</td>"
            f"<td>{format_record(row['wins'], row['losses'], row['ties'])}</td>"
            f"<td>{row['home_wins']}-{row['home_losses']}</td>"
            f"<td>{row['away_wins']}-{row['away_losses']}</td>"
            f"<td>{row['seen']}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Team</th><th>Overall</th><th>Seen at home</th>"
        "<th>Seen on road</th><th>Games</th></tr></thead>"
        f"<tbody>{''.join(cells)}</tbody></table>"
    )


def _year_table(rows: list[dict]) -> str:
    if not rows:
        return "<p>No confirmed games yet.</p>"
    cells = [
        f"<tr><td>{row['year']}</td><td>{row['games']}</td>"
        f"<td>{format_record(row['wins'], row['losses'], row['ties'])}</td></tr>"
        for row in rows
    ]
    return (
        "<table><thead><tr><th>Year</th><th>Games</th><th>Home W-L</th></tr></thead>"
        f"<tbody>{''.join(cells)}</tbody></table>"
    )


def _kv_table(pairs: list[tuple[str, str]]) -> str:
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in pairs)
    return f"<table>{rows}</table>"


def _simple_list(items: list, label) -> str:
    if not items:
        return "<p>None.</p>"
    return "<ul>" + "".join(f"<li>{label(item)}</li>" for item in items) + "</ul>"


def _notable_list(rows: list[dict]) -> str:
    if not rows:
        return "<p>No walk-offs, extras, or no-hitters logged yet.</p>"
    return _simple_list(
        rows,
        lambda game: f"{format_score(game)} — {', '.join(game['flags'])}",
    )


def _game_line(game: dict | None, field: str, unit: str) -> str:
    if not game:
        return "—"
    return f"{format_score(game)} ({game.get(field)} {unit})"


def _player_seen_table(rows: list[dict]) -> str:
    if not rows:
        return "<p>No player lines yet. Run reparse over the cache.</p>"
    cells = [
        (
            f"<tr><td>{row['player_name']}</td><td>{row['games_seen']}</td>"
            f"<td>{row['games_started']}</td><td>{row['games_started_pitching']}</td>"
            f"<td>{row['hr']}</td><td>{row['slash']}</td></tr>"
        )
        for row in rows
    ]
    return (
        "<table><thead><tr><th>Player</th><th>Games</th><th>Started</th>"
        "<th>SP</th><th>HR</th><th>Slash</th></tr></thead>"
        f"<tbody>{''.join(cells)}</tbody></table>"
    )


def _starter_table(rows: list[dict]) -> str:
    if not rows:
        return "<p>No starting pitchers in this view.</p>"
    cells = [
        f"<tr><td>{row['player_name']}</td><td>{row['games_started_pitching']}</td></tr>"
        for row in rows
    ]
    return (
        "<table><thead><tr><th>Pitcher</th><th>Starts seen</th></tr></thead>"
        f"<tbody>{''.join(cells)}</tbody></table>"
    )


def _home_run_block(players: dict) -> str:
    count = players.get("home_run_count") or 0
    if not count:
        return "<p>No home runs in this view.</p>"
    longest = players.get("longest_home_runs") or []
    items = []
    for event in longest:
        distance = f" — {int(event['distance'])} ft" if event.get("distance") else ""
        items.append(f"{event.get('batter_name')}: {event.get('description') or ''}{distance}")
    extra = _simple_list(items, lambda item: item) if items else ""
    return f"<p>{count} home runs seen.</p>{extra}"
