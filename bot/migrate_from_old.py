"""
Einmaliges, idempotentes Migrationsskript: übernimmt Fragen/Spieler/Antworten aus der
alten games.db in die neue bot.db. Die alte Datenbank wird ausschliesslich gelesen,
niemals verändert.

Aufruf: python3 migrate_from_old.py
"""
import sqlite3

import database

OLD_GAMES_DB = "/home/picom/Desktop/My-Discord-Bot/Bot1/games.db"


def main():
    database.init_db()

    old_conn = sqlite3.connect(OLD_GAMES_DB)
    old_conn.row_factory = sqlite3.Row

    q_count = p_count = a_count = 0

    with database.get_connection() as new_conn:
        for row in old_conn.execute("SELECT * FROM questions"):
            cur = new_conn.execute(
                "SELECT 1 FROM questions WHERE id = ?", (row["id"],)
            ).fetchone()
            if cur:
                continue
            new_conn.execute(
                """INSERT INTO questions
                   (id, question, option_a, option_b, option_c, option_d, correct_option,
                    submitted_by, verified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["id"], row["question"], row["option_a"], row["option_b"],
                    row["option_c"], row["option_d"], row["correct_option"],
                    str(row["submitted_by"]) if row["submitted_by"] is not None else None,
                    row["verified"],
                ),
            )
            q_count += 1

        for row in old_conn.execute("SELECT * FROM players"):
            new_conn.execute(
                "INSERT OR IGNORE INTO players (user_id, score) VALUES (?, ?)",
                (str(row["user_id"]), row["score"]),
            )
            p_count += 1

        for row in old_conn.execute("SELECT * FROM answered_questions"):
            new_conn.execute(
                "INSERT OR IGNORE INTO answered_questions (user_id, question_id) VALUES (?, ?)",
                (str(row["user_id"]), row["question_id"]),
            )
            a_count += 1

        new_conn.commit()

    old_conn.close()

    print(f"Migration abgeschlossen: {q_count} Fragen, {p_count} Spieler, {a_count} beantwortete Fragen neu importiert.")
    with database.get_connection() as conn:
        total_q = conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
        total_p = conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
        total_a = conn.execute("SELECT COUNT(*) c FROM answered_questions").fetchone()["c"]
    print(f"Gesamtstand in bot.db: {total_q} Fragen, {total_p} Spieler, {total_a} beantwortete Fragen.")


if __name__ == "__main__":
    main()
