from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tracker import db
from tracker.enrich import AlreadyLoggedError, accept_candidate, enrich_all, reparse_cache, resolve_add
from tracker.html import render_report_html
from tracker.mlb import MlbClient
from tracker.paths import DB_PATH
from tracker.reports import build_report, format_record, format_score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracker", description="Track MLB games you attended.")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add a game from the command line.")
    add.add_argument("--date", default="", help="Full date like 2024-06-15, or a year like 2024.")
    add.add_argument("--home", default="")
    add.add_argument("--away", default="")
    add.add_argument("--notes", default="")
    add.add_argument("--section", default="")
    add.add_argument("--row", default="")
    add.add_argument("--seat", default="")
    add.add_argument("--game-number", type=int)

    sub.add_parser("list", help="List logged games.")

    delete = sub.add_parser("delete", help="Remove a logged game.")
    delete.add_argument("--id", type=int, required=True)

    enrich = sub.add_parser("enrich", help="Fetch official details for unmatched-enrich rows.")
    enrich.add_argument("--force", action="store_true")
    enrich.add_argument("--id", type=int)

    sub.add_parser("reparse", help="Rebuild official tables from cached feeds.")

    report = sub.add_parser("report", help="Print reports.")
    report.add_argument("--html", nargs="?", const="report.html")

    serve = sub.add_parser("serve", help="Run the local web UI.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "serve":
        from tracker.app import run_server

        run_server(host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0

    conn = db.connect(DB_PATH)
    try:
        if args.command == "add":
            return _cmd_add(conn, args)
        if args.command == "list":
            return _cmd_list(conn)
        if args.command == "delete":
            return _cmd_delete(conn, args)
        if args.command == "enrich":
            return _cmd_enrich(conn, args)
        if args.command == "reparse":
            return _cmd_reparse(conn)
        if args.command == "report":
            return _cmd_report(conn, args)
    finally:
        conn.close()
    return 1


def _cmd_add(conn, args) -> int:
    result = resolve_add(
        conn,
        date=args.date,
        home_team=args.home,
        away_team=args.away,
        notes=args.notes,
        seat_section=args.section,
        seat_row=args.row,
        seat_seat=args.seat,
        client=MlbClient(),
    )
    if not result.ok:
        if result.error:
            print(result.error)
        if args.home and not result.home.unique:
            print(f"Home team “{args.home}” is unknown or ambiguous: {[t.name for t in result.home.matches]}")
        if args.away and not result.away.unique:
            print(f"Away team “{args.away}” is unknown or ambiguous: {[t.name for t in result.away.matches]}")
        return 2
    pending = result.pending
    assert pending is not None
    open_candidates = [item for item in pending.candidates if not item.already_logged_id]
    if not open_candidates:
        if pending.candidates:
            print("That official game is already in your log. Use the UI if you need to inspect it.")
            return 3
        print("No completed MLB game for that lookup. Use the UI to save personal-only.")
        return 4
    if len(open_candidates) > 1 and not args.game_number:
        print("Multiple matchups. Re-run with --date or --game-number, or pick one in the UI.")
        for candidate in open_candidates:
            print(
                f"  game {candidate.game_number}: {candidate.away_team} {candidate.away_score} "
                f"@ {candidate.home_team} {candidate.home_score} (pk {candidate.game_pk})"
            )
        return 5
    chosen = open_candidates[0]
    if args.game_number:
        chosen = next((item for item in open_candidates if item.game_number == args.game_number), None)
        if chosen is None:
            print(f"No candidate with --game-number {args.game_number}")
            return 5
    try:
        game_id = accept_candidate(conn, pending, chosen.game_pk, client=MlbClient())
    except AlreadyLoggedError as exc:
        print(f"Already logged as game {exc.attended_id}")
        return 3
    print(f"Saved attended game {game_id} (pk {chosen.game_pk})")
    return 0


def _cmd_delete(conn, args) -> int:
    deleted = db.delete_attended_game(conn, args.id)
    if deleted is None:
        print(f"No game with id {args.id}")
        return 1
    print(f"Deleted {deleted['away_team']} @ {deleted['home_team']} on {deleted['date']}")
    return 0


def _cmd_list(conn) -> int:
    games = db.list_attended_games(conn)
    if not games:
        print("No games logged.")
        return 0
    for game in games:
        score = (
            f"{game['away_score']}-{game['home_score']}"
            if game.get("away_score") is not None
            else "unmatched"
        )
        print(f"{game['id']:>4}  {game.get('official_date') or game['date']}  {game['away_team']} @ {game['home_team']}  {score}")
    return 0


def _cmd_enrich(conn, args) -> int:
    results = enrich_all(conn, client=MlbClient(), force=args.force, game_id=args.id)
    for row in results:
        extra = f" ({row.get('reason')})" if row.get("reason") else ""
        print(f"{row['id']}: {row['status']}{extra}")
    return 0


def _cmd_reparse(conn) -> int:
    results = reparse_cache(conn)
    print(f"Reparsed {len(results)} cached games")
    return 0


def _cmd_report(conn, args) -> int:
    report = build_report(conn)
    if args.html:
        path = Path(args.html)
        path.write_text(render_report_html(report))
        print(f"Wrote {path}")
        return 0
    overall = report["overall"]
    print(f"Overall home-team record: {format_record(overall['wins'], overall['losses'], overall['ties'])}")
    print(f"Confirmed {report['totals']['confirmed']} of {report['totals']['attended']} logged")
    print("\nBy team:")
    for row in report["by_team"]:
        print(f"  {row['team']}: {format_record(row['wins'], row['losses'], row['ties'])} ({row['seen']} seen)")
    stadiums = report["stadiums"]
    print(f"\nParks: {stadiums['visited_count']} / {stadiums['current_park_count']}")
    for park in stadiums["visited"]:
        print(f"  visited {park['name']}")
    longest = report["longest_shortest"]
    if longest["longest_duration"]:
        print(f"\nLongest: {format_score(longest['longest_duration'])} ({longest['longest_duration']['duration_minutes']} min)")
    if longest["shortest_duration"]:
        print(f"Shortest: {format_score(longest['shortest_duration'])} ({longest['shortest_duration']['duration_minutes']} min)")
    attendance = report["attendance"]
    if attendance["highest"]:
        print(f"Highest attendance: {format_score(attendance['highest'])} ({attendance['highest']['attendance']})")
    if attendance["lowest"]:
        print(f"Lowest attendance: {format_score(attendance['lowest'])} ({attendance['lowest']['attendance']})")
    print("\nBy year:")
    for row in report["by_year"]:
        print(f"  {row['year']}: {row['games']} games, {format_record(row['wins'], row['losses'], row['ties'])}")
    if report["notable"]:
        print("\nNotable:")
        for game in report["notable"]:
            print(f"  {format_score(game)} — {', '.join(game['flags'])}")
    if report["unmatched"]:
        print("\nUnmatched:")
        for game in report["unmatched"]:
            print(f"  {game['date']}: {game['away_team']} @ {game['home_team']}")
    players = report["players"]
    if players["most_seen"]:
        print("\nMost seen players:")
        for row in players["most_seen"][:8]:
            print(f"  {row['player_name']}: {row['games_seen']} games")
    if players["starters"]:
        print("\nStarting pitchers seen:")
        for row in players["starters"][:8]:
            print(f"  {row['player_name']}: {row['games_started_pitching']} starts")
    if players["home_run_count"]:
        print(f"\nHome runs seen: {players['home_run_count']}")
        for event in players["longest_home_runs"][:3]:
            distance = f" ({int(event['distance'])} ft)" if event.get("distance") else ""
            print(f"  {event['batter_name']}: {event['description']}{distance}")
    return 0
