"""Die Testbox: Was würde mit dieser Nachricht passieren?

Anlass (Plan 14, Teil 2, dritte Schwaeche): "Man sieht nicht, was der Filter
tut. Erst wenn jemand sich beschwert, merkt man, dass ein Wort zu scharf
eingestellt war."

Patrick am 06.09.2026: "ich sehe keine testbox wenn ich über die app auf das
backend zugreife? wo hast du das bitte eingebaut?" - zu Recht, sie war bis
dahin nur beschrieben. Diese Datei sichert, dass es sie gibt und vor allem,
dass sie ein Trockenlauf bleibt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import wortfilter  # noqa: E402

APP = (REPO / "backend" / "app.py").read_text(encoding="utf-8")
SEITE = (REPO / "backend" / "templates" / "moderation.html").read_text(encoding="utf-8")
ROUTE = APP[APP.index("def moderation_testen"):APP.index("def moderation_link_blacklist_add")]


# --- Die Wortpruefung ist aus dem Cog heraus ---------------------------------


def test_die_wortpruefung_liegt_in_einem_eigenen_modul():
    """Vorher stand sie mitten im Cog, zwischen Loeschen und Timeout - das
    Backend kam gar nicht heran, und genau deshalb konnte es die Testbox nicht
    geben."""
    assert (REPO / "bot" / "wortfilter.py").exists()


@pytest.mark.parametrize("verboten", ["import discord", "import sqlite3", "import flask"])
def test_das_modul_bleibt_frei_von_schweren_abhaengigkeiten(verboten):
    assert verboten not in (REPO / "bot" / "wortfilter.py").read_text(encoding="utf-8")


def test_das_cog_benutzt_dieselbe_pruefung():
    """Sonst urteilen Bot und Testbox verschieden - und die Box waere wertlos."""
    quelle = (REPO / "bot" / "cogs" / "moderation.py").read_text(encoding="utf-8")
    assert "from wortfilter import find_matched_word" in quelle


def test_wortgrenzen_bleiben_erhalten():
    """Der wichtigste Schutz vor Fehlalarmen: `ass` in `Klasse` schlaegt nicht
    an, `mist` in `Pferdemist` ebensowenig."""
    assert wortfilter.find_matched_word("eine gute Klasse", ["ass"]) is None
    assert wortfilter.find_matched_word("frischer Pferdemist", ["mist"]) is None
    assert wortfilter.find_matched_word("das ist doch Mist", ["mist"]) == "mist"


def test_verschleierte_schreibweisen_werden_erkannt():
    assert wortfilter.find_matched_word("sch.eiss", ["scheiss"])
    assert wortfilter.find_matched_word("s c h e i s s e", ["scheisse"])


# --- Die Box selbst ----------------------------------------------------------


def test_es_gibt_eine_route_und_ein_formular():
    assert '@app.route("/moderation/testen"' in APP
    assert "url_for('moderation_testen')" in SEITE


def test_die_box_darf_nur_wer_den_filter_verwaltet():
    kopf = APP[APP.index('@app.route("/moderation/testen"'):APP.index("def moderation_testen")]
    assert '@require_permission("manage_badword_filter")' in kopf


def test_die_box_prueft_beide_filter():
    assert "wortfilter.pruefe(" in ROUTE
    assert "linkfilter.pruefe_nachricht" in ROUTE


@pytest.mark.parametrize("verboten", ["log_badword_violation", "message.delete",
                                      "set_setting", "grant_permission"])
def test_die_box_veraendert_nichts(verboten):
    """Das ist der Kern. Ein Trockenlauf, der Verstoesse zaehlt oder Nachrichten
    loescht, waere schlimmer als keiner."""
    assert verboten not in ROUTE


def test_die_box_erklaert_die_annahme_bei_neuen_mitgliedern():
    """Sonst wundert sich jemand, warum die Regel im Test greift und im Chat
    nicht - oder umgekehrt."""
    assert "ungünstigste" in SEITE


def test_die_eingabe_ist_begrenzt():
    assert 'maxlength="500"' in SEITE and "text[:500]" in ROUTE


def test_das_ergebnis_ueberlebt_die_weiterleitung():
    """Die Route leitet auf die Seite zurueck; ohne Zwischenspeicher waere das
    Ergebnis weg, bevor es jemand sieht."""
    assert 'session["automod_test"]' in ROUTE
    assert 'session.pop("automod_test", None)' in APP


def test_die_box_steht_ueber_den_reitern():
    """Sie gilt fuer beide Filter und soll auffindbar sein - Patrick hat sie
    genau deshalb gesucht und nicht gefunden."""
    assert SEITE.index("Filter ausprobieren") < SEITE.index('id="automodTabs"')


# --------------------------------------------------------------------------
# Wortstufen und erlaubte Wendungen (06.09.2026)
#
# Patrick auf die Frage, ob Stufen den Pflegeaufwand wert sind: ja, mit je
# eigener Reaktion. Vorher war jedes Wort gleich schlimm - ein derber Fluch
# loeste denselben Timeout aus wie eine Beleidigung.
# --------------------------------------------------------------------------

DATABASE = (REPO / "bot" / "database.py").read_text(encoding="utf-8")
COG = (REPO / "bot" / "cogs" / "moderation.py").read_text(encoding="utf-8")


def test_es_gibt_drei_stufen():
    assert wortfilter.STUFEN == ("leicht", "mittel", "schwer")


def test_die_stufe_kommt_mit_dem_treffer_zurueck():
    assert wortfilter.pruefe("du arschloch", {"arschloch": "schwer"}).stufe == "schwer"


def test_ohne_eingetragene_stufe_gilt_die_standardstufe():
    assert wortfilter.pruefe("du arschloch", ["arschloch"]).stufe == wortfilter.STANDARDSTUFE


def test_der_bot_reagiert_je_nach_stufe():
    assert 'f"badword_action_{treffer.stufe}"' in COG


def test_die_bisherige_reaktion_wird_uebernommen():
    """Sonst aendert sich beim Umstieg still das Verhalten - bei einem Filter,
    der Nachrichten loescht und Leute stummschaltet, das Letzte was man will."""
    assert "_migriere_wortstufen" in DATABASE
    assert "badword_action_mittel" in DATABASE


def test_eine_erlaubte_wendung_entschaerft_den_treffer():
    assert not wortfilter.pruefe(
        "die analyse der bodenprobe", {"anal": "mittel"},
        ausnahmen=["analyse der bodenprobe"]).getroffen


def test_eine_wendung_laesst_sich_nicht_zum_durchschmuggeln_missbrauchen():
    """Der entscheidende Punkt. Zaehlte schon das blosse Vorkommen der Wendung,
    haenge man sie einfach an jede Beleidigung an."""
    assert wortfilter.pruefe(
        "analyse der bodenprobe du anal", {"anal": "mittel"},
        ausnahmen=["analyse der bodenprobe"]).getroffen


def test_ausnahmen_gelten_nicht_fuer_verschleiertes():
    """Wer 'a.n.a.l' schreibt, meint keine Bodenprobe."""
    assert wortfilter.pruefe(
        "an.al", {"anal": "mittel"}, ausnahmen=["analyse"]).getroffen


def test_die_ausnahmen_haben_eine_eigene_tabelle():
    assert "CREATE TABLE IF NOT EXISTS badword_ausnahmen" in DATABASE


def test_das_backend_kann_stufen_und_wendungen_pflegen():
    assert "moderation_word_stufe" in APP
    assert "moderation_ausnahme_add" in APP
    assert "moderation_ausnahme_remove" in APP


def test_die_testbox_nennt_stufe_und_reaktion():
    """Sonst sieht man zwar, DASS es anschlaegt, aber nicht was passiert."""
    assert "test_ergebnis.stufe" in SEITE and "test_ergebnis.reaktion" in SEITE


def test_eine_unbekannte_stufe_wird_abgewiesen():
    assert 'raise ValueError(f"Unbekannte Stufe' in DATABASE


# --------------------------------------------------------------------------
# Punktesystem (06.09.2026)
#
# Patrick: "man soll die Punkte einstellen können. machen wir 5. ein schwerer
# verstoß gibt 3 Punkte Mittel 2 und leicht 1 Punkt."
#
# Vorher zaehlte der Filter nur, WIE OFT jemand angeeckt ist. Drei "scheisse"
# wogen damit genauso schwer wie drei rassistische Beschimpfungen.
# --------------------------------------------------------------------------

CONFIG = (REPO / "bot" / "config.py").read_text(encoding="utf-8")


def test_es_gibt_punkte_je_stufe_und_eine_schwelle():
    for schluessel in ("badword_punkte_leicht", "badword_punkte_mittel",
                       "badword_punkte_schwer", "badword_punkte_schwelle"):
        assert schluessel in CONFIG


def test_die_verstoesse_merken_sich_die_stufe():
    """Ohne sie liessen sich keine Punkte summieren - die Tabelle wusste nur
    DASS, nicht WIE SCHWER."""
    assert '"badword_violations", "stufe"' in DATABASE
    assert "def verstoss_punkte" in DATABASE


def test_altbestand_zaehlt_als_mittel():
    """Verstoesse von vor der Umstellung haben keine Stufe. Sie einfach zu
    ignorieren wuerde jedem sein Konto leeren."""
    stelle = DATABASE[DATABASE.index("def verstoss_punkte"):][:2200]
    assert "wortfilter.STANDARDSTUFE" in stelle


def test_die_punkte_folgen_demselben_zeitfenster():
    """Verjaehrung und manueller Reset muessen fuer Punkte genauso gelten wie
    fuer die Zaehlung - sonst raeumt ein Reset die Verstoesse weg, das
    Punktekonto bleibt aber stehen."""
    stelle = DATABASE[DATABASE.index("def verstoss_punkte"):][:2200]
    assert "_violation_reset_key" in stelle
    assert "-30 days" in stelle
    assert "manual_only" in stelle


def test_auch_ein_leichter_verstoss_wird_geprueft():
    """Der Timeout haengt am Konto, nicht an der Reaktion dieser einen Stufe.
    Stuende die Pruefung hinter dem vorzeitigen Ausstieg bei "nur loeschen",
    koennte ein leichter Verstoss das Fass nie zum Ueberlaufen bringen."""
    stelle = COG[COG.index("async def _check_badwords"):]
    stelle = stelle[:stelle.index("@commands.Cog.listener")]
    ausstieg = stelle.index('if action != "delete_only"')
    pruefung = stelle.index("badword_punkte_schwelle")
    assert pruefung > ausstieg
    assert "return True" not in stelle[ausstieg:pruefung]


def test_der_timeout_nennt_die_einheit():
    """"3 Punkte" statt "3 Verstoesse" - sonst wundert sich jemand, warum er
    nach zwei Nachrichten stumm ist."""
    assert 'einheit="Punkte"' in COG


def test_analyse_bleibt_frei_anal_nicht():
    """Patricks Pruefstein am 06.09.2026: "der wortfilter der analyse nicht als
    schimpfwort durchgehen lässt anal aber schon"."""
    liste = {"anal": "leicht"}
    assert not wortfilter.pruefe("Die Analyse der Bodenprobe", liste).getroffen
    assert not wortfilter.pruefe("analytisch betrachtet", liste).getroffen
    assert wortfilter.pruefe("das ist doch anal", liste).getroffen


