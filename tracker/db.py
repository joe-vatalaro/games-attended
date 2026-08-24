from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracker.paths import DB_PATH, ensure_data_dirs

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS attended_games (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team_id INTEGER,
    away_team_id INTEGER,
    venue TEXT,
    seat_section TEXT,
    seat_row TEXT,
    seat_seat TEXT,
    notes TEXT,
    mlb_game_pk INTEGER,
    needs_review INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS attended_games_mlb_game_pk
    ON attended_games (mlb_game_pk)
    WHERE mlb_game_pk IS NOT NULL;

CREATE TABLE IF NOT EXISTS game_details (
    mlb_game_pk INTEGER PRIMARY KEY,
    official_date TEXT,
    season INTEGER,
    game_type TEXT,
    series_description TEXT,
    series_game_number INTEGER,
    venue_id INTEGER,
    venue_name TEXT,
    home_team_id INTEGER,
    away_team_id INTEGER,
    home_score INTEGER,
    away_score INTEGER,
    winning_team_id INTEGER,
    home_starter TEXT,
    away_starter TEXT,
    winning_pitcher TEXT,
    losing_pitcher TEXT,
    save_pitcher TEXT,
    attendance INTEGER,
    duration_minutes INTEGER,
    innings INTEGER,
    weather_condition TEXT,
    weather_temp TEXT,
    weather_wind TEXT,
    linescore_json TEXT,
    is_walkoff INTEGER NOT NULL DEFAULT 0,
    is_extra_innings INTEGER NOT NULL DEFAULT 0,
    is_no_hitter INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS player_game_stats (
    mlb_game_pk INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    team_id INTEGER,
    side TEXT NOT NULL,
    batting_order INTEGER,
    started_game INTEGER NOT NULL DEFAULT 0,
    started_pitching INTEGER NOT NULL DEFAULT 0,
    pa INTEGER,
    ab INTEGER,
    h INTEGER,
    doubles INTEGER,
    triples INTEGER,
    hr INTEGER,
    r INTEGER,
    rbi INTEGER,
    bb INTEGER,
    so INTEGER,
    sb INTEGER,
    hbp INTEGER,
    outs INTEGER,
    h_allowed INTEGER,
    r_allowed INTEGER,
    er INTEGER,
    bb_allowed INTEGER,
    so_pitched INTEGER,
    hr_allowed INTEGER,
    pitching_decision TEXT,
    PRIMARY KEY (mlb_game_pk, player_id)
);

CREATE TABLE IF NOT EXISTS game_events (
    id INTEGER PRIMARY KEY,
    mlb_game_pk INTEGER NOT NULL,
    at_bat_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    inning INTEGER,
    inning_half TEXT,
    batter_id INTEGER,
    batter_name TEXT,
    pitcher_id INTEGER,
    pitcher_name TEXT,
    description TEXT,
    rbi INTEGER,
    extra_json TEXT,
    UNIQUE (mlb_game_pk, at_bat_index)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    ensure_data_dirs()
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < 1:
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA user_version = 1")
    if current < 2:
        _add_column_if_missing(conn, "game_details", "series_description", "TEXT")
        _add_column_if_missing(conn, "game_details", "series_game_number", "INTEGER")
        conn.execute("PRAGMA user_version = 2")
    if current < 3:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS player_game_stats (
                mlb_game_pk INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                team_id INTEGER,
                side TEXT NOT NULL,
                batting_order INTEGER,
                started_game INTEGER NOT NULL DEFAULT 0,
                started_pitching INTEGER NOT NULL DEFAULT 0,
                pa INTEGER,
                ab INTEGER,
                h INTEGER,
                doubles INTEGER,
                triples INTEGER,
                hr INTEGER,
                r INTEGER,
                rbi INTEGER,
                bb INTEGER,
                so INTEGER,
                sb INTEGER,
                hbp INTEGER,
                outs INTEGER,
                h_allowed INTEGER,
                r_allowed INTEGER,
                er INTEGER,
                bb_allowed INTEGER,
                so_pitched INTEGER,
                hr_allowed INTEGER,
                pitching_decision TEXT,
                PRIMARY KEY (mlb_game_pk, player_id)
            );
            CREATE TABLE IF NOT EXISTS game_events (
                id INTEGER PRIMARY KEY,
                mlb_game_pk INTEGER NOT NULL,
                at_bat_index INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                inning INTEGER,
                inning_half TEXT,
                batter_id INTEGER,
                batter_name TEXT,
                pitcher_id INTEGER,
                pitcher_name TEXT,
                description TEXT,
                rbi INTEGER,
                extra_json TEXT,
                UNIQUE (mlb_game_pk, at_bat_index)
            );
            """
        )
        conn.execute("PRAGMA user_version = 3")
    conn.commit()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def insert_attended_game(conn: sqlite3.Connection, fields: dict[str, Any]) -> int:
    now = utc_now()
    columns = [
        "date",
        "home_team",
        "away_team",
        "home_team_id",
        "away_team_id",
        "venue",
        "seat_section",
        "seat_row",
        "seat_seat",
        "notes",
        "mlb_game_pk",
        "needs_review",
        "created_at",
        "updated_at",
    ]
    values = {column: fields.get(column) for column in columns}
    values["needs_review"] = int(bool(values.get("needs_review")))
    values["created_at"] = now
    values["updated_at"] = now
    placeholders = ", ".join(f":{column}" for column in columns)
    cursor = conn.execute(
        f"INSERT INTO attended_games ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_attended_game(conn: sqlite3.Connection, game_id: int, fields: dict[str, Any]) -> None:
    allowed = {
        "date",
        "home_team",
        "away_team",
        "home_team_id",
        "away_team_id",
        "venue",
        "seat_section",
        "seat_row",
        "seat_seat",
        "notes",
        "mlb_game_pk",
        "needs_review",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = :{key}" for key in updates)
    updates["id"] = game_id
    conn.execute(f"UPDATE attended_games SET {assignments} WHERE id = :id", updates)
    conn.commit()


def delete_attended_game(conn: sqlite3.Connection, game_id: int) -> dict[str, Any] | None:
    game = get_attended_game(conn, game_id)
    if game is None:
        return None
    game_pk = game.get("mlb_game_pk")
    conn.execute("DELETE FROM attended_games WHERE id = ?", (game_id,))
    if game_pk is not None:
        conn.execute("DELETE FROM game_details WHERE mlb_game_pk = ?", (game_pk,))
        conn.execute("DELETE FROM player_game_stats WHERE mlb_game_pk = ?", (game_pk,))
        conn.execute("DELETE FROM game_events WHERE mlb_game_pk = ?", (game_pk,))
    conn.commit()
    return game


def get_attended_game(conn: sqlite3.Connection, game_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM attended_games WHERE id = ?", (game_id,)).fetchone()
    return row_to_dict(row)


def get_attended_by_pk(conn: sqlite3.Connection, game_pk: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM attended_games WHERE mlb_game_pk = ?",
        (game_pk,),
    ).fetchone()
    return row_to_dict(row)


def list_attended_games(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.*, d.home_score, d.away_score, d.venue_name, d.official_date,
               d.winning_team_id, d.innings, d.duration_minutes, d.attendance,
               d.is_walkoff, d.is_extra_innings, d.is_no_hitter, d.status
        FROM attended_games a
        LEFT JOIN game_details d ON d.mlb_game_pk = a.mlb_game_pk
        ORDER BY COALESCE(d.official_date, a.date) DESC, a.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_unmatched_games(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM attended_games
        WHERE mlb_game_pk IS NULL
        ORDER BY date DESC, id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_unenriched_games(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.*
        FROM attended_games a
        LEFT JOIN game_details d ON d.mlb_game_pk = a.mlb_game_pk
        WHERE a.mlb_game_pk IS NOT NULL AND d.fetched_at IS NULL
        ORDER BY a.date DESC, a.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_game_details(conn: sqlite3.Connection, game_pk: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM game_details WHERE mlb_game_pk = ?",
        (game_pk,),
    ).fetchone()
    return row_to_dict(row)


def upsert_game_details(conn: sqlite3.Connection, details: dict[str, Any]) -> None:
    columns = [
        "mlb_game_pk",
        "official_date",
        "season",
        "game_type",
        "series_description",
        "series_game_number",
        "venue_id",
        "venue_name",
        "home_team_id",
        "away_team_id",
        "home_score",
        "away_score",
        "winning_team_id",
        "home_starter",
        "away_starter",
        "winning_pitcher",
        "losing_pitcher",
        "save_pitcher",
        "attendance",
        "duration_minutes",
        "innings",
        "weather_condition",
        "weather_temp",
        "weather_wind",
        "linescore_json",
        "is_walkoff",
        "is_extra_innings",
        "is_no_hitter",
        "status",
        "fetched_at",
    ]
    values = {column: details.get(column) for column in columns}
    values["is_walkoff"] = int(bool(values.get("is_walkoff")))
    values["is_extra_innings"] = int(bool(values.get("is_extra_innings")))
    values["is_no_hitter"] = int(bool(values.get("is_no_hitter")))
    values["fetched_at"] = values.get("fetched_at") or utc_now()
    placeholders = ", ".join(f":{column}" for column in columns)
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in columns if column != "mlb_game_pk"
    )
    conn.execute(
        f"""
        INSERT INTO game_details ({', '.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(mlb_game_pk) DO UPDATE SET {assignments}
        """,
        values,
    )
    conn.commit()


PLAYER_STAT_COLUMNS = [
    "mlb_game_pk",
    "player_id",
    "player_name",
    "team_id",
    "side",
    "batting_order",
    "started_game",
    "started_pitching",
    "pa",
    "ab",
    "h",
    "doubles",
    "triples",
    "hr",
    "r",
    "rbi",
    "bb",
    "so",
    "sb",
    "hbp",
    "outs",
    "h_allowed",
    "r_allowed",
    "er",
    "bb_allowed",
    "so_pitched",
    "hr_allowed",
    "pitching_decision",
]

GAME_EVENT_COLUMNS = [
    "mlb_game_pk",
    "at_bat_index",
    "event_type",
    "inning",
    "inning_half",
    "batter_id",
    "batter_name",
    "pitcher_id",
    "pitcher_name",
    "description",
    "rbi",
    "extra_json",
]


def replace_player_game_stats(
    conn: sqlite3.Connection, game_pk: int, rows: list[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM player_game_stats WHERE mlb_game_pk = ?", (game_pk,))
    placeholders = ", ".join(f":{column}" for column in PLAYER_STAT_COLUMNS)
    for row in rows:
        values = {column: row.get(column) for column in PLAYER_STAT_COLUMNS}
        values["mlb_game_pk"] = game_pk
        values["started_game"] = int(bool(values.get("started_game")))
        values["started_pitching"] = int(bool(values.get("started_pitching")))
        conn.execute(
            f"""
            INSERT INTO player_game_stats ({', '.join(PLAYER_STAT_COLUMNS)})
            VALUES ({placeholders})
            """,
            values,
        )
    conn.commit()


def replace_game_events(
    conn: sqlite3.Connection, game_pk: int, rows: list[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM game_events WHERE mlb_game_pk = ?", (game_pk,))
    placeholders = ", ".join(f":{column}" for column in GAME_EVENT_COLUMNS)
    for row in rows:
        values = {column: row.get(column) for column in GAME_EVENT_COLUMNS}
        values["mlb_game_pk"] = game_pk
        conn.execute(
            f"""
            INSERT INTO game_events ({', '.join(GAME_EVENT_COLUMNS)})
            VALUES ({placeholders})
            """,
            values,
        )
    conn.commit()


def list_player_game_stats(conn: sqlite3.Connection, game_pk: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM player_game_stats
        WHERE mlb_game_pk = ?
        ORDER BY side, batting_order IS NULL, batting_order, player_name
        """,
        (game_pk,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def list_game_events(
    conn: sqlite3.Connection,
    game_pk: int,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM game_events WHERE mlb_game_pk = ?"
    params: list[Any] = [game_pk]
    if event_type:
        sql += " AND event_type = ?"
        params.append(event_type)
    sql += " ORDER BY at_bat_index"
    return [row_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def get_player_name(conn: sqlite3.Connection, player_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT player_name FROM player_game_stats
        WHERE player_id = ?
        ORDER BY mlb_game_pk DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    return row["player_name"] if row else None


def get_attended_with_details(conn: sqlite3.Connection, game_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT a.*, d.official_date, d.season, d.game_type, d.series_description, d.series_game_number,
               d.venue_id, d.venue_name AS mlb_venue,
               d.home_score, d.away_score, d.winning_team_id, d.home_starter, d.away_starter,
               d.winning_pitcher, d.losing_pitcher, d.save_pitcher, d.attendance,
               d.duration_minutes, d.innings, d.weather_condition, d.weather_temp,
               d.weather_wind, d.linescore_json, d.is_walkoff, d.is_extra_innings,
               d.is_no_hitter, d.status, d.fetched_at
        FROM attended_games a
        LEFT JOIN game_details d ON d.mlb_game_pk = a.mlb_game_pk
        WHERE a.id = ?
        """,
        (game_id,),
    ).fetchone()
    return row_to_dict(row)
