"""Reine Entscheidungsregeln fuer Moderationsaktionen - ohne Abhaengigkeiten.

Bewusst ein eigenes Modul ohne discord.py, Flask oder Datenbank: Die Regel hier
hat einen schwer deutbaren Ausfall verursacht (Kick, Ban und Timeout schlugen
fehl, obwohl der Bot ADMINISTRATOR hatte), und soll deshalb einzeln testbar sein
- ohne dass dafuer die gesamte Anwendung geladen werden muss.
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
            f"Discords Rollenhierarchie verhindert das: Die höchste Rolle des Nutzers "
            f"({target_role_name}, Position {target_role_position}) liegt nicht unter der "
            f"höchsten Rolle des Bots ({bot_role_name}, Position {bot_role_position}). "
            f"Das ist unabhängig von den Berechtigungen — auch Administrator hebt es nicht "
            f"auf. Abhilfe: In den Servereinstellungen unter Rollen die Bot-Rolle "
            f"{bot_role_name} über {target_role_name} schieben."
        )
    return None
