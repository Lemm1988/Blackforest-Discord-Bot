"""Tests fuer Discords Rollenhierarchie bei Moderationsaktionen.

Kick, Ban und Timeout schlugen fehl, obwohl der Bot ADMINISTRATOR hatte. Discord
meldete nur ein generisches "Missing Permissions" (50013), die Oberflaeche zeigte
"Mir fehlt die Berechtigung" - wer daraufhin die Rechte kontrollierte, fand dort
alles Noetige und suchte an der falschen Stelle weiter.

Die tatsaechliche Regel steht in Discords Dokumentation
(https://docs.discord.com/developers/topics/permissions):

    "A bot can only kick, ban, and edit nicknames for users whose highest role is
    lower than the bot's highest role."

Die Rollenhierarchie ist also eine eigene Schranke NEBEN den Berechtigungen.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# Aus dem abhaengigkeitsfreien Modul, nicht aus app.py: Letzteres zieht discord.py
# und Flask mit und laesst sich nur als Paket importieren.
from moderation_rules import moderation_block_reason  # noqa: E402


def _reason(target_pos: int, bot_pos: int, owner: bool = False):
    return moderation_block_reason(
        is_guild_owner=owner,
        target_role_name="Moderator",
        target_role_position=target_pos,
        bot_role_name="Bot-Rolle",
        bot_role_position=bot_pos,
    )


def test_target_below_bot_is_allowed():
    assert _reason(target_pos=3, bot_pos=4) is None


def test_target_above_bot_is_blocked():
    """Der reale Fall: Bot auf Position 4, Moderator auf 5."""
    grund = _reason(target_pos=5, bot_pos=4)
    assert grund is not None
    assert "Rollenhierarchie" in grund


def test_equal_position_is_blocked():
    """Gleiche Position genuegt NICHT - die Rolle des Ziels muss echt niedriger
    liegen. Ein `>` statt `>=` waere hier ein stiller Fehler."""
    assert _reason(target_pos=4, bot_pos=4) is not None


def test_server_owner_can_never_be_moderated():
    """Auch wenn die Rollen es erlauben wuerden: Der Server-Eigentuemer ist
    grundsaetzlich unantastbar."""
    grund = _reason(target_pos=1, bot_pos=9, owner=True)
    assert grund is not None
    assert "Eigentümer" in grund


def test_message_names_the_cause_and_the_fix():
    """Die Meldung muss den Grund benennen UND sagen, was zu tun ist - der
    urspruengliche Text ('Mir fehlt die Berechtigung') schickte auf die falsche
    Faehrte, weil die Berechtigungen tatsaechlich vollstaendig waren."""
    grund = _reason(target_pos=5, bot_pos=4)
    assert "Administrator" in grund, "muss klarstellen, dass Administrator nicht hilft"
    assert "Servereinstellungen" in grund, "muss den Loesungsweg nennen"
    assert "Moderator" in grund and "Bot-Rolle" in grund, "muss beide Rollen benennen"
