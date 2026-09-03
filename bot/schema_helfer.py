"""Kleine Schema-Werkzeuge — bewusst ohne jede Abhängigkeit ausser sqlite3.

Warum eine eigene Datei: Die Module (modul_quiz.py und Konsorten) brauchen
ensure_column() für ihre Migrationen. Läge es weiterhin in database.py, müssten
sie beim Laden database importieren — und das zieht config nach sich, das
dotenv und einen gesetzten DISCORD_TOKEN verlangt.

Genau daran ist die CI gescheitert: Sie installiert absichtlich nur pytest, und
das Laden der Modul-Metadaten (Schema, Achievements, Bedingungen) darf die
Laufzeitumgebung des Bots nicht voraussetzen. Lokal fiel es nicht auf, weil dort
alles installiert ist.
"""
from __future__ import annotations


def ensure_column(conn, table: str, column: str, coldef: str) -> None:
    """Fügt eine Spalte hinzu, falls sie fehlt.

    ALTER TABLE ADD COLUMN ist in SQLite nicht von sich aus wiederholbar wie
    CREATE TABLE IF NOT EXISTS - deshalb wird vorher nachgesehen.

    Kommt ohne Row-Fabrik aus: PRAGMA table_info liefert den Spaltennamen an
    Position 1. Auf `row["name"]` zu bauen setzte voraus, dass der Aufrufer
    seine Verbindung entsprechend eingerichtet hat.
    """
    vorhanden = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in vorhanden:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")
