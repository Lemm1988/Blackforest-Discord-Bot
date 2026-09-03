"""Reine Entscheidungsregeln fuer Moderationsaktionen - ohne Abhaengigkeiten.

Bewusst ein eigenes Modul ohne discord.py, Flask oder Datenbank: Die Regel hier
hat einen schwer deutbaren Ausfall verursacht (Kick, Ban und Timeout schlugen
fehl, obwohl der Bot ADMINISTRATOR hatte), und soll deshalb einzeln testbar sein
- ohne dass dafuer die gesamte Anwendung geladen werden muss.

Wichtig zur Formulierung der Meldungen: Welche Rollen ueber der Bot-Rolle stehen,
ist eine bewusste Entscheidung und kein Konfigurationsfehler (Stand 03.09.2026:
Manager steht darueber, Moderator darunter). Ein fehlgeschlagener Kick gegen eine
geschuetzte Person ist deshalb das gewuenschte Verhalten. Die Meldung erklaert
das und raet NICHT dazu, die Bot-Rolle hochzuziehen - jede Erhoehung vergroessert
den Kreis derer, die der Bot entfernen kann.
"""
from __future__ import annotations


def moderation_block_reason(
    *,
    is_guild_owner: bool,
    target_role_name: str,
    target_role_position: int,
    bot_role_name: str,
    bot_role_position: int,
) -> str | None:
    """Entscheidet, ob Discord eine Moderationsaktion von vornherein ablehnen wird.

    Die Regel steht so in Discords Dokumentation
    (https://docs.discord.com/developers/topics/permissions):

        "A bot can only kick, ban, and edit nicknames for users whose highest
        role is lower than the bot's highest role."

    Die Rollenhierarchie ist damit eine eigene Schranke NEBEN den Berechtigungen -
    auch ADMINISTRATOR hebt sie nicht auf. Gleiche Position genuegt ebenfalls
    nicht, die Rolle des Ziels muss echt niedriger liegen.

    Gibt den Grund im Klartext zurueck, oder None, wenn nichts dagegen spricht.
    """
    if is_guild_owner:
        return (
            "Das ist der Server-Eigentümer — den kann in Discord grundsätzlich niemand "
            "kicken, bannen oder timeouten, auch kein Administrator."
        )
    if target_role_position >= bot_role_position:
        return (
            f"Diese Person ist vor Aktionen des Bots geschützt: Ihre höchste Rolle "
            f"({target_role_name}, Position {target_role_position}) liegt nicht unter der "
            f"Bot-Rolle ({bot_role_name}, Position {bot_role_position}). Discords "
            f"Rollenhierarchie verhindert die Aktion unabhängig von allen Berechtigungen — "
            f"auch Administrator hebt sie nicht auf. "
            f"Das ist so gewollt: Wer über der Bot-Rolle steht, ist bewusst vor Aktionen "
            f"des Bots geschützt. Wenn wirklich gegen diese Person vorgegangen werden soll, "
            f"muss ein Administrator das direkt in Discord tun."
        )
    return None
