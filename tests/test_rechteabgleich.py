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


# --------------------------------------------------------------------------
# Rechte pro Person (Patrick, 05.09.2026)
#
#   "das heisst nicht jeder Moderator hat automatisch alle rechte im bot
#    backend oder beim bot. es gibt moderatoren die sich um den
#    schimpfwortfilter kuemmern oder welche die kicken koennen.. manche koennen
#    beides andere moderatoren koennen was anderes"
#
# Die Moderator-Rolle bringt seither KEINE Rechte mehr mit. Sie ist die
# Auszeichnung, die Befugnisse stehen einzeln in der Benutzerverwaltung. Damit
# das traegt, muss ein einzeln vergebenes Recht die Rollenabgleiche ueberleben.
# --------------------------------------------------------------------------

#: Der Zustand nach der Umstellung: Moderator ohne Buendel, Manager traegt noch
#: alles - dadurch bleiben die Rechte "rollenverwaltet" und damit entziehbar.
OHNE_BUENDEL = {MODERATOR: set(), MANAGER: SPIEGEL_MOD_RECHTE | {"send_dm"}}


def test_ein_einzeln_vergebenes_recht_ueberlebt_den_abgleich():
    """Der Kern der Umstellung. Ohne das waere die Einzelvergabe wertlos:
    Manager fuehrt dieselben Rechte, sie gelten also als rollenverwaltet und
    wuerden jedem entzogen, den keine Rolle deckt."""
    m = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=OHNE_BUENDEL,
        eigene_rechte={"manage_badword_filter"},
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen={"manage_badword_filter"},
    )
    assert m.entziehen == [] and m.vergeben == []


def test_der_schutz_gilt_auch_bei_von_hand_vergebener_rolle():
    """doc_loomis und sven tragen die Moderator-Rolle, seit ein Mensch sie
    ihnen gab - sie stehen in keinem Merker. Verlangte der Schutz eine vom Bot
    gesetzte Rolle, verloeren beide ihre Einzelrechte."""
    m = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=OHNE_BUENDEL,
        eigene_rechte={"moderate_members"},
        spiegelrollen=set(),          # ausdruecklich KEINE Spiegelrolle
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen={"moderate_members"},
    )
    assert m.entziehen == []


def test_die_rolle_bringt_keine_weiteren_rechte_mehr_mit():
    """Wer Moderator wird, bekommt dadurch nichts - das war der ganze Punkt."""
    m = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=OHNE_BUENDEL, eigene_rechte=set(),
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen=set(),
    )
    assert m.vergeben == []


def test_zwei_moderatoren_koennen_verschiedenes_duerfen():
    """Der Satz, um den es geht: manche kuemmern sich um den Filter, andere
    kicken. Beide behalten genau ihres."""
    filter_mod = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=OHNE_BUENDEL,
        eigene_rechte={"manage_badword_filter"},
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen={"manage_badword_filter"})
    kick_mod = rechteabgleich.massnahmen(
        rollen={MODERATOR}, rollenrechte=OHNE_BUENDEL,
        eigene_rechte={"moderate_members"},
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen={"moderate_members"})
    assert filter_mod.leer and kick_mod.leer


def test_ohne_die_rolle_geht_auch_das_einzelrecht():
    """Patricks Entscheidung vom 05.09.2026 auf die Frage, was beim
    Rollenentzug mit einzeln vergebenen Rechten geschieht: Sie gehen mit.
    Die Rolle bleibt das Signal, dass jemand ueberhaupt eine Aufgabe hat."""
    m = rechteabgleich.massnahmen(
        rollen=set(), rollenrechte=OHNE_BUENDEL,
        eigene_rechte={"manage_badword_filter"},
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen={"manage_badword_filter"},
    )
    assert m.entziehen == ["manage_badword_filter"]


