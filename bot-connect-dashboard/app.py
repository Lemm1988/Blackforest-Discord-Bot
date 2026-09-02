"""Bot Connect Dashboard - lokales Verwaltungs-Tool NUR fuer die Tunnel-Zugaenge
(App-Zugaenge der Android-App). Bewusst ein EIGENSTAENDIGER, komplett vom
Haupt-Backend (backend/app.py, Port 5000/8500, uebers Internet per Iroh-Tunnel
erreichbar) getrennter Prozess auf einem eigenen Port, der NUR an 127.0.0.1
bindet. Siehe README.md in diesem Ordner fuer die vollstaendige Begruendung -
KEIN dumbpipe-Tunnel und KEIN nginx-Proxy fuer diesen Port einrichten, das wuerde
den gesamten Sinn (nicht uebers Internet erreichbar) zunichtemachen.

Kein Login noetig: die Netzwerk-Isolation (nur localhost, kein Tunnel) IST hier
bewusst die Zugriffskontrolle, analog zum bereits vorausgesetzten Vertrauen in
SSH-Zugriff auf diesen Pi im gesamten Projekt.

Mitgliederliste: kommt NICHT aus einer eigenen Discord-API-Anbindung (kein
zweiter Bot-Token noetig), sondern per HTTP von backend/internal_app.py
(127.0.0.1:5052) - einem KOMPLETT SEPARATEN, ebenfalls nur-lokalen Endpunkt ohne
nginx-Eintrag. Siehe internal_app.py fuer die ausfuehrliche Begruendung, warum
dafuer NICHT einfach der bestehende Backend-Port (5001) mitgenutzt wird (der ist
trotz "nur 127.0.0.1" tatsaechlich ueber den Iroh-Tunnel erreichbar).
"""
import sys
from pathlib import Path

import requests
from flask import Flask, flash, redirect, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import tunnel_access_core as core  # noqa: E402

app = Flask(__name__)
# Zufaelliger Wert reicht - keine sicherheitsrelevante Funktion, da die App nur ueber
# 127.0.0.1 erreichbar ist (kein Angreifer, der Session-Cookies faelschen koennte).
app.secret_key = "bot-connect-dashboard-local-only"

INTERNAL_MEMBERS_URL = "http://127.0.0.1:5052/members"


def _fetch_members():
    """Liste aktueller Server-Mitglieder vom internen Nur-lokal-Endpunkt. Liefert
    bei jedem Fehler (Bot laeuft nicht, Endpunkt nicht erreichbar) eine leere
    Liste statt einer Exception - das Dashboard soll trotzdem laden, nur eben
    ohne befuellbares Dropdown."""
    try:
        resp = requests.get(INTERNAL_MEMBERS_URL, timeout=5)
        resp.raise_for_status()
        return resp.json().get("members", [])
    except Exception:
        return []


@app.route("/")
def index():
    return render_template("index.html", grants=core.list_grants(), members=_fetch_members())


@app.route("/add", methods=["POST"])
def add():
    discord_user_id = request.form.get("discord_user_id", "").strip()
    try:
        if not discord_user_id:
            raise core.TunnelAccessError("Bitte eine Person aus der Liste auswaehlen.")
        members = _fetch_members()
        member = next((m for m in members if m["id"] == discord_user_id), None)
        if not member:
            raise core.TunnelAccessError(
                "Unbekannte oder nicht mehr gueltige Mitglieder-Auswahl (Mitgliederliste "
                "neu geladen? Ist der Bot online?)."
            )
        label = member["username"]
        ticket = core.add_grant(label, discord_user_id)
        flash(f"Zugang für '{label}' erstellt. Ticket (jetzt kopieren, wird nur einmal angezeigt):", "success")
        flash(ticket, "ticket")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


@app.route("/revoke", methods=["POST"])
def revoke():
    label = request.form.get("label", "")
    try:
        core.revoke_grant(label)
        flash(f"Zugang für '{label}' widerrufen. Eine bestehende Login-Session dieser Person "
              "im Haupt-Backend wurde ebenfalls beendet. Das Ticket ist damit dauerhaft "
              "ungültig - für erneuten Zugang muss ein neues verteilt werden.", "success")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


@app.route("/pause", methods=["POST"])
def pause():
    """Pausieren statt widerrufen: haelt den Tunnel an, laesst Secret und Ticket
    aber bestehen. Fuer voruebergehendes Aussetzen - nach dem Fortsetzen
    funktioniert dasselbe Ticket weiter, es muss nichts neu verteilt werden."""
    label = request.form.get("label", "")
    try:
        core.pause_grant(label)
        flash(f"Zugang für '{label}' pausiert. Die Verbindung ist sofort unterbrochen, das "
              "Ticket bleibt gültig und funktioniert nach dem Fortsetzen unverändert.", "success")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


@app.route("/resume", methods=["POST"])
def resume():
    label = request.form.get("label", "")
    try:
        core.resume_grant(label)
        flash(f"Zugang für '{label}' fortgesetzt - das bisherige Ticket gilt unverändert.", "success")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    label = request.form.get("label", "")
    try:
        ticket_value = core.reset_grant(label)
        flash(f"Zugang für '{label}' neu gesetzt. NEUES Ticket:", "success")
        flash(ticket_value, "ticket")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


@app.route("/delete", methods=["POST"])
def delete():
    """Entfernt den Eintrag restlos inklusive Historie. Ist der Zugang noch aktiv,
    widerruft core.delete_grant() ihn zuvor regulaer - sonst bliebe ein laufender
    Tunnel zurueck, den danach kein Eintrag mehr sichtbar macht."""
    label = request.form.get("label", "")
    try:
        core.delete_grant(label)
        flash(f"Zugang für '{label}' vollständig gelöscht.", "success")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


@app.route("/ticket", methods=["POST"])
def ticket():
    label = request.form.get("label", "")
    try:
        value = core.show_ticket(label)
        flash(f"Ticket für '{label}':", "success")
        flash(value, "ticket")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # NUR 127.0.0.1 - siehe README.md. Niemals auf 0.0.0.0 aendern.
    app.run(host="127.0.0.1", port=5050)