def test_das_backend_kann_die_punkte_einstellen():
    assert "punkte_schwelle" in APP and "badword_punkte_" in APP
    assert 'name="punkte_schwelle"' in SEITE


def test_null_punkte_sind_erlaubt():
    """0 heisst "zaehlt gar nicht mit" - eine sinnvolle Einstellung fuer
    leichte Woerter, die nur weggeraeumt werden sollen."""
    stelle = APP[APP.index('punkte = max(0, int(form.get(f"punkte_'):][:200]
    assert "max(0," in stelle


def test_die_testbox_nennt_die_punkte():
    assert "test_ergebnis.punkte" in SEITE


def test_die_alte_verstoss_schwelle_ist_weg():
    """`badword_timeout_threshold` (3 Verstoesse) wurde vom Punktekonto
    abgeloest. Patrick am 06.09.2026: "schwelle von 3 wird nichtmehr benötigt
    ist ja jetzt 5 Punkte." Bliebe das Feld in der Oberflaeche stehen, wuerde
    jemand daran drehen und sich wundern, dass nichts passiert.

    Der Linkfilter hat seine eigene Schwelle und ist davon nicht betroffen.
    """
    for quelle, name in ((APP, "app.py"), (SEITE, "moderation.html"), (CONFIG, "config.py")):
        assert "badword_timeout_threshold" not in quelle, name
    assert "timeout_threshold" not in SEITE.replace("link_threshold", "").replace("spam_threshold", "")
    assert "link_filter_timeout_threshold" in APP, "Der Linkfilter braucht seine Schwelle weiter"