def test_ein_recht_ohne_zugeordnete_rolle_ueberlebt():
    """Gegenprobe zu einem Fehler, der am 05.09.2026 echten Schaden anrichtete.

    `create_giveaway` stand in keiner Zuordnung. Wer es vergeben bekam, loeste
    damit keine Rolle aus - verlor also die Moderator-Auszeichnung. Und weil
    die Manager-Rolle dasselbe Recht fuehrt, galt es als rollenverwaltet und
    wurde ihm im selben Atemzug wieder entzogen. Am Ende hatte synthek gar
    nichts mehr und verschwand aus der Benutzerverwaltung.

    Ein Recht, das keine Rolle repraesentiert, kann auch an keiner haengen.
    """
    m = rechteabgleich.massnahmen(
        rollen=set(), rollenrechte={MANAGER: {"create_giveaway"}},
        eigene_rechte={"create_giveaway"},
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen={"create_giveaway"},
    )
    assert m.entziehen == []


def test_ein_zugeordnetes_recht_haengt_weiter_an_seiner_rolle():
    """Die Sicherung darf die Regel vom Vormittag nicht aushebeln: Was einer
    Rolle zugeordnet ist, gilt nur mit dieser Rolle."""
    m = rechteabgleich.massnahmen(
        rollen=set(), rollenrechte={MANAGER: {"moderate_members"}},
        eigene_rechte={"moderate_members"},
        zuordnung=SPIEGEL_ZUORDNUNG, handverliehen={"moderate_members"},
    )
    assert m.entziehen == ["moderate_members"]


# --------------------------------------------------------------------------
# Grundausstattung und Abweichung (Patrick, 05.09.2026)
#
#   "wenn man auf dem server zum Moderator gemacht wird hat man erstmal alle
#    Moderatoren rechte die ueber die rechteverwaltung eingestellt wurden aber
#    man kann auch einzelne rechte vergeben und entziehen"
#
# Zwei Ebenen: Die Rolle liefert eine Voreinstellung, davon wird pro Person
# abgewichen. Ein Abgleich, der stur alles nachzieht, was die Rolle mitbringt,
# macht jeden gezielten Entzug beim naechsten Lauf wieder rueckgaengig - und
# damit waere die zweite Ebene wertlos.
# --------------------------------------------------------------------------

GRUNDAUSSTATTUNG = {"manage_badword_filter", "manage_categories",
                    "moderate_members", "verify_questions"}
MIT_GRUNDAUSSTATTUNG = {MODERATOR: GRUNDAUSSTATTUNG,
                        MANAGER: GRUNDAUSSTATTUNG | {"send_dm", "create_embed"}}


def _mod(**abweichungen):
    grund = dict(rollen={MODERATOR}, rollenrechte=MIT_GRUNDAUSSTATTUNG,
                 eigene_rechte=set(), zuordnung=SPIEGEL_ZUORDNUNG,
                 handverliehen=set(), ausnahmen=set())
    grund.update(abweichungen)
    return rechteabgleich.massnahmen(**grund)


def test_wer_moderator_wird_bekommt_die_grundausstattung():
    assert set(_mod().vergeben) == GRUNDAUSSTATTUNG


def test_ein_bewusst_entzogenes_recht_wird_nicht_nachgezogen():
    """Der Kern von Plan 16. Ohne die Ausnahme kaeme das Recht beim naechsten
    Abgleich sofort zurueck, und kein Moderator liesse sich zuschneiden."""
    eigene = GRUNDAUSSTATTUNG - {"moderate_members"}
    m = _mod(eigene_rechte=eigene, handverliehen=eigene,
             ausnahmen={"moderate_members"})
    assert m.vergeben == []


def test_die_ausnahme_entzieht_nicht_zusaetzlich():
    """Sie verhindert nur das Nachziehen. Entzogen wurde das Recht schon."""
    eigene = GRUNDAUSSTATTUNG - {"moderate_members"}
    m = _mod(eigene_rechte=eigene, handverliehen=eigene,
             ausnahmen={"moderate_members"})
    assert m.entziehen == []


