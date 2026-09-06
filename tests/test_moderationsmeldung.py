"""Kick, Bann, Entbannung und Timeout werden in einem Kanal gemeldet.

Patrick am 06.09.2026: "kick ban habe ich schon getestet das hat funktioniert
sollte aber auch angekündigt werden in einem kanal und den sollte man
einstellen können."

Bis dahin landeten Moderationsaktionen nur im Audit-Log des Bots - man musste
also wissen, dass etwas passiert ist, um es nachzusehen.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP = (REPO / "backend" / "app.py").read_text(encoding="utf-8")
CONFIG = (REPO / "bot" / "config.py").read_text(encoding="utf-8")
SEITE = (REPO / "backend" / "templates" / "members.html").read_text(encoding="utf-8")
HELFER = APP[APP.index("def _melde_moderation"):APP.index('@app.route("/members/<user_id>/kick"')]

AKTIONEN = ("kick", "ban", "unban", "timeout")


def test_der_kanal_ist_einstellbar():
    assert '"moderation_log_channel_id": ""' in CONFIG
    assert "members_meldekanal" in APP
    assert "channel-picker" in SEITE


def test_leerer_kanal_schaltet_die_meldung_ab():
    """Voreinstellung ist leer - wer nichts einstellt, bekommt auch nichts."""
    assert "if not kanal_id:\n            return" in HELFER


@pytest.mark.parametrize("aktion", AKTIONEN)
def test_jede_aktion_meldet(aktion):
    assert f'_melde_moderation("{aktion}"' in APP


@pytest.mark.parametrize("aktion", AKTIONEN)
def test_jede_aktion_hat_titel_und_farbe(aktion):
    zuordnung = APP[APP.index("MODERATIONS_MELDUNGEN = {"):]
    assert f'"{aktion}": (' in zuordnung[:400]


def test_die_meldung_kommt_erst_nach_erfolg():
    """Eine Meldung ueber einen Kick, der an Discords Rollenhierarchie
    gescheitert ist, waere schlimmer als keine."""
    stelle = APP[APP.index("def member_kick"):APP.index("def member_ban")]
    assert stelle.index("else:") < stelle.index('_melde_moderation("kick"')
    assert "except discord.Forbidden" in stelle[:stelle.index('_melde_moderation("kick"')]


def test_ein_fehlschlag_beim_melden_bricht_die_aktion_nicht_ab():
    """Gekickt ist gekickt, auch wenn der Meldekanal geloescht wurde."""
    assert "try:" in HELFER and "except Exception" in HELFER
    assert '"warning"' in HELFER


def test_der_moderator_wird_vor_dem_bot_thread_aufgeloest():
    """Auf dem Bot-Thread gibt es keinen Flask-Kontext - `session` waere dort
    nicht erreichbar."""
    vor = HELFER.index("moderator_id = session.get")
    assert vor < HELFER.index("async def _senden")


def test_die_meldung_nennt_das_wesentliche():
    for feld in ("Betroffen", "Moderator", "Grund"):
        assert f'name="{feld}"' in HELFER


def test_bann_und_timeout_nennen_den_zusatz():
    """Beim Bann der geloeschte Zeitraum, beim Timeout die Dauer - beides ist
    sonst nirgends im Kanal zu sehen."""
    assert 'zusatz=f"Nachrichten gelöscht: {loesch_label}"' in APP
    assert 'zusatz=f"{duration_minutes} Minuten"' in APP


def test_der_betroffene_wird_ueber_den_cache_aufgeloest():
    """Nach Kick oder Bann ist die Person kein Mitglied mehr - get_member
    liefert dann nichts, get_user meist noch den Namen."""
    assert "bot.get_user(int(user_id))" in HELFER


def test_die_aenderung_des_kanals_steht_im_audit_log():
    stelle = APP[APP.index("def members_meldekanal"):]
    assert "log_audit" in stelle[:900]