# --------------------------------------------------------------------------
# Erkennung nachgeschärft (07.09.2026)
#
# Patrick: "ich habe einige dinge bei schimpfwortfilter versucht. zum beispiel
# hurensoohn würde durchgehen. oder arschlooch undsoweiter also da musst du
# nochmal viel besser nachbessern."
#
# Drei Luecken, die dabei ans Licht kamen.
# --------------------------------------------------------------------------

WORTFILTER = (REPO / "bot" / "wortfilter.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("eingabe", [
    "arschlooch", "arschloooch", "arschlochhh", "arrschloch",
    "hurensoohn", "huuurensohn", "fiick", "scheiiisse",
])
def test_gedehnte_schreibweisen_werden_erkannt(eingabe):
    """`normalize()` zog nur DREI und mehr gleiche Zeichen zusammen - doppelte
    ueberlebten. `arschlooch` ging damit glatt durch."""
    assert wortfilter.pruefe(eingabe, {"arschloch": "mittel", "hurensohn": "schwer",
                                       "fick": "mittel", "scheisse": "leicht"}).getroffen


def test_doppelte_buchstaben_werden_nicht_im_text_zusammengezogen():
    """Der naheliegende Weg waere gewesen, im Text jede Doppelung zu
    reduzieren. Das zerstoert die Sprache: Kaffee -> Kafe, Schiff -> Schif."""
    assert wortfilter.normalize("Kaffee Schiff alle") == "kaffee schiff alle"