def test_zwei_moderatoren_mit_verschiedenem_zuschnitt():
    """Einer kuemmert sich um den Filter, einer kickt - beide behalten ihres."""
    nur_filter = GRUNDAUSSTATTUNG - {"moderate_members"}
    nur_kick = GRUNDAUSSTATTUNG - {"manage_badword_filter"}
    a = _mod(eigene_rechte=nur_filter, handverliehen=nur_filter,
             ausnahmen={"moderate_members"})
    b = _mod(eigene_rechte=nur_kick, handverliehen=nur_kick,
             ausnahmen={"manage_badword_filter"})
    assert a.leer and b.leer


def test_ein_zusatzrecht_ueber_die_grundausstattung_hinaus_bleibt():
    m = _mod(eigene_rechte=GRUNDAUSSTATTUNG | {"send_dm"}, handverliehen={"send_dm"})
    assert m.entziehen == []


def test_ohne_ausnahme_wird_die_luecke_wieder_gefuellt():
    """Die Gegenprobe: Fehlt die Markierung, holt der Abgleich das Recht
    zurueck. Genau dieses Verhalten war der Anlass fuer Plan 16."""
    eigene = GRUNDAUSSTATTUNG - {"moderate_members"}
    m = _mod(eigene_rechte=eigene, handverliehen=eigene, ausnahmen=set())
    assert m.vergeben == ["moderate_members"]


def test_ohne_die_rolle_wird_die_ausnahme_weggeraeumt():
    """Eine Ausnahme ohne Rolle bedeutet nichts mehr - ohne die Rolle gaebe es
    das Recht ohnehin nicht. Bliebe sie stehen, wuerde eine Entscheidung von
    damals die Person auch dann noch einschraenken, wenn sie die Rolle
    irgendwann neu bekommt."""
    m = _mod(rollen=set(), ausnahmen={"moderate_members"})
    assert m.ausnahmen_aufheben == ["moderate_members"]


def test_solange_die_rolle_da_ist_bleibt_die_ausnahme():
    m = _mod(eigene_rechte=GRUNDAUSSTATTUNG - {"moderate_members"},
             ausnahmen={"moderate_members"})
    assert m.ausnahmen_aufheben == []


def test_eine_aufzuraeumende_ausnahme_macht_die_massnahme_nicht_leer():
    """Sonst kaeme der Abgleich nie dazu, sie wegzuraeumen."""
    m = _mod(rollen=set(), ausnahmen={"moderate_members"})
    assert not m.leer


def test_die_datenbank_kennt_die_ausnahmespalte():
    quelle = (REPO / "bot" / "database.py").read_text(encoding="utf-8")
    assert '"user_permissions", "ausnahme"' in quelle
    assert "def ausnahmen(" in quelle


def test_lesende_abfragen_uebergehen_ausnahmen():
    """Eine Ausnahme ist kein Recht. Wuerde has_permission sie mitzaehlen,
    haette der Entzug im Backend gar keine Wirkung."""
    quelle = (REPO / "bot" / "database.py").read_text(encoding="utf-8")
    for stelle in ("AND ausnahme = 0 \"\n            \"AND permission_key IN (?, 'is_admin')",
                   "\"AND ausnahme = 0 ORDER BY permission_key\""):
        assert stelle in quelle, stelle


def test_der_abgleich_erzeugt_keine_ausnahmen():
    """bewusst=False: Was der Abgleich wegraeumt, ist keine Entscheidung eines
    Menschen und darf nicht dauerhaft blockieren."""
    assert "bewusst=False" in COG


def test_das_cog_reicht_die_ausnahmen_herein():
    assert "ausnahmen=database.ausnahmen(uid)" in COG


