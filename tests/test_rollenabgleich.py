"""Abgleich zwischen Backend-Rechten und Discord-Rollen (Plan 11, Teil A).

Bis zum 03.09.2026 waren das zwei getrennte Welten: Wer im Backend
`moderate_members` bekam, hatte deshalb noch lange keine Moderator-Rolle auf dem
Server. Dass etwas auseinanderlief, fiel erst auf, wenn sich jemand wunderte,
warum er etwas nicht darf.

Der Abgleich zeigt das an, ohne selbst etwas zu ändern — erst sehen, dann
handeln. Entsprechend heikel sind hier nicht Abstürze, sondern **falsche
Aussagen**: Eine Rolle als „überzählig" auszuweisen, die völlig in Ordnung ist,
verleitet dazu, sie jemandem wegzunehmen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

from rollenabgleich import abgleichen, zusammenfassung  # noqa: E402

# Beispielserver: Moderator und Supporter kann der Bot verwalten, Manager nicht.
MANAGER, MODERATOR, SUPPORTER, MEMBER = "700", "500", "400", "200"
VERWALTBAR = {MODERATOR, SUPPORTER, MEMBER}
ZUORDNUNG = {"moderate_members": MODERATOR, "create_embed": SUPPORTER, "is_admin": MANAGER}


def _ab(rechte, rollen, zuordnung=None):
    return abgleichen(
        rechte=set(rechte),
        rollen=set(rollen),
        zuordnung=zuordnung if zuordnung is not None else ZUORDNUNG,
        verwaltbare_rollen=VERWALTBAR,
    )


# --------------------------------------------------------------------------
# Der Normalfall
# --------------------------------------------------------------------------

def test_alles_passt():
    ergebnis = _ab({"moderate_members"}, {MODERATOR, MEMBER})
    assert ergebnis.passt
    assert zusammenfassung(ergebnis, {}) == "passt"


def test_fehlende_rolle_wird_erkannt():
    ergebnis = _ab({"moderate_members"}, {MEMBER})
    assert ergebnis.fehlend == [MODERATOR]
    assert not ergebnis.passt


def test_rolle_ohne_recht_wird_erkannt():
    ergebnis = _ab(set(), {MODERATOR})
    assert ergebnis.ueberzaehlig == [MODERATOR]


def test_ohne_zuordnung_passt_immer_alles():
    """Solange nichts zugeordnet ist, darf der Abgleich niemanden bemängeln -
    sonst stünden auf der Seite lauter Warnungen, bevor man überhaupt etwas
    eingestellt hat."""
    ergebnis = _ab({"is_admin", "send_dm"}, {MANAGER, MODERATOR, MEMBER}, zuordnung={})
    assert ergebnis.passt


# --------------------------------------------------------------------------
# Rollen, die den Bot nichts angehen
# --------------------------------------------------------------------------

def test_nicht_zugeordnete_rollen_gelten_nie_als_ueberzaehlig():
    """Der wichtigste Fall. Member und Server Booster stehen in keiner
    Zuordnung - sie als 'überzählig' zu melden, würde dazu verleiten, sie
    jemandem wegzunehmen. Das Backend hat mit ihnen schlicht nichts zu tun."""
    ergebnis = _ab({"moderate_members"}, {MODERATOR, MEMBER, "999", "888"})
    assert ergebnis.ueberzaehlig == []
    assert ergebnis.passt


def test_person_ganz_ohne_rollen_und_ohne_rechte():
    assert _ab(set(), set()).passt


# --------------------------------------------------------------------------
# Rollen über dem Bot
# --------------------------------------------------------------------------

def test_unverwaltbare_rolle_wird_getrennt_ausgewiesen():
    """Manager steht über dem Bot. Die Rolle als 'fehlend' zu melden wäre
    irreführend - hier hilft kein Knopfdruck, nur Handarbeit in Discord."""
    ergebnis = _ab({"is_admin"}, set())
    assert ergebnis.unverwaltbar == [MANAGER]
    assert ergebnis.fehlend == []


def test_zusammenfassung_unterscheidet_die_faelle():
    namen = {MANAGER: "Manager", MODERATOR: "Moderator", SUPPORTER: "Supporter"}
    text = zusammenfassung(_ab({"is_admin", "moderate_members"}, {SUPPORTER}), namen)
    assert "fehlt: Moderator" in text
    assert "nur von Hand: Manager" in text
    assert "ohne passendes Recht: Supporter" in text


# --------------------------------------------------------------------------
# Eine Rolle, mehrere Rechte
# --------------------------------------------------------------------------

def test_eine_rolle_fuer_mehrere_rechte():
    """Zwei Moderationsrechte können beide die Moderator-Rolle bedeuten. Wer
    EINES davon hat, dem steht die Rolle zu - sonst würde das zweite Recht sie
    unerwartet wieder streitig machen."""
    zuordnung = {"moderate_members": MODERATOR, "manage_badword_filter": MODERATOR}
    assert _ab({"moderate_members"}, {MODERATOR}, zuordnung).passt
    assert _ab({"manage_badword_filter"}, {MODERATOR}, zuordnung).passt
    assert _ab({"moderate_members", "manage_badword_filter"}, {MODERATOR}, zuordnung).passt


def test_rolle_faellt_erst_weg_wenn_kein_recht_mehr_da_ist():
    zuordnung = {"moderate_members": MODERATOR, "manage_badword_filter": MODERATOR}
    ergebnis = _ab(set(), {MODERATOR}, zuordnung)
    assert ergebnis.ueberzaehlig == [MODERATOR]


# --------------------------------------------------------------------------
# Der Abgleich verändert nichts
# --------------------------------------------------------------------------

def test_eingaben_bleiben_unberuehrt():
    """Er zeigt nur an. Würde er seine Eingabemengen verändern, wirkten sich
    mehrere Aufrufe gegenseitig aus - und die Seite zeigte je nach Reihenfolge
    etwas anderes."""
    rechte, rollen = {"moderate_members"}, {MODERATOR, MEMBER}
    zuordnung = dict(ZUORDNUNG)
    _ab(rechte, rollen, zuordnung)
    assert rechte == {"moderate_members"}
    assert rollen == {MODERATOR, MEMBER}
    assert zuordnung == ZUORDNUNG


def test_mehrfacher_aufruf_liefert_dasselbe():
    a = _ab({"moderate_members"}, {MEMBER})
    b = _ab({"moderate_members"}, {MEMBER})
    assert (a.fehlend, a.ueberzaehlig, a.unverwaltbar) == (b.fehlend, b.ueberzaehlig, b.unverwaltbar)


# --------------------------------------------------------------------------
# Die Zuordnung selbst
# --------------------------------------------------------------------------

def test_nur_verwaltbare_rollen_lassen_sich_zuordnen():
    """Gegenprobe im Backend: Eine gesperrte Rolle darf auch über einen
    manipulierten Aufruf nicht in die Zuordnung geraten - sie würde beim
    Vergeben an Discord scheitern."""
    quelle = (REPO / "backend" / "app.py").read_text()
    block = quelle[quelle.index("def roles_mapping_save"):]
    block = block[: block.index("@app.route(\"/users\")")]
    assert "not in verwaltbar" in block, (
        "roles_mapping_save() prüft nicht, ob die Rolle unter der Bot-Rolle liegt."
    )


def test_zuordnungsaenderung_landet_im_audit_log():
    quelle = (REPO / "backend" / "app.py").read_text()
    block = quelle[quelle.index("def roles_mapping_save"):]
    block = block[: block.index("@app.route(\"/users\")")]
    assert "log_audit" in block


def test_unbekannte_berechtigung_wird_abgelehnt():
    """Ein Tippfehler würde sonst eine Zeile anlegen, die nie greift - und
    niemandem fällt auf warum."""
    quelle = (REPO / "bot" / "database.py").read_text()
    block = quelle[quelle.index("def set_permission_role"):]
    block = block[: block.index("def get_all_permissioned_users")]
    assert "ALL_PERMISSIONS" in block and "raise ValueError" in block


# --------------------------------------------------------------------------
# Vom Anzeigen zum Handeln (Plan 11, Teil A — 03.09.2026)
#
# Hier wird es ernst: Der Bot ändert jetzt wirklich Rollen. Die beiden
# Regeln, die den Unterschied zwischen brauchbar und gefährlich ausmachen,
# stehen deshalb im Mittelpunkt.
# --------------------------------------------------------------------------

from rollenabgleich import massnahmen  # noqa: E402


def _plan(rechte, rollen, vom_bot=(), zuordnung=None):
    return massnahmen(
        rechte=set(rechte),
        rollen=set(rollen),
        zuordnung=zuordnung if zuordnung is not None else ZUORDNUNG,
        verwaltbare_rollen=VERWALTBAR,
        vom_bot_vergeben=set(vom_bot),
    )


def test_fehlende_rolle_wird_vergeben():
    p = _plan({"moderate_members"}, set())
    assert p.vergeben == [MODERATOR]
    assert p.entziehen == []


def test_der_bot_nimmt_nur_weg_was_er_selbst_gab():
    """Die wichtigste Regel überhaupt. Ohne sie würde beim ersten Abgleich
    jedem die Moderator-Rolle entzogen, der sie von Hand bekommen hat."""
    von_hand = _plan(set(), {MODERATOR}, vom_bot=set())
    assert von_hand.entziehen == [], "Eine von Hand vergebene Rolle wurde entzogen."

    vom_bot = _plan(set(), {MODERATOR}, vom_bot={MODERATOR})
    assert vom_bot.entziehen == [MODERATOR]


def test_unverwaltbare_rollen_werden_nicht_angefasst():
    """Manager steht über dem Bot. Ihn einzuplanen hiesse, an einer
    Discord-Ablehnung zu scheitern - und zwar bei jedem Abgleich erneut."""
    p = _plan({"is_admin"}, set())
    assert MANAGER not in p.vergeben
    assert p.vergeben == []

    # Auch nicht beim Entziehen, selbst wenn er als selbst vergeben gilt
    p2 = _plan(set(), {MANAGER}, vom_bot={MANAGER})
    assert p2.entziehen == []


def test_nicht_zugeordnete_rollen_bleiben_unberuehrt():
    """Member und Server Booster gehen den Abgleich nichts an - auch dann
    nicht, wenn der Bot sie selbst vergeben hat (etwa per Reaktionsrolle)."""
    p = _plan(set(), {MEMBER, "999"}, vom_bot={MEMBER, "999"})
    assert p.entziehen == []


def test_passender_zustand_ergibt_keine_massnahme():
    p = _plan({"moderate_members"}, {MODERATOR}, vom_bot={MODERATOR})
    assert p.leer


def test_vergeben_und_entziehen_gleichzeitig():
    """Jemand verliert ein Recht und bekommt ein anderes - beides in einem Zug."""
    p = _plan({"create_embed"}, {MODERATOR}, vom_bot={MODERATOR})
    assert p.vergeben == [SUPPORTER]
    assert p.entziehen == [MODERATOR]


def test_ohne_zuordnung_passiert_nichts():
    p = _plan({"is_admin", "moderate_members"}, {MANAGER, MODERATOR},
              vom_bot={MANAGER, MODERATOR}, zuordnung={})
    assert p.leer


def test_planen_veraendert_nichts():
    """Es ist eine Vorschau. Würde sie ihre Eingaben verändern, zeigte die
    Seite je nach Reihenfolge etwas anderes an."""
    rechte, rollen, vom_bot = {"moderate_members"}, {MODERATOR}, {MODERATOR}
    massnahmen(rechte=rechte, rollen=rollen, zuordnung=ZUORDNUNG,
               verwaltbare_rollen=VERWALTBAR, vom_bot_vergeben=vom_bot)
    assert (rechte, rollen, vom_bot) == ({"moderate_members"}, {MODERATOR}, {MODERATOR})


# --------------------------------------------------------------------------
# Die Absicherungen im Backend
# --------------------------------------------------------------------------

def test_der_owner_wird_uebergangen():
    """Wie bei den Moderationsaktionen - der Bot-Besitzer bleibt unangetastet."""
    quelle = (REPO / "backend" / "app.py").read_text()
    block = quelle[quelle.index("def _rollen_anwenden"):]
    block = block[: block.index("def _massnahme_fuer")]
    assert "BOT_OWNER_ID" in block


def test_der_abgleich_zeigt_erst_und_handelt_dann():
    """Ein Knopf, der auf einen Schlag Rollen bei allen Mitgliedern ändert,
    muss vorher zeigen, was er tut."""
    quelle = (REPO / "backend" / "app.py").read_text()
    block = quelle[quelle.index("def roles_mapping_sync"):]
    block = block[: block.index("@app.route(\"/roles-mapping\")")]
    assert 'request.form.get("bestaetigt")' in block
    vorschau = block.index("roles_sync_preview.html")
    handeln = block.index("_rollen_anwenden")
    assert vorschau < handeln, "Erst die Vorschau, dann das Handeln."


def test_jede_vergabe_wird_vermerkt():
    """Ohne Merker könnte der Bot beim nächsten Abgleich nicht unterscheiden,
    was er selbst gab und was von Hand kam."""
    quelle = (REPO / "backend" / "app.py").read_text()
    block = quelle[quelle.index("def _rollen_anwenden"):]
    block = block[: block.index("def _massnahme_fuer")]
    assert "merke_rollenvergabe" in block
    assert "vergiss_rollenvergabe" in block


def test_aenderungen_landen_im_audit_log():
    quelle = (REPO / "backend" / "app.py").read_text()
    for funktion in ("_rollen_nachziehen", "roles_mapping_sync"):
        block = quelle[quelle.index(f"def {funktion}"):]
        block = block[:block.index("\n    @app.route") if "\n    @app.route" in block else 3000]
        assert "log_audit" in block, f"{funktion} protokolliert nicht."


def test_ohne_zuordnung_tut_das_nachziehen_nichts():
    """Solange nichts eingerichtet ist, darf eine Rechtevergabe keine
    Discord-Rolle anfassen."""
    quelle = (REPO / "backend" / "app.py").read_text()
    block = quelle[quelle.index("def _rollen_nachziehen"):]
    block = block[: block.index("roles-mapping/sync")]
    assert "if not zuordnung:" in block and "return" in block


# --------------------------------------------------------------------------
# Der Gegenweg zum Spiegel (Patrick, 05.09.2026)
#
#   "zusaetzlich wird der fall abgedeckt: rolle wird entzogen, keine
#    Moderatorenrechte mehr vergeben dann wird rolle auch entzogen"
#
# Wenn ein Recht die Rolle nach sich zieht, muss der Wegfall des Rechts die
# Rolle auch wieder mitnehmen - sonst bleibt auf dem Server eine Auszeichnung
# stehen, fuer die es im Backend keine Grundlage mehr gibt.
# --------------------------------------------------------------------------


def test_faellt_das_letzte_zugeordnete_recht_weg_geht_die_rolle_mit():
    p = _plan(set(), {MODERATOR}, vom_bot={MODERATOR})
    assert p.entziehen == [MODERATOR]


def test_solange_ein_zugeordnetes_recht_bleibt_bleibt_die_rolle():
    """Eine Rolle kann fuer mehrere Rechte stehen. Sie geht erst, wenn das
    letzte davon weg ist - sonst wuerde ein Entzug die Rolle streitig machen,
    obwohl ein anderes Recht sie weiterhin traegt."""
    p = _plan({"moderate_members"}, {MODERATOR}, vom_bot={MODERATOR})
    assert p.entziehen == []


def test_eine_von_hand_vergebene_rolle_bleibt_auch_ohne_jedes_recht():
    """Sie kann aus Gruenden vergeben worden sein, die das Backend nichts angehen."""
    p = _plan(set(), {MODERATOR}, vom_bot=set())
    assert p.entziehen == []
