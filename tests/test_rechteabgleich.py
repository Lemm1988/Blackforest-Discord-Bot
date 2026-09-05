"""Von der Discord-Rolle zum Backend-Recht.

Hintergrund (Patrick, 05.09.2026): "wenn ein user berechtigungen erhält in
beide richtungen setzen."

Auf dem Server standen zwei Mitglieder mit der Moderator-Rolle und ohne jede
Backend-Berechtigung, waehrend umgekehrt jemand saemtliche Moderationsrechte
hatte, ohne die Rolle zu tragen. Die Benutzerverwaltung zeigte beides als
Hinweis an - behoben wurde es nie, weil die Uebertragung nur auf Knopfdruck
lief und die Gegenrichtung ueberhaupt nicht existierte
(`grep -rn "on_member_update" bot/` fand keinen Treffer).

Diese Datei sichert die Entscheidungslogik der neuen Gegenrichtung. Sie ist der
gefaehrlichere der beiden Wege: Ein Fehler beim Vergeben gibt jemandem zu viel
Macht, ein Fehler beim Entziehen sperrt jemanden aus seinem eigenen Backend aus.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import rechteabgleich  # noqa: E402

MODERATOR = "1240770829525192805"
MANAGER = "1240771507656069211"
DEKO = "1305878121731588148"

# Nachgebildet nach der echten Zuordnung auf dem Server, damit die Tests an den
# tatsaechlichen Verhaeltnissen haengen und nicht an einem Wunschbild.
ROLLENRECHTE = {
    MODERATOR: {"moderate_members", "manage_badword_filter", "verify_questions",
                "manage_categories", "submit_questions"},
    MANAGER: {"moderate_members", "manage_badword_filter", "manage_game_settings",
              "create_giveaway", "create_embed", "send_dm", "verify_questions",
              "manage_categories", "submit_questions"},
    DEKO: {"submit_questions"},
}


def massnahme(**kw):
    grund = dict(rollen=set(), rollenrechte=ROLLENRECHTE, eigene_rechte=set())
    grund.update(kw)
    return rechteabgleich.massnahmen(**grund)


# --------------------------------------------------------------------------
# Ableiten
# --------------------------------------------------------------------------

def test_ohne_rollen_keine_rechte():
    assert rechteabgleich.abgeleitete_rechte(set(), ROLLENRECHTE) == set()


def test_unbekannte_rolle_bringt_nichts_mit():
    """Eine Rolle ohne Eintrag ist Dekoration - sie darf nicht stillschweigend
    irgendetwas mitbringen."""
    assert rechteabgleich.abgeleitete_rechte({"9999"}, ROLLENRECHTE) == set()


def test_mehrere_rollen_addieren_sich():
    ergebnis = rechteabgleich.abgeleitete_rechte({MODERATOR, MANAGER}, ROLLENRECHTE)
    assert ergebnis == ROLLENRECHTE[MANAGER]


def test_zweite_rolle_nimmt_der_ersten_nichts_weg():
    """Waere es ein Schnitt statt einer Vereinigung, wuerde eine harmlose
    Deko-Rolle einem Moderator seine Rechte entziehen."""
    ergebnis = rechteabgleich.abgeleitete_rechte({MODERATOR, DEKO}, ROLLENRECHTE)
    assert ROLLENRECHTE[MODERATOR] <= ergebnis


# --------------------------------------------------------------------------
# is_admin bleibt Handarbeit
# --------------------------------------------------------------------------

def test_is_admin_wird_nie_ueber_eine_rolle_vergeben():
    """Der Generalschluessel. has_permission() laesst damit alles durch - ein
    versehentlicher Rollenklick duerfte niemandem die volle Kontrolle ueber den
    Bot geben."""
    rollenrechte = {MODERATOR: {"is_admin", "moderate_members"}}
    m = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=rollenrechte, eigene_rechte=set())
    assert m.vergeben == ["moderate_members"]
    assert "is_admin" not in m.vergeben


def test_is_admin_wird_nie_ueber_eine_rolle_entzogen():
    """Die gefaehrlichere Haelfte: Wer den letzten Admin per Rollenaenderung
    entrechtet, sperrt alle aus."""
    m = rechteabgleich.massnahmen(
        rollen=set(), rollenrechte=ROLLENRECHTE, eigene_rechte={"is_admin"})
    assert m.entziehen == []


# --------------------------------------------------------------------------
# Vergeben - der Fall Sven777 und doc_loomis
# --------------------------------------------------------------------------

def test_moderatorrolle_bringt_die_moderationsrechte():
    m = massnahme(rollen={MODERATOR})
    assert set(m.vergeben) == ROLLENRECHTE[MODERATOR]
    assert m.entziehen == []


def test_wer_die_rechte_schon_hat_bekommt_nichts_doppelt():
    m = massnahme(rollen={MODERATOR}, eigene_rechte=ROLLENRECHTE[MODERATOR])
    assert m.leer


def test_zusaetzliche_handrechte_bleiben_unangetastet():
    """Jemand ist Moderator UND hat von Hand ein Recht bekommen, das in keiner
    Rollenzuordnung vorkommt. Der Abgleich darf das zweite nicht anfassen."""
    m = massnahme(rollen={MODERATOR},
                  eigene_rechte=ROLLENRECHTE[MODERATOR] | {"steht_in_keiner_rolle"})
    assert m.leer


# --------------------------------------------------------------------------
# Entziehen - hier entscheidet sich, ob es gefaehrlich wird
# --------------------------------------------------------------------------

def test_handverliehene_rollenrechte_gehen_mit_der_rolle(): 
    """Der Fall, der die urspruenglich vorsichtigere Regel abgeloest hat.

    Am 05.09.2026 entzog Patrick einem Mitglied die Moderator-Rolle auf dem
    Server. Im Backend blieben sieben Moderationsrechte stehen, weil er sie
    seinerzeit selbst von Hand eingetragen hatte und der Bot nur wegnahm, was
    er selbst gegeben hatte. Seine Ansage: "die rechte die er nur als moderator
    hat muessen entzogen werden."

    Ein Recht, das an einer Rolle haengt, gilt jetzt nur mit dieser Rolle -
    unabhaengig davon, wer es eingetragen hat.
    """
    m = massnahme(rollen=set(), eigene_rechte={"moderate_members", "send_dm"})
    assert set(m.entziehen) == {"moderate_members", "send_dm"}


def test_rechte_ausserhalb_jeder_rolle_bleiben_unangetastet():
    """Der Unterschied zwischen streng und unbrauchbar.

    Ein Admin muss jemandem weiterhin gezielt ein Recht geben koennen, ohne
    ihm eine Rolle zu verleihen. Nur Rechte, die ohnehin an einer Rolle
    haengen, unterliegen dem Abgleich - bei allen anderen waere gar nicht
    entscheidbar, ob sie zur Rolle gehoeren oder daneben stehen.
    """
    m = massnahme(rollen=set(), eigene_rechte={"nur_von_hand_vergeben"})
    assert m.entziehen == []


def test_welche_rechte_ueberhaupt_rollenverwaltet_sind():
    verwaltet = rechteabgleich.rollenverwaltete_rechte(ROLLENRECHTE)
    assert "moderate_members" in verwaltet
    assert "is_admin" not in verwaltet, "der Generalschluessel gehoert nie dazu"
    assert "gibtsnicht" not in verwaltet


def test_vom_bot_gesetzte_rechte_gehen_mit_der_rolle():
    m = massnahme(rollen=set(), eigene_rechte={"moderate_members"})
    assert m.entziehen == ["moderate_members"]


def test_nur_die_wirklich_verwaisten_rechte():
    """Wer von Manager auf Moderator zurueckgestuft wird, verliert genau die
    Rechte, die nur der Manager mitbrachte - nicht die gemeinsamen."""
    m = massnahme(rollen={MODERATOR}, eigene_rechte=ROLLENRECHTE[MANAGER])
    assert set(m.entziehen) == ROLLENRECHTE[MANAGER] - ROLLENRECHTE[MODERATOR]
    assert "moderate_members" not in m.entziehen, "gemeinsames Recht muss bleiben"


def test_der_besitzer_verliert_nie_etwas():
    """Nimmt ihm jemand versehentlich eine Rolle weg, darf er sich dadurch
    nicht aus seinem eigenen Backend aussperren."""
    m = massnahme(rollen=set(), eigene_rechte=ROLLENRECHTE[MANAGER], geschuetzt=True)
    assert m.entziehen == []


def test_der_besitzer_bekommt_aber_weiterhin_rechte():
    """Geschuetzt heisst "wird nicht beschnitten", nicht "wird uebergangen"."""
    m = massnahme(rollen={MODERATOR}, geschuetzt=True)
    assert set(m.vergeben) == ROLLENRECHTE[MODERATOR]


# --------------------------------------------------------------------------
# Begruendung fuers Audit-Log
# --------------------------------------------------------------------------

def test_begruendung_nennt_beide_richtungen():
    m = massnahme(rollen={MODERATOR}, eigene_rechte={"create_embed"})
    text = rechteabgleich.begruendung(m)
    assert "erhält:" in text and "verliert:" in text and "create_embed" in text


def test_begruendung_bei_nichts_zu_tun():
    assert rechteabgleich.begruendung(massnahme()) == "keine Änderung nötig"


# --------------------------------------------------------------------------
# Das Cog haengt die Logik auch wirklich ein
# --------------------------------------------------------------------------

COG = (REPO / "bot" / "cogs" / "rechtesync.py").read_text()


def test_es_gibt_den_listener_ueberhaupt():
    """Der Kern des gemeldeten Problems: Diese Richtung existierte nicht."""
    assert "async def on_member_update" in COG
    assert "rechteabgleich.massnahmen" in COG


def test_der_listener_prueft_erst_ob_sich_rollen_geaendert_haben():
    """on_member_update feuert auch fuer Spitznamen und Avatare. Ohne den
    Vergleich liefe der volle Abgleich bei jeder Kleinigkeit - auf einem Pi
    spuerbar."""
    assert "if vorher == nachher" in COG


def test_der_besitzerschutz_ist_verdrahtet():
    assert "is_protected_owner" in COG


def test_entzogene_rechte_beenden_die_sitzung():
    """Sonst wirkte ein Entzug erst beim naechsten Login weiter - jemand
    koennte mit offener Sitzung weiterarbeiten."""
    assert "invalidate_user_session" in COG


def test_jede_automatische_aenderung_landet_im_audit_log():
    """Wer spaeter fragt, warum jemand ein Recht verloren hat, soll die
    Antwort finden und nicht raten muessen."""
    assert 'log_audit' in COG and '"rechtesync"' in COG


def test_das_cog_wird_geladen():
    assert '"rechtesync"' in (REPO / "bot" / "main.py").read_text()


def test_startabgleich_laeuft_nur_einmal():
    """on_ready feuert nach jedem Verbindungsabbruch erneut. Ein voller
    Durchlauf ueber alle Mitglieder bei jedem Wackler waere unnoetige Last."""
    assert "_startabgleich_erledigt" in COG


# --------------------------------------------------------------------------
# Spiegelrollen (05.09.2026)
#
# Patrick vergab einem Mitglied NUR das Recht, den Schimpfwortfilter zu
# bearbeiten. Drei Sekunden spaeter hatte es fuenf Rechte:
#
#   13:27:29  manage_badword_filter   von: <Patrick>
#   13:27:32  manage_categories       von: rolle:<Moderator>
#   13:27:32  moderate_members        von: rolle:<Moderator>
#   13:27:32  submit_questions        von: rolle:<Moderator>
#   13:27:32  verify_questions        von: rolle:<Moderator>
#
# Ursache war ein Kreislauf: Das Recht brachte ueber permission_roles die
# Moderator-Rolle auf den Server, und diese Funktion leitete aus der frisch
# vergebenen Rolle gleich alle Rechte eines Moderators ab. Patrick dazu: "im
# bot wurden ihm alle rechte zugewiesen anstatt nur der schimpfwortfilter. das
# sollte nicht so sein."
# --------------------------------------------------------------------------

SPIEGEL_MOD_RECHTE = {"manage_badword_filter", "manage_categories", "moderate_members",
              "verify_questions", "submit_questions"}
SPIEGEL_ROLLENRECHTE = {MODERATOR: SPIEGEL_MOD_RECHTE}
#: permission_roles: welches Recht welche Rolle nach sich zieht.
SPIEGEL_ZUORDNUNG = {r: MODERATOR for r in
             ("manage_badword_filter", "manage_categories", "moderate_members",
              "verify_questions", "send_dm")}


def _spiegelfall(**abweichungen):
    """Der Fall von synthek: ein Recht von Hand, die Rolle als dessen Anzeige."""
    grund = dict(
        rollen={MODERATOR},
        rollenrechte=SPIEGEL_ROLLENRECHTE,
        eigene_rechte={"manage_badword_filter"},
        spiegelrollen={MODERATOR},
        zuordnung=SPIEGEL_ZUORDNUNG,
        handverliehen={"manage_badword_filter"},
    )
    grund.update(abweichungen)
    return rechteabgleich.massnahmen(**grund)


def test_eine_spiegelrolle_vergibt_keine_weiteren_rechte():
    """Der eigentliche Befund: aus einem Recht wurden fuenf."""
    assert _spiegelfall().vergeben == []


def test_das_ausloesende_recht_bleibt_erhalten():
    """Sonst bliebe die Rolle uebrig und gar kein Recht mehr.

    Ohne diesen Schutz griffe die aeltere Regel: `manage_badword_filter` haengt
    an einer Rolle, keine "echte" Rolle deckt es - also weg damit.
    """
    assert "manage_badword_filter" not in _spiegelfall().entziehen


def test_die_abgeleiteten_rechte_werden_wieder_entzogen():
    """Aufraeumen des Bestands, den der Kreislauf angelegt hat."""
    m = _spiegelfall(eigene_rechte={"manage_badword_filter"} | SPIEGEL_MOD_RECHTE)
    assert set(m.entziehen) == SPIEGEL_MOD_RECHTE - {"manage_badword_filter"}


def test_eine_echte_rolle_bringt_weiterhin_alle_rechte():
    """Wer die Moderator-Rolle von Hand in Discord bekommt, ist Moderator.

    Daran aendert sich nichts - sonst waere die Gegenrichtung nutzlos.
    """
    m = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=SPIEGEL_ROLLENRECHTE, eigene_rechte=set(),
        spiegelrollen=set(), zuordnung=SPIEGEL_ZUORDNUNG, handverliehen=set(),
    )
    assert set(m.vergeben) == SPIEGEL_MOD_RECHTE


def test_ohne_spiegelrollen_bleibt_alles_wie_vorher():
    """Rueckwaertskompatibel: die neuen Angaben sind optional."""
    ohne = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=SPIEGEL_ROLLENRECHTE, eigene_rechte=set())
    mit = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=SPIEGEL_ROLLENRECHTE, eigene_rechte=set(),
        spiegelrollen=set(), zuordnung={}, handverliehen=set())
    assert ohne.vergeben == mit.vergeben and ohne.entziehen == mit.entziehen


def test_der_schutz_endet_mit_der_rolle():
    """Patricks Regel vom selben Vormittag darf nicht kaputtgehen.

    Wird die Rolle in Discord entzogen, ist sie keine Spiegelrolle mehr,
    sondern gar keine - und die daran haengenden Rechte gehen mit, auch die
    von Hand eingetragenen.
    """
    m = _spiegelfall(rollen=set(), eigene_rechte={"manage_badword_filter"} | SPIEGEL_MOD_RECHTE)
    assert set(m.entziehen) == SPIEGEL_MOD_RECHTE


def test_ein_handrecht_ohne_zuordnung_bleibt_unangetastet():
    """Rechte, die in keiner Rollenzuordnung vorkommen, gehen den Abgleich nichts an."""
    m = _spiegelfall(eigene_rechte={"manage_badword_filter", "create_embed"})
    assert "create_embed" not in m.entziehen


def test_ein_vom_bot_gesetztes_recht_ist_nicht_geschuetzt():
    """Nur was ein Mensch eingetragen hat, gilt als Ausloeser.

    Sonst wuerde der Kreislauf sich selbst schuetzen: Die vier zu viel
    vergebenen Rechte zeigen ebenfalls auf die Moderator-Rolle.
    """
    m = _spiegelfall(
        eigene_rechte={"manage_badword_filter", "manage_categories"},
        handverliehen={"manage_badword_filter"},
    )
    assert m.entziehen == ["manage_categories"]


def test_der_besitzer_bleibt_auch_hier_unangetastet():
    m = _spiegelfall(eigene_rechte=SPIEGEL_MOD_RECHTE, geschuetzt=True)
    assert m.entziehen == []


def test_das_cog_reicht_die_spiegelrollen_herein():
    """Ohne diese Uebergabe rechnet die Logik zwar richtig, sieht aber nichts."""
    assert "bot_vergebene_rollen" in COG
    assert "spiegelrollen=" in COG
    assert "handverliehen=" in COG


def test_die_herkunft_uebergeht_spiegelrollen():
    """Kein Recht darf auf eine Rolle zurueckgefuehrt werden, die es selbst ausgeloest hat."""
    assert "if str(rolle.id) in spiegelrollen" in COG