@pytest.mark.parametrize("eingabe", ["arschloch!", "arschloch!!!", "du arschloch!"])
def test_satzzeichen_am_wortende_hebeln_die_erkennung_nicht_aus(eingabe):
    """Die Leetspeak-Tabelle macht aus `!` ein `i`. Aus `arschloch!` wurde
    dadurch `arschlochi` - die Wortgrenze war hin und das Wort ging durch,
    obwohl es voellig unverschleiert dastand."""
    assert wortfilter.pruefe(eingabe, {"arschloch": "mittel"}).getroffen


@pytest.mark.parametrize("eingabe", ["@rschloch", "hu$o", "n!gger"])
def test_leetspeak_mit_satzzeichen_greift_weiterhin(eingabe):
    """Die Zeichen einfach nicht mehr zu ersetzen waere die falsche Antwort -
    das hier sind echte Umgehungen. Deshalb zwei Fassungen."""
    liste = {"arschloch": "mittel", "huso": "schwer", "nigger": "schwer"}
    assert wortfilter.pruefe(eingabe, liste).getroffen


def test_es_gibt_zwei_fassungen_nur_wenn_noetig():
    """Im Normalfall - und das ist fast jede Nachricht - bleibt es bei einer."""
    assert len(wortfilter.varianten("ganz normaler text")) == 1
    assert len(wortfilter.varianten("arschloch!")) == 2


@pytest.mark.parametrize("eingabe", ["huren sohn", "huren  sohn", "du huren sohn du"])
def test_mit_leerzeichen_zerlegte_woerter_werden_erkannt(eingabe):
    """Die naheliegendste Umgehung ueberhaupt. Der Durchlauf fuer
    auseinandergezogene Schreibweisen greift dort nicht - der sucht nach
    EINZELNEN Buchstaben, nicht nach zwei ganzen Silben."""
    assert wortfilter.pruefe(eingabe, {"hurensohn": "schwer"}).getroffen


def test_kurze_woerter_werden_nicht_zusammengesetzt():
    """Sonst entstuenden Treffer aus zwei harmlosen Silben. Erst ab sechs
    Zeichen wird ueber ein Leerzeichen hinweg gesucht."""
    assert wortfilter.MINDESTLAENGE_GETEILT >= 6
    assert not wortfilter.pruefe("die ka cke", {"kacke": "leicht"}).getroffen


@pytest.mark.parametrize("satz", [
    "Ich hab neue Samen bestellt",
    "Die Analyse der Bodenprobe steht an",
    "Ich trinke Kaffee und schaue nach dem Schiff",
    "Ein Klasse Ergebnis, wirklich!",
    "Guten Morgen zusammen!",
    "Die Pflanze wächst geil, echt jetzt!",
    "Frischer Pferdemist als Dünger",
    "Der Ritter der Kokosnuss",
    "Die Mitte des Beetes ist zu nass",
    "Meine Hündin liegt im Beet",
])
def test_die_schaerfere_erkennung_erzeugt_keine_fehlalarme(satz):
    """Die Gegenprobe zu allem oben. Ein Filter, der harmlose Saetze loescht,
    ist schlimmer als einer, der etwas durchlaesst."""
    # Bewusst nur Woerter, die auch wirklich in der Liste stehen. "geil",
    # "samen" oder "mist" gehoeren nicht dazu - sie wurden am 06.09.2026
    # aussortiert, weil sie in einer Grow-Gruppe Alltagssprache sind.
    liste = {w: "mittel" for w in
             ("arschloch", "hurensohn", "kacke", "hure", "fick", "arsch",
              "titten", "analritter", "scheisse")}
    assert not wortfilter.pruefe(satz, liste).getroffen


def test_die_muster_sind_gepuffert():
    """Die Wortliste wird bei jeder Nachricht durchlaufen und aendert sich
    dabei selten."""
    assert "lru_cache" in WORTFILTER


# --- Probemeldung ------------------------------------------------------------


def test_es_gibt_eine_probemeldung():
    """Sonst liesse sich die Einstellung nur pruefen, indem man jemanden
    wirklich kickt - eine schlechte Art, eine Konfiguration zu testen."""
    assert "members_meldekanal_test" in APP
    assert '"test": (' in APP


def test_die_probemeldung_ist_als_probe_erkennbar():
    """Damit im Kanal niemand denkt, es sei etwas passiert."""
    stelle = APP[APP.index("def members_meldekanal_test"):][:1200]
    assert "Probemeldung" in stelle