# --------------------------------------------------------------------------
# Die Fehler vom 05.09.2026 abends
#
# Patrick gab synthek nachtraeglich das Recht "Giveaway starten" - und danach
# war die Person aus der Benutzerverwaltung verschwunden. Der Audit-Log zeigt
# die Kette in zwei Sekunden:
#
#   19:34:22  set_user_permissions   create_giveaway
#   19:34:22  revoke_permission      create_giveaway
#   19:34:23  revoke_permission      manage_badword_filter
#   19:34:23  rechtesync             verliert: create_giveaway
#   19:34:23  rollen_angeglichen     -Moderator
#
# Vier Ursachen, die zusammenwirkten. Diese Tests halten jede einzeln fest.
# --------------------------------------------------------------------------

APP = (REPO / "backend" / "app.py").read_text(encoding="utf-8")
USERS_HTML = (REPO / "backend" / "templates" / "users.html").read_text(encoding="utf-8")


def test_das_formular_ergaenzt_statt_zu_ersetzen():
    """Das Formular zeigt nicht an, was jemand schon hat. Als "genau diese
    Rechte" gelesen, loeschte ein Nachtragen alles andere stillschweigend."""
    assert "set_user_permissions" not in APP
    assert "Ergaenzen, nicht ersetzen" in APP


def test_der_hinweistext_verspricht_nichts_falsches():
    assert "hinzugefügt" in USERS_HTML
    assert "werden entfernt" not in USERS_HTML


def test_die_benutzerverwaltung_kennt_modulrechte():
    """`submit_questions` kommt aus dem Quizmodul und stand deshalb weder in
    ALL_PERMISSIONS noch in PERMISSION_LABELS. Folge: In der Liste stand der
    rohe Schluessel, das X daneben tat nichts, und vergeben liess es sich
    ueber die Seite gar nicht."""
    assert "all_permissions=database.alle_berechtigungen()" in APP
    assert "permission_labels=_alle_rechte_labels()" in APP


def test_das_x_funktioniert_auch_fuer_modulrechte():
    stelle = APP[APP.index("def users_revoke_one"):]
    assert "database.alle_berechtigungen()" in stelle[:900]


def test_der_startabgleich_traegt_fehlende_rollen_nach():
    """Ohne die Gegenrichtung loeste der Start jede Unstimmigkeit zerstoerend
    auf: Wer ein Recht hatte, dessen Rolle fehlte, verlor das Recht."""
    assert "async def rollen_nachziehen" in COG
    assert "await self.rollen_nachziehen()" in COG


def test_die_gegenrichtung_laeuft_vor_der_ableitung():
    """Die Reihenfolge ist der ganze Punkt. Liefe sie danach, waeren die
    Rechte schon entzogen, bevor die Rollen nachgetragen werden."""
    assert COG.index("await self.rollen_nachziehen()") < COG.index("Abgleich nach Neustart")


def test_der_startabgleich_entzieht_keine_rollen():
    """Nur vergeben. Beim Start eine Rolle wegzunehmen, die vielleicht gerade
    erst von Hand vergeben wurde, waere der gleiche Fehler in gruen."""
    stelle = COG[COG.index("async def rollen_nachziehen"):COG.index("async def on_member_update")]
    assert "remove_roles" not in stelle
    assert "massnahme.entziehen" not in stelle


def test_der_besitzer_bleibt_beim_startabgleich_aussen_vor():
    stelle = COG[COG.index("async def rollen_nachziehen"):COG.index("async def on_member_update")]
    assert "is_protected_owner" in stelle


def test_der_merker_wird_vor_der_rollenvergabe_geschrieben():
    """Discord meldet die Aenderung sofort zurueck. Stuende der Merker danach,
    liefe on_member_update los, bevor die Zeile da ist - und leitete aus der
    frisch vergebenen Rolle die ganze Grundausstattung ab. Beobachtet am
    05.09.2026 im Log: erst drei Rechte dazu, eine Sekunde spaeter wieder weg.
    """
    for quelle, name in ((COG, "rechtesync.py"), (APP, "app.py")):
        stelle = quelle[quelle.index("merke_rollenvergabe"):]
        vor = quelle.index("merke_rollenvergabe")
        # Das add_roles, das zu dieser Vergabe gehoert, kommt danach.
        assert quelle.index("add_roles", vor) > vor, name
