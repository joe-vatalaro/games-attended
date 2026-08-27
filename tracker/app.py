from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for

from tracker import db
from tracker.enrich import (
    AlreadyLoggedError,
    PendingAdd,
    accept_candidate,
    parse_date_input,
    reject_candidate,
    resolve_add,
    save_personal_only,
)
from tracker.mlb import MlbClient, MlbError, game_type_label
from tracker.paths import DB_PATH, SECRET_KEY_PATH, ensure_data_dirs
from tracker.reports import (
    BATTING_TABLE_COLUMNS,
    PITCHING_TABLE_COLUMNS,
    build_report,
    format_innings_pitched,
    format_record,
    format_score,
    format_slash,
    list_player_summaries,
    parse_report_type_groups,
    player_page,
)
from tracker.teams import all_teams, team_by_id


def create_app(
    db_path: Path | str | None = None,
    client: MlbClient | None = None,
    secret_key: str | None = None,
) -> Flask:
    app = Flask(__name__)
    app.secret_key = secret_key or _load_secret_key()
    app.config["DB_PATH"] = str(db_path or DB_PATH)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    mlb_client = client or MlbClient()

    def get_conn():
        return db.connect(app.config["DB_PATH"])

    @app.context_processor
    def inject_helpers():
        return {
            "format_record": format_record,
            "format_score": format_score,
            "format_slash": format_slash,
            "format_innings_pitched": format_innings_pitched,
            "team_by_id": team_by_id,
            "game_type_label": game_type_label,
        }

    @app.route("/")
    def dashboard():
        conn = get_conn()
        games = db.list_attended_games(conn)
        unmatched = db.list_unmatched_games(conn)
        unenriched = db.list_unenriched_games(conn)
        conn.close()
        return render_template(
            "dashboard.html",
            games=games[:10],
            game_count=len(games),
            unmatched=unmatched,
            unenriched=unenriched,
        )

    @app.route("/add", methods=["GET", "POST"])
    def add_game():
        teams = all_teams()
        form = _form_from_request()
        if request.method == "GET":
            return render_template("add.html", teams=teams, form=form, home_choices=[], away_choices=[])

        conn = get_conn()
        result = resolve_add(
            conn,
            date=form["date"],
            home_team=form["home_team"],
            away_team=form["away_team"],
            notes=form["notes"],
            venue=form["venue"],
            seat_section=form["seat_section"],
            seat_row=form["seat_row"],
            seat_seat=form["seat_seat"],
            client=mlb_client,
        )
        conn.close()

        if not result.ok:
            messages = []
            if result.error:
                messages.append(result.error)
            if form["home_team"] and not result.home.matches:
                messages.append(f"Unknown home team “{form['home_team']}”. Try BOS or Red Sox.")
            elif form["home_team"] and not result.home.unique:
                messages.append("Home team is ambiguous — pick one of the matches.")
            if form["away_team"] and not result.away.matches:
                messages.append(f"Unknown away team “{form['away_team']}”. Try NYY or Yankees.")
            elif form["away_team"] and not result.away.unique:
                messages.append("Away team is ambiguous — pick one of the matches.")
            for message in messages:
                flash(message, "error")
            return render_template(
                "add.html",
                teams=teams,
                form=form,
                home_choices=result.home.matches,
                away_choices=result.away.matches,
            )

        session["pending_add"] = result.pending.to_dict()
        return redirect(url_for("confirm"))

    @app.route("/confirm", methods=["GET"])
    def confirm():
        pending = _pending_from_session()
        if pending is None:
            flash("Start by entering a date plus a team, or both teams plus a year.", "error")
            return redirect(url_for("add_game"))
        return render_template("confirm.html", pending=pending)

    @app.route("/confirm/accept", methods=["POST"])
    def confirm_accept():
        pending = _pending_from_session()
        if pending is None:
            flash("That add expired. Enter the game again.", "error")
            return redirect(url_for("add_game"))
        game_pk = int(request.form["game_pk"])
        conn = get_conn()
        try:
            game_id = accept_candidate(conn, pending, game_pk, client=mlb_client)
        except AlreadyLoggedError as exc:
            conn.close()
            flash("That official game is already in your log.", "error")
            return redirect(url_for("game_detail", game_id=exc.attended_id))
        except (MlbError, ValueError) as exc:
            conn.close()
            flash(str(exc), "error")
            return redirect(url_for("confirm"))
        conn.close()
        session.pop("pending_add", None)
        flash("Game saved and enriched.", "ok")
        return redirect(url_for("game_detail", game_id=game_id))

    @app.route("/confirm/reject", methods=["POST"])
    def confirm_reject():
        pending = _pending_from_session()
        if pending is None:
            flash("That add expired. Enter the game again.", "error")
            return redirect(url_for("add_game"))
        game_pk = int(request.form["game_pk"])
        pending = reject_candidate(pending, game_pk)
        if not pending.candidates:
            session["pending_add"] = pending.to_dict()
            flash("Rejected the MLB match. Try a different date, or save a personal-only row.", "ok")
            return redirect(url_for("confirm"))
        session["pending_add"] = pending.to_dict()
        return redirect(url_for("confirm"))

    @app.route("/confirm/personal", methods=["POST"])
    def confirm_personal():
        pending = _pending_from_session()
        if pending is None:
            flash("That add expired. Enter the game again.", "error")
            return redirect(url_for("add_game"))
        full_date, _, _ = parse_date_input(pending.date)
        if not (full_date and pending.home_team and pending.away_team):
            flash("Saving without a match needs a date and both teams.", "error")
            return redirect(url_for("add_game", date=pending.date, home_team=pending.home_team, away_team=pending.away_team, notes=pending.notes))
        conn = get_conn()
        game_id = save_personal_only(conn, pending)
        conn.close()
        session.pop("pending_add", None)
        flash("Saved without an official MLB match. You can confirm it later.", "ok")
        return redirect(url_for("game_detail", game_id=game_id))

    @app.route("/games")
    def games():
        conn = get_conn()
        rows = db.list_attended_games(conn)
        conn.close()
        return render_template("games.html", games=rows)

    @app.route("/games/<int:game_id>")
    def game_detail(game_id: int):
        conn = get_conn()
        game = db.get_attended_with_details(conn, game_id)
        lineups = {"away": [], "home": []}
        starters = {"away": None, "home": None}
        home_runs = []
        if game and game.get("mlb_game_pk"):
            for row in db.list_player_game_stats(conn, game["mlb_game_pk"]):
                if row["started_game"]:
                    lineups[row["side"]].append(row)
                if row["started_pitching"]:
                    starters[row["side"]] = row
            for side in lineups:
                lineups[side].sort(key=lambda item: item["batting_order"] or 99)
            home_runs = db.list_game_events(conn, game["mlb_game_pk"], event_type="home_run")
        conn.close()
        if game is None:
            flash("Game not found.", "error")
            return redirect(url_for("games"))
        return render_template(
            "game.html",
            game=game,
            lineups=lineups,
            starters=starters,
            home_runs=home_runs,
        )

    @app.route("/games/<int:game_id>/notes", methods=["POST"])
    def update_notes(game_id: int):
        conn = get_conn()
        game = db.get_attended_game(conn, game_id)
        if game is None:
            conn.close()
            flash("Game not found.", "error")
            return redirect(url_for("games"))
        notes = (request.form.get("notes") or "").strip()
        db.update_attended_game(conn, game_id, {"notes": notes or None})
        conn.close()
        flash("Notes saved.", "ok")
        return redirect(url_for("game_detail", game_id=game_id))

    @app.route("/games/<int:game_id>/delete", methods=["POST"])
    def delete_game(game_id: int):
        conn = get_conn()
        deleted = db.delete_attended_game(conn, game_id)
        conn.close()
        if deleted is None:
            flash("Game not found.", "error")
        else:
            flash(
                f"Deleted {deleted['away_team']} @ {deleted['home_team']} on {deleted['date']}.",
                "ok",
            )
        return redirect(url_for("games"))

    @app.route("/games/<int:game_id>/match", methods=["POST"])
    def rematch_game(game_id: int):
        conn = get_conn()
        game = db.get_attended_game(conn, game_id)
        if game is None:
            conn.close()
            flash("Game not found.", "error")
            return redirect(url_for("games"))
        result = resolve_add(
            conn,
            date=game["date"],
            home_team=game["home_team"],
            away_team=game["away_team"],
            notes=game["notes"] or "",
            venue=game["venue"] or "",
            seat_section=game["seat_section"] or "",
            seat_row=game["seat_row"] or "",
            seat_seat=game["seat_seat"] or "",
            attended_game_id=game_id,
            client=mlb_client,
        )
        conn.close()
        if not result.ok or result.pending is None:
            flash("Could not resolve those teams again.", "error")
            return redirect(url_for("game_detail", game_id=game_id))
        session["pending_add"] = result.pending.to_dict()
        return redirect(url_for("confirm"))

    @app.route("/games/<int:game_id>/refresh", methods=["POST"])
    def refresh_game(game_id: int):
        conn = get_conn()
        game = db.get_attended_game(conn, game_id)
        if game is None or not game.get("mlb_game_pk"):
            conn.close()
            flash("Nothing to refresh.", "error")
            return redirect(url_for("game_detail", game_id=game_id))
        try:
            from tracker.enrich import enrich_game

            enrich_game(conn, game["mlb_game_pk"], client=mlb_client, force=True)
            flash("Refreshed official details from MLB.", "ok")
        except MlbError as exc:
            flash(str(exc), "error")
        conn.close()
        return redirect(url_for("game_detail", game_id=game_id))

    @app.route("/players")
    def players():
        selected = _selected_type_groups()
        conn = get_conn()
        rows = list_player_summaries(conn, selected)
        conn.close()
        return render_template(
            "players.html",
            batting_players=[row for row in rows if row["batting_games"]],
            pitching_players=[row for row in rows if row["pitching_games"]],
            batting_columns=BATTING_TABLE_COLUMNS,
            pitching_columns=PITCHING_TABLE_COLUMNS,
            type_groups=selected,
        )

    @app.route("/players/<int:player_id>")
    def player_detail(player_id: int):
        selected = _selected_type_groups()
        conn = get_conn()
        payload = player_page(conn, player_id, selected)
        conn.close()
        if payload is None:
            flash("Player not found.", "error")
            return redirect(url_for("players"))
        return render_template("player.html", player=payload, type_groups=selected)

    @app.route("/report")
    def report():
        selected = _selected_type_groups()
        conn = get_conn()
        payload = build_report(conn, type_groups=selected)
        conn.close()
        return render_template("report.html", report=payload)

    return app


def _selected_type_groups() -> list[str]:
    return parse_report_type_groups(
        request.args.getlist("type"),
        explicit="filter" in request.args,
    )


def _form_from_request() -> dict[str, str]:
    keys = ["date", "home_team", "away_team", "notes", "venue", "seat_section", "seat_row", "seat_seat"]
    if request.method == "GET":
        return {key: (request.args.get(key) or "") for key in keys}
    return {key: (request.form.get(key) or "").strip() for key in keys}


def _pending_from_session() -> PendingAdd | None:
    payload = session.get("pending_add")
    if not payload:
        return None
    return PendingAdd.from_dict(payload)


def _load_secret_key() -> str:
    ensure_data_dirs()
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key)
    return key


def run_server(host: str = "127.0.0.1", port: int = 5000, open_browser: bool = True) -> None:
    import webbrowser

    app = create_app()
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    app.run(host=host, port=port, debug=False)
