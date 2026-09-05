"""Die fünf Vergabewege nebeneinander — und was daran auffällt.

Hintergrund (Patrick, 04.09.2026): "ich kann an mehreren stellen im bot rechte
und rollendinge einstellen die irgendwie unabhaengig voneinander laufen. das
fuehrt ggf. zu fehlern wenn ein user den bot benutzen will. rolle ohne passende
berechtigung soll nicht passieren."

Teil D von Plan 12. Die Uebertragung in beide Richtungen laeuft seit dem
05.09.2026 (rollenabgleich.py und rechteabgleich.py), aber es gab keine Stelle,
an der man SIEHT, wie die Wege zusammenwirken.

Der Anlass fuer diese Datei ist ein Befund aus den echten Daten: Das Recht
`send_dm` zeigt auf die Moderator-Rolle, die Moderator-Rolle bringt `send_dm`
aber nicht mit. Wer das Recht bekommt, erhaelt die Rolle - und beim naechsten
Rechteabgleich nimmt der Bot das Recht wieder weg, weil keine seiner Rollen es
deckt. Uebrig bleibt eine Rolle ohne die passende Berechtigung, also genau der
Zustand, den Patrick ausschliessen wollte. Ein Fehler, den man ohne Uebersicht
nur bemerkt, wenn sich jemand beschwert.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import vergabewege  # noqa: E402

MODERATOR = "1240770829525192805"
MANAGER = "1240771507656069211"
IDEENSCHMIED = "1305878121731588148"
MEMBER = "1244256684633755699"
UNBEKANNT = "9999999999999999999"


def rolle(rolle_id, name, verwaltbar=True, position=10):
    return {"id": rolle_id, "name": name, "position": position, "verwaltbar": verwaltbar}


ROLLEN = [
    rolle(MANAGER, "Manager", verwaltbar=False, position=17),
    rolle(MODERATOR, "Moderator", position=15),
    rolle(MEMBER, "Member", position=12),
    rolle(IDEENSCHMIED, "Ideenschmied", position=11),
]


def daten(**abweichungen):
    """Ein sauberer Ausgangszustand, in dem NICHTS zu beanstanden ist.

    Jeder Test veraendert davon genau eine Sache. So steht in jedem Test, was
    den Befund ausgeloest hat - und ein Test, der nichts veraendert, muss ohne
    Befund durchlaufen.
    """
    grund = dict(
        rollen=ROLLEN,
        recht_zu_rolle={"moderate_members": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
        reaktionsrollen=[],
        verdiente_rollen=[],
    )
    grund.update(abweichungen)
    return grund


def arten(befunde):
    return [b.art for b in befunde]


# --- Der Ausgangszustand ist sauber -----------------------------------------


def test_sauberer_zustand_meldet_nichts():
    assert vergabewege.befunde(**daten()) == []


def test_leerer_zustand_meldet_nichts():
    assert vergabewege.befunde(
        rollen=ROLLEN, recht_zu_rolle={}, rolle_zu_rechten={}
    ) == []


def test_uebersicht_ohne_eintraege_ist_leer():
    """Rollen ohne jede Einstellung gehoeren nicht in die Uebersicht.

    Der Server hat siebzehn Rollen. Wuerde die Seite alle zeigen, verstecken
    die leeren Zeilen die wenigen interessanten.
    """
    assert vergabewege.uebersicht(
        rollen=ROLLEN, recht_zu_rolle={}, rolle_zu_rechten={}
    ) == []


# --- Weg 2 gegen Weg 3: der fehlende Rueckweg -------------------------------


def test_fehlender_rueckweg_wird_gemeldet():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ))
    assert arten(b) == ["rueckweg_fehlt"]


def test_fehlender_rueckweg_ist_hoch_wenn_das_recht_entzogen_wird():
    """Wenn irgendeine Rolle das Recht fuehrt, holt der Abgleich es zurueck.

    Genau das macht aus der Unstimmigkeit einen Kreislauf: vergeben, Rolle
    bekommen, Recht wieder verloren.
    """
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}, MANAGER: {"send_dm"}},
    ))
    treffer = [x for x in b if x.art == "rueckweg_fehlt"]
    assert treffer[0].schwere == vergabewege.HOCH


def test_fehlender_rueckweg_ist_nur_mittel_wenn_niemand_das_recht_fuehrt():
    """Haengt das Recht an gar keiner Rolle, bleibt es bestehen.

    Die beiden Karten widersprechen sich trotzdem, aber im Betrieb passiert
    nichts - deshalb eine Stufe darunter.
    """
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ))
    assert b[0].schwere == vergabewege.MITTEL


def test_rueckweg_vorhanden_meldet_nichts():
    assert vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"send_dm"}},
    )) == []


def test_leere_zuordnung_wird_uebergangen():
    """Eine leere role_id hebt die Zuordnung auf und ist kein Widerspruch."""
    assert vergabewege.befunde(**daten(recht_zu_rolle={"send_dm": ""})) == []


def test_is_admin_in_der_zuordnung_loest_keinen_rueckweg_befund_aus():
    """is_admin laeuft nie ueber eine Rolle - dafuer gibt es den eigenen Befund."""
    b = vergabewege.befunde(**daten(recht_zu_rolle={"is_admin": MODERATOR}))
    assert "rueckweg_fehlt" not in arten(b)


def test_der_befund_nennt_rolle_und_recht_im_klartext():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ))
    assert "send_dm" in b[0].titel and "Moderator" in b[0].titel
    assert "send_dm" in b[0].empfehlung and "Moderator" in b[0].empfehlung


def test_der_befund_zeigt_auf_die_betroffene_rolle():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ))
    assert b[0].rolle_id == MODERATOR


# --- is_admin ueber eine Rolle ----------------------------------------------


def test_admin_ueber_eine_rolle_wird_gemeldet():
    b = vergabewege.befunde(**daten(
        rolle_zu_rechten={MODERATOR: {"moderate_members", "is_admin"}},
    ))
    assert "admin_ueber_rolle" in arten(b)


def test_admin_ueber_eine_rolle_ist_immer_hoch():
    b = vergabewege.befunde(**daten(
        rolle_zu_rechten={MODERATOR: {"moderate_members", "is_admin"}},
    ))
    treffer = [x for x in b if x.art == "admin_ueber_rolle"]
    assert treffer[0].schwere == vergabewege.HOCH


def test_is_admin_zaehlt_nicht_als_rollenverwaltetes_recht():
    """Sonst wuerde der Abgleich ihn jemandem wegnehmen, dessen Rolle wegfaellt.

    NUR_VON_HAND kommt aus rechteabgleich - eine Quelle, damit die beiden
    Module nicht auseinanderlaufen koennen.
    """
    assert vergabewege._rollenverwaltete_rechte({MODERATOR: {"is_admin"}}) == set()


# --- Verwaiste Eintraege ----------------------------------------------------


def test_rolle_die_es_nicht_mehr_gibt_wird_gemeldet():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"moderate_members": UNBEKANNT},
        rolle_zu_rechten={UNBEKANNT: {"moderate_members"}},
    ))
    assert "verwaist" in arten(b)


def test_verwaiste_rolle_erzeugt_keine_weiteren_befunde():
    """Ein Eintrag ins Leere greift nie - alles andere daran ist gegenstandslos.

    Sonst haenge an einer geloeschten Rolle eine ganze Kette von Warnungen,
    von denen keine etwas bedeutet.
    """
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": UNBEKANNT},
        rolle_zu_rechten={},
        verdiente_rollen=[{"role_id": UNBEKANNT, "bedingung_art": "xp", "schwelle": 1}],
    ))
    assert [x for x in b if x.rolle_id == UNBEKANNT and x.art != "rueckweg_fehlt"] \
        == [x for x in b if x.art == "verwaist"]


def test_unbekannte_rolle_bekommt_einen_lesbaren_namen():
    z = vergabewege.uebersicht(**daten(
        recht_zu_rolle={"moderate_members": UNBEKANNT}, rolle_zu_rechten={},
    ))
    assert z[0].name == f"Rolle {UNBEKANNT}"
    assert z[0].vorhanden is False


# --- Rollen oberhalb des Bots -----------------------------------------------


def test_rolle_ueber_dem_bot_wird_gemeldet_wenn_sie_vergeben_werden_soll():
    """Discord laesst einen Bot nur Rollen unterhalb seiner eigenen vergeben.

    Die Zuordnung laesst sich einstellen, greift aber nie, und Discord sagt
    dazu nichts.
    """
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"moderate_members": MANAGER},
        rolle_zu_rechten={MANAGER: {"moderate_members"}},
    ))
    assert "unerreichbar" in arten(b)


def test_rolle_ueber_dem_bot_ist_still_wenn_sie_nur_rechte_mitbringt():
    """Zum Lesen einer Rolle braucht der Bot keine Hierarchie.

    Manager liegt ueber dem Bot und bringt neun Rechte mit - das funktioniert,
    weil der Bot die Rolle nur auswerten und nicht vergeben muss.
    """
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={},
        rolle_zu_rechten={MANAGER: {"moderate_members"}},
    ))
    assert "unerreichbar" not in arten(b)


# --- Dieselbe Rolle auf mehreren Wegen --------------------------------------


def test_zwei_vergabewege_fuer_dieselbe_rolle_werden_gemeldet():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"moderate_members": MEMBER},
        rolle_zu_rechten={},
        reaktionsrollen=[{"role_id": MEMBER, "emoji": "👍", "enabled": 1}],
    ))
    assert "doppelter_weg" in arten(b)


def test_rechte_einer_rolle_zaehlen_nicht_als_zweiter_vergabeweg():
    """`role_permissions` vergibt keine Rolle, es liest sie aus.

    Ohne diese Unterscheidung waere jede Rolle mit Rechten faelschlich als
    mehrfach vergeben gemeldet - also fast jede.
    """
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"moderate_members": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ))
    assert "doppelter_weg" not in arten(b)


def test_ein_einzelner_weg_meldet_nichts():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={},
        rolle_zu_rechten={},
        reaktionsrollen=[{"role_id": MEMBER, "emoji": "👍", "enabled": 1}],
    ))
    assert "doppelter_weg" not in arten(b)


# --- Kette: Leistung fuehrt zu Backend-Recht --------------------------------


def test_verdiente_rolle_mit_rechten_ist_ein_hinweis():
    """Oft genau so gewollt - der Ideenschmied darf Fragen einreichen.

    Trotzdem sichtbar: Eine Leistung im Spiel loest ein Recht im Backend aus.
    """
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={},
        rolle_zu_rechten={IDEENSCHMIED: {"submit_questions"}},
        verdiente_rollen=[{"role_id": IDEENSCHMIED, "bedingung_art": "quiz_beantwortet",
                           "schwelle": 100, "automatisch": 0}],
    ))
    treffer = [x for x in b if x.art == "kette"]
    assert treffer and treffer[0].schwere == vergabewege.HINWEIS


def test_reaktionsrolle_mit_rechten_ist_ebenfalls_eine_kette():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={},
        rolle_zu_rechten={MEMBER: {"submit_questions"}},
        reaktionsrollen=[{"role_id": MEMBER, "emoji": "👍", "enabled": 1}],
    ))
    assert "kette" in arten(b)


def test_verdiente_rolle_ohne_rechte_ist_keine_kette():
    """Die elf kosmetischen Rollen sollen keine elf Warnungen erzeugen."""
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={},
        rolle_zu_rechten={},
        verdiente_rollen=[{"role_id": MEMBER, "bedingung_art": "tage_dabei",
                           "schwelle": 10, "automatisch": 1}],
    ))
    assert "kette" not in arten(b)


def test_die_kette_nennt_die_betroffenen_rechte():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={},
        rolle_zu_rechten={IDEENSCHMIED: {"submit_questions"}},
        verdiente_rollen=[{"role_id": IDEENSCHMIED, "bedingung_art": "quiz_beantwortet",
                           "schwelle": 100, "automatisch": 0}],
    ))
    assert "submit_questions" in b[0].text


# --- Reihenfolge ------------------------------------------------------------


def test_schwerste_befunde_stehen_oben():
    b = vergabewege.befunde(**daten(
        recht_zu_rolle={"send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}, MANAGER: {"send_dm"}},
        verdiente_rollen=[{"role_id": MODERATOR, "bedingung_art": "xp",
                           "schwelle": 1, "automatisch": 1}],
    ))
    schweren = [x.schwere for x in b]
    assert schweren == sorted(schweren, key=lambda s: vergabewege._RANG[s])


# --- Uebersicht -------------------------------------------------------------


def test_uebersicht_fuehrt_alle_wege_einer_rolle_zusammen():
    z = vergabewege.uebersicht(**daten(
        recht_zu_rolle={"moderate_members": MODERATOR, "send_dm": MODERATOR},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ))
    assert len(z) == 1
    assert len(z[0].wege) == 3


def test_uebersicht_zeigt_die_rechte_einer_rolle_sortiert():
    z = vergabewege.uebersicht(**daten(
        recht_zu_rolle={},
        rolle_zu_rechten={MODERATOR: {"send_dm", "moderate_members"}},
    ))
    assert z[0].rechte == ["moderate_members", "send_dm"]


def test_abgeschaltete_reaktionsregel_wird_als_solche_ausgewiesen():
    """Auch abgeschaltete Regeln zaehlen - sonst waere die Sperre weg, sobald
    jemand die Regel kurz deaktiviert."""
    z = vergabewege.uebersicht(**daten(
        recht_zu_rolle={}, rolle_zu_rechten={},
        reaktionsrollen=[{"role_id": MEMBER, "emoji": "👍", "enabled": 0}],
    ))
    assert "abgeschaltet" in z[0].wege[0].detail


def test_verdiente_rolle_zeigt_bedingung_und_schwelle():
    z = vergabewege.uebersicht(**daten(
        recht_zu_rolle={}, rolle_zu_rechten={},
        verdiente_rollen=[{"role_id": MEMBER, "bedingung_art": "tage_dabei",
                           "schwelle": 30, "automatisch": 1}],
    ))
    assert "tage_dabei ab 30" in z[0].wege[0].detail
    assert "automatisch" in z[0].wege[0].detail


def test_uebersicht_sortiert_nach_namen_verwaiste_ans_ende():
    z = vergabewege.uebersicht(**daten(
        recht_zu_rolle={"moderate_members": UNBEKANNT, "send_dm": MEMBER},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ))
    assert [x.name for x in z] == ["Member", "Moderator", f"Rolle {UNBEKANNT}"]


def test_weg_label_ist_lesbar():
    w = vergabewege.Weg(vergabewege.VERDIENT, "xp ab 5000")
    assert w.label == "Verdient"


def test_unbekannte_wegart_faellt_auf_ihren_schluessel_zurueck():
    assert vergabewege.Weg("etwas_neues", "").label == "etwas_neues"


# --- Weg 1: von Hand verliehene Rechte --------------------------------------


def test_handverliehene_rechte_sind_die_ohne_rolle():
    assert vergabewege.handverliehene_rechte(
        eigene_rechte={"moderate_members", "create_embed"},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ) == ["create_embed"]


def test_is_admin_gilt_nicht_als_handverliehenes_recht():
    """Er ist immer Handarbeit - ihn hier zu melden waere nur Rauschen."""
    assert vergabewege.handverliehene_rechte(
        eigene_rechte={"is_admin"}, rolle_zu_rechten={},
    ) == []


def test_ohne_sonderrechte_ist_die_liste_leer():
    assert vergabewege.handverliehene_rechte(
        eigene_rechte={"moderate_members"},
        rolle_zu_rechten={MODERATOR: {"moderate_members"}},
    ) == []


# --- Das Modul bleibt prüfbar ------------------------------------------------


@pytest.mark.parametrize("verboten", ["import discord", "import sqlite3", "import flask"])
def test_modul_bleibt_frei_von_schweren_abhaengigkeiten(verboten):
    """Sonst laesst es sich in der CI nicht mehr testen.

    Dort ist ausser pytest nichts installiert - und das ist Absicht, damit
    die Logik ohne Discord-Verbindung und ohne Datenbank pruefbar bleibt.
    """
    quelle = (REPO / "bot" / "vergabewege.py").read_text(encoding="utf-8")
    assert verboten not in quelle
