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
"""
import sys
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import tunnel_access_core as core  # noqa: E402

app = Flask(__name__)
# Zufaelliger Wert reicht - keine sicherheitsrelevante Funktion, da die App nur ueber
# 127.0.0.1 erreichbar ist (kein Angreifer, der Session-Cookies faelschen koennte).
app.secret_key = "bot-connect-dashboard-local-only"


@app.route("/")
def index():
    return render_template("index.html", grants=core.list_grants())


@app.route("/add", methods=["POST"])
def add():
    label = request.form.get("label", "")
    try:
        ticket = core.add_grant(label)
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
        flash(f"Zugang für '{label}' widerrufen.", "success")
    except core.TunnelAccessError as e:
        flash(str(e), "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # NUR 127.0.0.1 - siehe README.md. Niemals auf 0.0.0.0 aendern.
    app.run(host="127.0.0.1", port=5050)
