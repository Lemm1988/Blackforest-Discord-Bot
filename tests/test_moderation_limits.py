"""Grenzwerte der Moderations- und Nachrichten-Endpunkte gegen die Discord-Doku.

Ergaenzt test_discord_limits.py (Embeds) um die Regeln, die an anderen Stellen
der API gelten. Alle Zahlen am 03.09.2026 nachgeschlagen unter
https://docs.discord.com/developers/resources/guild (Modify Guild Member) und
https://docs.discord.com/developers/resources/audit-log (X-Audit-Log-Reason).

Zusaetzlich abgesichert: die einzige Stelle im Projekt, an der Spaltennamen per
String in SQL eingesetzt werden. Das ist dort korrekt geloest (feste Whitelist,
Pruefung vor dem Einsetzen), aber es ist die Art Konstruktion, die beim naechsten
Erweitern kippt - deshalb ein Test, der das Muster erzwingt statt es nur zu
beschreiben.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP_PY = REPO / "backend" / "app.py"
DATABASE_PY = REPO / "bot" / "database.py"

# Discord: communication_disabled_until darf hoechstens 28 Tage in der Zukunft
# liegen. 28 * 24 * 60 = 40320 Minuten.
TIMEOUT_MAX_MINUTEN = 28 * 24 * 60


def _funktion(quelle: str, name: str) -> ast.AST:
    """Findet eine Funktion beliebiger Verschachtelungstiefe. Die Routen liegen
    innerhalb von create_app(), ein Blick nur auf die oberste Ebene fande sie
    nicht."""
    for node in ast.walk(ast.parse(quelle)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Funktion {name!r} nicht gefunden - umbenannt oder entfernt?")


def _zahlen(node: ast.AST) -> set[int]:
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, int)}


# --------------------------------------------------------------------------
# Timeout
# --------------------------------------------------------------------------

def test_28_tage_sind_40320_minuten():
    """Absicherung der Umrechnung selbst - die Zahl 40320 sagt beim Lesen nicht,
    woher sie kommt, und ein Vertipper faellt sonst niemandem auf."""
    assert TIMEOUT_MAX_MINUTEN == 40320


def test_timeout_wird_auf_28_tage_begrenzt():
    """Discord lehnt laengere Timeouts ab. Ohne Deckelung kaeme die Ablehnung erst
    aus der API und die Oberflaeche zeigte nur einen generischen Fehler."""
    fn = _funktion(APP_PY.read_text(), "member_timeout")
    assert TIMEOUT_MAX_MINUTEN in _zahlen(fn), (
        f"member_timeout() begrenzt die Dauer nicht auf {TIMEOUT_MAX_MINUTEN} Minuten "
        "(28 Tage). Discord weist laengere Timeouts zurueck."
    )


def test_timeout_hat_eine_untergrenze():
    """0 Minuten wuerde einen bestehenden Timeout aufheben statt einen zu setzen -
    ein stiller Fehlgriff, wenn jemand das Feld leer laesst."""
    fn = _funktion(APP_PY.read_text(), "member_timeout")
    assert 1 in _zahlen(fn), "member_timeout() hat keine Untergrenze von 1 Minute."


# --------------------------------------------------------------------------
# Direktnachrichten
# --------------------------------------------------------------------------

def test_dm_begrenzt_auf_2000_zeichen():
    """Discords Nachrichtenlimit fuer Konten ohne Nitro. Laengeres wird von der
    API mit Invalid Form Body abgelehnt."""
    fn = _funktion(APP_PY.read_text(), "send_dm")
    assert 2000 in _zahlen(fn), "send_dm() prueft die Nachrichtenlaenge nicht gegen 2000."


def test_dm_meldet_geschlossene_direktnachrichten_verstaendlich():
    """Discord antwortet mit 403 Forbidden, wenn der Empfaenger keine DMs von
    Servermitgliedern annimmt. Das ist kein Fehler des Absenders und muss als
    solcher erklaert werden, sonst sucht man den Fehler bei sich."""
    quelle = ast.get_source_segment(APP_PY.read_text(), _funktion(APP_PY.read_text(), "send_dm"))
    assert "discord.Forbidden" in quelle, "send_dm() faengt discord.Forbidden nicht ab."
    assert "NotFound" in quelle, "send_dm() unterscheidet nicht zwischen 'kein Nutzer' und 'DMs zu'."


# --------------------------------------------------------------------------
# Spaltennamen in SQL
# --------------------------------------------------------------------------

WHITELIST_FUNKTIONEN = [
    ("increment_user_stat", "_VALID_USER_STATS"),
    ("increment_server_stat", "_VALID_SERVER_STATS"),
    ("get_server_stat_today", "_VALID_SERVER_STATS"),
]


@pytest.mark.parametrize("funktion, whitelist", WHITELIST_FUNKTIONEN)
def test_spaltenname_wird_vor_dem_einsetzen_geprueft(funktion, whitelist):
    """Diese drei Funktionen setzen einen Spaltennamen per String in SQL ein -
    parametrisieren geht bei Spaltennamen nicht. Das ist genau dann sicher, wenn
    der Name vorher gegen eine feste Liste geprueft wird. Faellt die Pruefung
    weg, entsteht eine SQL-Injection, sobald der Wert je aus einer Anfrage
    stammt.
    """
    quelle = DATABASE_PY.read_text()
    fn = _funktion(quelle, funktion)
    text = ast.get_source_segment(quelle, fn) or ""
    assert whitelist in text, (
        f"{funktion}() prueft den Spaltennamen nicht gegen {whitelist}."
    )
    assert "raise" in text, (
        f"{funktion}() lehnt unbekannte Spaltennamen nicht ab, sondern setzt sie ein."
    )


@pytest.mark.parametrize("funktion, whitelist", WHITELIST_FUNKTIONEN)
def test_pruefung_steht_vor_dem_ersten_sql(funktion, whitelist):
    """Reihenfolge zaehlt: Eine Whitelist, die erst nach dem execute() greift,
    schuetzt nichts."""
    quelle = DATABASE_PY.read_text()
    text = ast.get_source_segment(quelle, _funktion(quelle, funktion)) or ""
    assert text.index(whitelist) < text.index("execute"), (
        f"In {funktion}() steht die Whitelist-Pruefung nach dem ersten SQL-Aufruf."
    )


def test_whitelists_sind_nicht_leer():
    """Eine leere Whitelist wuerde jeden Aufruf ablehnen - die Zaehler liefen
    still nicht mehr mit, ohne dass etwas auffaellt."""
    baum = ast.parse(DATABASE_PY.read_text())
    gefunden = {}
    for node in baum.body:
        if isinstance(node, ast.Assign):
            for ziel in node.targets:
                if isinstance(ziel, ast.Name) and ziel.id.startswith("_VALID_"):
                    gefunden[ziel.id] = ast.literal_eval(node.value)
    assert gefunden, "Keine _VALID_-Whitelists in database.py gefunden."
    for name, werte in gefunden.items():
        assert werte, f"{name} ist leer."
        assert all(isinstance(w, str) and w.isidentifier() for w in werte), (
            f"{name} enthaelt einen Wert, der kein gueltiger Spaltenname ist."
        )


# --------------------------------------------------------------------------
# Laenge des Moderationsgrundes (Plan 02, umgesetzt 03.09.2026)
#
# Der Grund landet bei Discord im HTTP-Header X-Audit-Log-Reason, und der
# erlaubt "1-512 URL-encoded UTF-8 characters". Das Tueckische ist das
# "URL-encoded": Gezaehlt wird NACH der Kodierung. Ein "a" bleibt ein Zeichen,
# ein "ü" wird zu %C3%BC und belegt sechs. Ein deutscher Text reisst die Grenze
# also viel frueher, als das Zaehlen der Buchstaben vermuten laesst - und wer
# nur len(text) prueft, baut genau diesen Fehler ein.
# --------------------------------------------------------------------------

from urllib.parse import quote  # noqa: E402


@pytest.fixture(scope="module")
def kuerzen():
    from discord_source import load_from_app
    return load_from_app("AUDIT_REASON_MAX", "_kuerze_audit_grund")


def test_grenze_entspricht_der_dokumentation(kuerzen):
    assert kuerzen["AUDIT_REASON_MAX"] == 512


def test_kurzer_grund_bleibt_unveraendert(kuerzen):
    text = "Werbung im Chat, dritte Verwarnung."
    assert kuerzen["_kuerze_audit_grund"](text) == (text, False)


def test_langer_grund_wird_gekuerzt(kuerzen):
    gekuerzt, wurde = kuerzen["_kuerze_audit_grund"]("a" * 600)
    assert wurde is True
    assert len(quote(gekuerzt, safe="")) <= 512


def test_umlaute_zaehlen_sechsfach(kuerzen):
    """200 Umlaute sind 200 Zeichen, aber 1200 kodierte - muessen also gekuerzt
    werden. Eine Pruefung ueber len(text) wuerde sie durchlassen und Discord
    antwortete mit einer nichtssagenden 400."""
    text = "ü" * 200
    assert len(text) < 512, "Rohlaenge liegt unter der Grenze - genau die Falle"
    assert len(quote(text, safe="")) > 512, "kodiert aber darueber"
    gekuerzt, wurde = kuerzen["_kuerze_audit_grund"](text)
    assert wurde is True
    assert len(quote(gekuerzt, safe="")) <= 512


@pytest.mark.parametrize("zeichen", ["ü", "ß", "é", "🌱", "„", "—"])
def test_kein_schnitt_mitten_im_zeichen(kuerzen, zeichen):
    """text[:512] wuerde ein Mehrbyte-Zeichen zerteilen. Hier wird zeichenweise
    gezaehlt, das Ergebnis muss also weiterhin gueltiger Text sein."""
    gekuerzt, _ = kuerzen["_kuerze_audit_grund"](zeichen * 400)
    gekuerzt.encode("utf-8").decode("utf-8")  # wuerfe UnicodeDecodeError bei kaputtem Schnitt
    assert len(quote(gekuerzt, safe="")) <= 512


def test_gekuerzter_text_ist_erkennbar(kuerzen):
    """Ein Auslassungszeichen am Ende - sonst sieht der abgeschnittene Grund im
    Audit-Log wie ein vollstaendiger Satz aus, der mitten im Wort endet."""
    gekuerzt, _ = kuerzen["_kuerze_audit_grund"]("Spam " * 200)
    assert gekuerzt.endswith("…")


def test_genau_an_der_grenze_wird_nicht_angefasst(kuerzen):
    text = "a" * 512
    assert len(quote(text, safe="")) == 512
    assert kuerzen["_kuerze_audit_grund"](text) == (text, False)


def test_ein_zeichen_darueber_wird_gekuerzt(kuerzen):
    assert kuerzen["_kuerze_audit_grund"]("a" * 513)[1] is True


# --------------------------------------------------------------------------
# Nachrichten beim Bann löschen (Plan 05, umgesetzt 03.09.2026)
#
# Discord erlaubt beim Bann delete_message_seconds von 0 bis 604800 (7 Tage).
# Das ist die EINZIGE Änderung in diesem Projekt mit nicht umkehrbarer Wirkung:
# Gelöschte Nachrichten sind weg, auch die von anderen Leuten in der
# Unterhaltung. Entsprechend konservativ ist alles hier abgesichert.
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def zeitraeume():
    from discord_source import load_from_app
    return load_from_app("BAN_LOESCHZEITRAEUME")["BAN_LOESCHZEITRAEUME"]


def test_null_ist_dabei_und_bedeutet_nichts_loeschen(zeitraeume):
    assert zeitraeume["0"][1] == 0


def test_erste_auswahl_ist_nichts_loeschen(zeitraeume):
    """Die Reihenfolge im Dictionary bestimmt die Reihenfolge im Auswahlfeld -
    und damit die Vorauswahl. Sie muss der harmlose Fall sein."""
    assert next(iter(zeitraeume)) == "0"


def test_keine_auswahl_ueberschreitet_discords_grenze(zeitraeume):
    """604800 Sekunden = 7 Tage. Alles darüber lehnt Discord ab."""
    for schluessel, (label, sekunden) in zeitraeume.items():
        assert 0 <= sekunden <= 604800, f"{label} liegt außerhalb von Discords Bereich."


def test_schluessel_und_wert_passen_zusammen(zeitraeume):
    """Der Schlüssel landet im Formular, der Wert geht an Discord. Laufen sie
    auseinander, löscht der Bot einen anderen Zeitraum als angezeigt - und das
    ist nicht rückgängig zu machen."""
    for schluessel, (label, sekunden) in zeitraeume.items():
        assert int(schluessel) == sekunden, (
            f"Auswahl {label!r}: Formularwert {schluessel} != {sekunden} Sekunden."
        )


def test_sieben_tage_sind_604800_sekunden():
    assert 7 * 24 * 60 * 60 == 604800


def test_route_prueft_gegen_die_liste_statt_zu_uebernehmen():
    """Ein manipulierter Aufruf darf keinen beliebigen Zeitraum setzen können.
    Ohne Prüfung ginge auch ein Wert, den Discord ablehnt - oder schlimmer,
    einer der mehr löscht als angezeigt."""
    quelle = APP_PY.read_text()
    fn = _funktion(quelle, "member_ban")
    text = ast.get_source_segment(quelle, fn) or ""
    assert "BAN_LOESCHZEITRAEUME.get(" in text, (
        "member_ban() prüft den Löschzeitraum nicht gegen die feste Liste."
    )
    assert 'BAN_LOESCHZEITRAEUME["0"]' in text, (
        "Bei unbekanntem Wert muss auf 0 zurückgefallen werden, nicht auf einen "
        "beliebigen Standard."
    )


def test_geloeschter_zeitraum_landet_im_audit_log():
    """Nicht rückgängig zu machen heißt: Es muss später nachvollziehbar sein,
    was verschwunden ist."""
    quelle = APP_PY.read_text()
    text = ast.get_source_segment(quelle, _funktion(quelle, "member_ban")) or ""
    assert "Nachrichten gelöscht" in text


def test_rueckfrage_nennt_den_zeitraum():
    """Der Bestätigungsdialog muss sagen, was gelöscht wird - sonst klickt
    jemand 'ok' und ist überrascht."""
    detail = (REPO / "backend" / "templates" / "member_detail.html").read_text()
    block = detail[detail.index("member_ban"):]
    block = block[:block.index("</form>")]
    assert "delete_messages" in block, "Das Auswahlfeld fehlt im Bann-Formular."
    assert "onsubmit" in block and "confirm" in block
    assert "nicht wiederherstellen" in block, (
        "Die Rückfrage warnt nicht davor, dass das Löschen endgültig ist."
    )
