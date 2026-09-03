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


def test_message_names_the_cause():
    """Die Meldung muss den Grund benennen - der urspruengliche Text ('Mir fehlt
    die Berechtigung') schickte auf die falsche Faehrte, weil die Berechtigungen
    tatsaechlich vollstaendig waren."""
    grund = _reason(target_pos=5, bot_pos=4)
    assert "Administrator" in grund, "muss klarstellen, dass Administrator nicht hilft"
    assert "Moderator" in grund and "Bot-Rolle" in grund, "muss beide Rollen benennen"


def test_message_raet_nicht_zum_hochziehen_der_bot_rolle():
    """Frueher endete die Meldung mit dem Rat, die Bot-Rolle in den
    Servereinstellungen ueber die Zielrolle zu schieben. Dieser Rat ist auf
    diesem Server FALSCH: Die Bot-Rolle steht absichtlich unter Moderator und
    Manager, damit das Moderationsteam nicht ueber den Bot entfernt werden kann
    (Entscheidung Patrick, 03.09.2026).

    Ein fehlgeschlagener Kick gegen ein Teammitglied ist also das gewuenschte
    Verhalten. Wer der Meldung folgte, wuerde genau die Schutzgrenze aufheben,
    die hier beabsichtigt ist."""
    grund = _reason(target_pos=5, bot_pos=4)
    assert "Servereinstellungen" not in grund, (
        "Die Meldung raet wieder dazu, die Rollen umzusortieren - das hebt eine "
        "absichtliche Sicherheitsgrenze auf."
    )
    assert "schieben" not in grund and "hochziehen" not in grund
    assert "gewollt" in grund or "geschützt" in grund, (
        "Die Meldung muss erklaeren, dass die Sperre beabsichtigt ist - sonst "
        "sucht der Moderator nach einem Fehler, den es nicht gibt."
    )


def test_message_nennt_den_richtigen_weg():
    """Statt die Rollen umzubauen: Ein Administrator handelt direkt in Discord."""
    grund = _reason(target_pos=5, bot_pos=4)
    assert "Administrator" in grund and "Discord" in grund
