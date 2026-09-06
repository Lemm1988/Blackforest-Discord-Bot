"""Der Linkfilter: welche Regel wann greift.

Aufgabe (Patrick, 04.09.2026): "der Linkfilter sollte nicht nur white sondern
auch blacklisting koennen und mehrere einstellungen haben."

Eine Sperrliste gab es bis zum 06.09.2026 nicht - `grep -n "blacklist" bot/
backend/` fand keinen einzigen Treffer. Es gab nur "alles blockieren",
"Whitelist" und "nur neue Mitglieder", und die Entscheidung steckte mitten im
Cog, verwoben mit dem Loeschen der Nachricht und dem Timeout. Pruefbar war sie
nur, indem man den Bot startete und eine Nachricht schrieb.

Diese Datei sichert die Reihenfolge der Regeln. Sie ist der Kern: Eine
Sperrliste, die von einer anderen Regel ueberstimmt wird, ist keine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bot"))

import linkfilter  # noqa: E402


def urteil(url="https://example.com/x", **regeln):
    return linkfilter.pruefe(url=url, **regeln)


# --- Domains erkennen -------------------------------------------------------


@pytest.mark.parametrize("url,erwartet", [
    ("https://www.example.com/foo?x=1", "example.com"),
    ("http://example.com", "example.com"),
    ("www.example.com", "example.com"),
    ("https://cdn.example.com:8443/a/b", "cdn.example.com"),
    ("https://EXAMPLE.COM", "example.com"),
    ("https://example.com#anker", "example.com"),
    ("", ""),
])
def test_domain_wird_sauber_herausgeloest(url, erwartet):
    assert linkfilter.domain_aus_url(url) == erwartet


def test_unterdomains_sind_eingeschlossen():
    """`example.com` auf der Liste deckt auch `cdn.example.com`."""
    assert linkfilter.passt_auf_liste("cdn.example.com", ["example.com"])


def test_die_umgekehrte_richtung_gilt_nicht():
    """`cdn.example.com` auf der Liste erlaubt nicht die ganze Domain."""
    assert not linkfilter.passt_auf_liste("example.com", ["cdn.example.com"])


def test_stern_schreibweise_wird_angenommen():
    """`*.example.com` liegt nahe und wuerde sonst stillschweigend nie greifen -
    bei einer Sperrliste das Schlimmste, was passieren kann."""
    assert linkfilter.passt_auf_liste("cdn.example.com", ["*.example.com"])
    assert linkfilter.passt_auf_liste("example.com", ["*.example.com"])


def test_ein_eintrag_darf_auch_eine_ganze_adresse_sein():
    assert linkfilter.passt_auf_liste("example.com", ["https://www.example.com/pfad"])


def test_leere_eintraege_werden_uebergangen():
    assert not linkfilter.passt_auf_liste("example.com", ["", None, "  "])


def test_eine_aehnlich_aussehende_domain_passt_nicht():
    """`notexample.com` endet auf `example.com`, ist aber etwas anderes -
    deshalb wird auf den Punkt geprueft, nicht auf den blossen Textanfang."""
    assert not linkfilter.passt_auf_liste("notexample.com", ["example.com"])


# --- Die Reihenfolge der Regeln ---------------------------------------------


def test_ohne_jede_regel_ist_nichts_verboten():
    assert not urteil().verstoss


def test_die_sperrliste_greift():
    e = urteil(gesperrt=["example.com"])
    assert e.verstoss and e.regel == linkfilter.SPERRLISTE


def test_die_sperrliste_schlaegt_die_erlaubtliste():
    """Wer eine Domain ausdruecklich verbietet, meint es. Stuende die
    Erlaubtliste vorn, waere die Sperrliste wirkungslos, sobald jemand dieselbe
    Domain versehentlich in beide Listen eintraegt."""
    e = urteil(gesperrt=["example.com"], erlaubt=["example.com"], erlaubtliste_aktiv=True)
    assert e.regel == linkfilter.SPERRLISTE


def test_die_sperrliste_greift_auch_wenn_sonst_keine_regel_aktiv_ist():
    """Der eigentliche Zweck: eine einzelne Domain verbieten, ohne gleich alle
    Links zu sperren."""
    e = urteil(gesperrt=["example.com"], alles_blockieren=False,
               erlaubtliste_aktiv=False, neue_mitglieder_aktiv=False)
    assert e.verstoss


def test_alles_blockieren_greift_fuer_jede_domain():
    e = urteil(url="https://irgendwas.de", alles_blockieren=True)
    assert e.verstoss and e.regel == linkfilter.ALLES_BLOCKIEREN


def test_erlaubtliste_laesst_bekannte_domains_durch():
    assert not urteil(erlaubtliste_aktiv=True, erlaubt=["example.com"]).verstoss


def test_erlaubtliste_blockt_unbekannte_domains():
    e = urteil(url="https://fremd.de", erlaubtliste_aktiv=True, erlaubt=["example.com"])
    assert e.regel == linkfilter.NICHT_ERLAUBT


def test_neue_mitglieder_duerfen_auch_erlaubte_domains_nicht():
    """Sonst koennten neue Mitglieder ueber eine erlaubte Domain doch Links
    posten - die Regel waere dann keine."""
    e = urteil(erlaubtliste_aktiv=True, erlaubt=["example.com"],
               neue_mitglieder_aktiv=True, ist_neues_mitglied=True)
    assert e.regel == linkfilter.NEUES_MITGLIED


def test_laengere_zugehoerigkeit_faellt_nicht_unter_die_regel():
    assert not urteil(neue_mitglieder_aktiv=True, ist_neues_mitglied=False).verstoss


def test_die_regel_wird_benannt():
    """Fuer das Protokoll und die geplante Testbox im Backend. Im Chat bekommt
    niemand die Regel genannt - das waere eine Anleitung zum Umgehen."""
    assert urteil(gesperrt=["example.com"]).begruendung
    assert not urteil().begruendung


# --- Ganze Nachrichten ------------------------------------------------------


def test_eine_nachricht_ohne_link_ist_nie_ein_verstoss():
    assert not linkfilter.pruefe_nachricht("guten morgen zusammen",
                                           alles_blockieren=True).verstoss


def test_der_erste_link_einer_nachricht_wird_beurteilt():
    e = linkfilter.pruefe_nachricht("schau mal www.example.com an",
                                    gesperrt=["example.com"])
    assert e.verstoss and e.domain == "example.com"


@pytest.mark.parametrize("text", ["www.example.com", "https://example.com",
                                  "Text davor http://example.com und danach"])
def test_links_werden_in_verschiedenen_schreibweisen_gefunden(text):
    assert linkfilter.erster_link(text)


# --- Das Modul bleibt prüfbar ------------------------------------------------


@pytest.mark.parametrize("verboten", ["import discord", "import sqlite3", "import flask"])
def test_das_modul_bleibt_frei_von_schweren_abhaengigkeiten(verboten):
    assert verboten not in (REPO / "bot" / "linkfilter.py").read_text(encoding="utf-8")


def test_das_cog_benutzt_die_gemeinsame_logik():
    """Sonst laufen Bot und Backend-Testbox spaeter auseinander."""
    quelle = (REPO / "bot" / "cogs" / "moderation.py").read_text(encoding="utf-8")
    assert "linkfilter.pruefe(" in quelle
    assert "gesperrt=database.get_link_blacklist()" in quelle


def test_das_backend_kann_die_sperrliste_pflegen():
    quelle = (REPO / "backend" / "app.py").read_text(encoding="utf-8")
    assert "moderation_link_blacklist_add" in quelle
    assert "moderation_link_blacklist_remove" in quelle
    assert "link_blacklist=database.get_link_blacklist_full()" in quelle
