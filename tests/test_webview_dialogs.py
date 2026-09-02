"""Waechter dafuer, dass Bestaetigungsdialoge in der App funktionieren.

Kick, Ban und Timeout liessen sich ueber die App nicht ausloesen: Ein Klick
bewirkte gar nichts - keine Rueckfrage, keine Fehlermeldung, keine Anfrage am
Server (im nginx-Log stand kein einziges POST auf die Moderationsrouten).

Ursache war das Zusammenspiel zweier fuer sich harmloser Dinge:

1. Das Backend sichert zustandsaendernde Formulare mit
   onsubmit="return confirm(...)" ab.
2. Die App setzte keinen WebChromeClient. Eine Android-WebView verwirft
   JavaScript-Dialoge dann stillschweigend, window.confirm() liefert sofort
   false - und ein false aus onsubmit bricht das Absenden ab.

Im normalen Browser funktionierten dieselben Formulare, deshalb fiel es lange
nicht auf. Beide Seiten werden hier abgesichert, denn beide fuer sich sind
unverdaechtig - erst zusammen ergeben sie den Ausfall.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAIN_ACTIVITY = REPO / "android-app/app/src/main/java/de/blackforest/bottunnel/MainActivity.kt"
TEMPLATES = REPO / "backend/templates"


def test_app_handles_javascript_dialogs():
    """Ohne WebChromeClient mit onJsConfirm bleibt jede Bestaetigung wirkungslos."""
    quelle = MAIN_ACTIVITY.read_text()
    assert "webChromeClient" in quelle, (
        "Die App setzt keinen WebChromeClient - JavaScript-Dialoge werden dann "
        "stillschweigend verworfen und Formulare mit confirm() senden nie ab."
    )
    assert "onJsConfirm" in quelle, (
        "onJsConfirm fehlt - confirm() liefert dann false und bricht das Absenden ab."
    )


def test_confirm_dialog_cancels_when_dismissed():
    """Wegtippen des Dialogs muss abbrechen, nicht bestaetigen. Sonst koennte ein
    versehentlicher Tipp jemanden vom Server werfen."""
    quelle = MAIN_ACTIVITY.read_text()
    block = quelle[quelle.index("onJsConfirm"):]
    block = block[:block.index("onJsAlert")]
    assert "setOnCancelListener" in block and "cancel()" in block, (
        "Der Bestaetigungsdialog behandelt das Wegtippen nicht als Abbruch."
    )


def test_moderation_forms_still_rely_on_confirm():
    """Gegenprobe: Solange die Formulare confirm() benutzen, MUSS die App die
    Dialoge behandeln. Wird das hier einmal umgestellt, faellt dieser Test auf und
    die Verknuepfung der beiden Seiten wird neu bewertet."""
    detail = (TEMPLATES / "member_detail.html").read_text()
    formulare = re.findall(r'onsubmit="return confirm', detail)
    assert formulare, (
        "member_detail.html benutzt kein confirm() mehr - dann kann dieser "
        "Waechter angepasst werden."
    )
