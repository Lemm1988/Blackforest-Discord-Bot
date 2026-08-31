# Fernzugriff-Setup: SSH-Keypair, Iroh 1.0 / dumbpipe, Claude Code auf dem Handy

Diese Anleitung beschreibt exakt, wie der Fernzugriff auf diesen Raspberry Pi (Mycodo,
Discord-Bot) eingerichtet wurde: SSH per Schlüsselpaar, Iroh-1.0-basierte P2P-Tunnel (via
`dumbpipe`) für den Zugriff auf die Mycodo-Weboberfläche und das Discord-Bot-Backend über
mobile Daten, und wie man Claude Code über Termux auf einem Android-Handy installiert.

**Stand: 31.08.2026 — WICHTIGER HINWEIS VORAB.** Abschnitt 3 (Discord-Bot-Backend per
dumbpipe) beschreibt den **historischen** Zustand mit einem einzigen, geteilten Tunnel-Ticket.
**Dieser geteilte Zugang wurde am 31.08.2026 aus Sicherheitsgründen komplett deaktiviert**
(siehe Abschnitt 7) — er ließ sich nicht einzeln widerrufen und war damit eine dauerhafte
Hintertür am neuen Zugangssystem vorbei. Abschnitt 3 bleibt als technischer Hintergrund
stehen (dumbpipe/Iroh-Grundlagen gelten weiterhin), aber **der eigentliche, aktuelle Weg,
um Zugriff auf das Discord-Bot-Backend zu bekommen, steht in Abschnitt 7.**

**Wichtiger Sicherheitshinweis vorab:** Diese Datei enthält absichtlich KEINE echten
Secrets/Tickets im Klartext (siehe Abschnitt 2.6). Behandle generierte Secrets/Tickets
wie Passwörter.

---

## 1. SSH-Zugriff per Public-Key-Authentifizierung

### 1.1 Warum Public-Key statt Passwort

Ein Schlüsselpaar besteht aus einem privaten Schlüssel (bleibt geheim auf dem Client,
hier dem Handy) und einem öffentlichen Schlüssel (wird auf dem Server, dem Pi, hinterlegt).
Beim Verbindungsaufbau beweist der Client per kryptografischer Signatur, dass er den
privaten Schlüssel besitzt, ohne ihn zu übertragen. Vorteile gegenüber Passwort-Login:

- Kein Passwort, das erraten oder per Brute-Force geknackt werden kann.
- Der private Schlüssel verlässt nie das Gerät, auf dem er erzeugt wurde.
- Auf dem Server kann man `PasswordAuthentication no` setzen, wodurch Passwort-Logins
  komplett deaktiviert werden und nur noch Geräte mit hinterlegtem Public Key reinkommen.

### 1.2 Serverseitige Konfiguration (auf dem Pi)

In `/etc/ssh/sshd_config` ist aktuell gesetzt:

```
PubkeyAuthentication yes
PasswordAuthentication no
```

- `PubkeyAuthentication yes`: Erlaubt Login per Schlüsselpaar.
- `PasswordAuthentication no`: Verbietet Login per Passwort komplett. Das ist der Grund,
  warum ohne passenden privaten Schlüssel kein Zugriff möglich ist, selbst wenn jemand
  das Passwort des Benutzers `picom` kennen würde.

Falls diese Einstellungen geändert werden müssen: Datei mit `sudo nano /etc/ssh/sshd_config`
bearbeiten, danach `sudo systemctl restart sshd` zum Neuladen.

### 1.3 Client-seitig: Schlüsselpaar erzeugen (falls noch keins existiert)

```bash
ssh-keygen -t ed25519 -C "claude-cli-zugriff"
```

- `-t ed25519`: Wählt den Ed25519-Algorithmus statt des älteren RSA. Ed25519 erzeugt
  kürzere Schlüssel bei gleichzeitig höherer Sicherheit und ist auf allen aktuellen
  Systemen unterstützt.
- `-C "claude-cli-zugriff"`: Ein frei wählbarer Kommentar, der im Public Key mit
  abgespeichert wird (taucht z. B. in `authorized_keys` auf dem Server auf) — nützlich,
  um später zu erkennen, welcher Schlüssel wozu gehört, wenn mehrere Geräte Zugriff haben.
- Ohne weitere Flags fragt der Befehl interaktiv nach dem Speicherort (Standard:
  `~/.ssh/id_ed25519`) und optional einer Passphrase zum zusätzlichen Schutz des
  privaten Schlüssels.

Das erzeugt zwei Dateien:
- `~/.ssh/id_ed25519` — der private Schlüssel. **Niemals weitergeben.**
- `~/.ssh/id_ed25519.pub` — der öffentliche Schlüssel. Darf verteilt werden.

### 1.4 Public Key auf den Pi übertragen

Variante A, mit `ssh-copy-id` (einfachster Weg, sofern noch Passwort-Login verfügbar ist):

```bash
ssh-copy-id picom@192.168.178.30
```

Variante B, manuell (nötig, wenn `PasswordAuthentication no` bereits aktiv ist und der
Public Key auf einem anderen Weg übertragen werden muss, z. B. per Copy-Paste über eine
bereits bestehende Sitzung):

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA... claude-cli-zugriff" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

- `chmod 700 ~/.ssh`: Nur der Besitzer darf das Verzeichnis lesen/schreiben/betreten.
  OpenSSH verweigert den Login, wenn die Verzeichnis-/Dateirechte zu offen sind.
- `chmod 600 ~/.ssh/authorized_keys`: Nur der Besitzer darf die Datei lesen/schreiben.
- `>>` statt `>`: Hängt den neuen Key an, statt bestehende Keys zu überschreiben.

**Aktueller Stand auf diesem Pi:** In `picom`s `~/.ssh/authorized_keys` sind mehrere Keys
hinterlegt, u.a. `claude-cli-zugriff` (CLI-Zugriff) und `u0_a481@localhost` (Termux/Debian
auf dem Handy, `u0_a481` = Android-App-UID von Termux). Zusätzlich liegt seit dem
GitHub-Actions-Setup (Abschnitt 8) ein separates Deploy-Key-Paar unter
`~/.ssh/github_android_build` — das ist NICHT für SSH-Login gedacht, sondern ausschließlich
für Git-Push ins `Blackforest-Discord-Bot`-Repo auf GitHub.

### 1.5 Verbindung testen

```bash
ssh picom@192.168.178.30
```

Bei Erfolg landet man ohne Passwortabfrage direkt in einer Shell auf dem Pi. Für
nicht-interaktive Aufrufe (z. B. aus einem Skript) empfiehlt sich zusätzlich:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 picom@192.168.178.30 '<befehl>'
```

- `-o BatchMode=yes`: Verhindert, dass SSH bei einem Problem (z. B. fehlender Key)
  interaktiv nach einem Passwort fragt und damit ein Skript blockiert — es bricht
  stattdessen sofort mit einem Fehler ab.
- `-o ConnectTimeout=8`: Bricht den Verbindungsversuch nach 8 Sekunden ab, statt
  minutenlang zu hängen, falls der Host nicht erreichbar ist.

**Praxis-Hinweis:** Die WLAN/Mobilfunk-Verbindung zwischen Handy und Pi ist gelegentlich
kurz instabil (SSH liefert dann `No route to host` oder `Connection timed out`). Bewährter
Umgang damit: einfach erneut versuchen, ggf. mit kurzer Wartezeit dazwischen. Ein simpler
Retry-Loop:

```bash
until ssh -4 -o ConnectTimeout=5 -o BatchMode=yes picom@192.168.178.30 'exit 0' 2>/dev/null; do
  sleep 5
done
```

### 1.6 Vorhandene Berechtigungen von `picom` (zur Kenntnis, nicht Teil dieses Setups)

Der Benutzer `picom` hat bereits vor diesem Setup passwortlosen `sudo`-Zugriff auf alles
(`NOPASSWD: ALL`, siehe `sudo -l`). Das bedeutet: Wer sich per SSH als `picom` einloggen
kann, hat effektiv vollen Root-Zugriff auf den Pi. Das unterstreicht, warum der private
SSH-Schlüssel (Abschnitt 1.3) mit derselben Sorgfalt wie ein Root-Passwort behandelt
werden sollte — und warum das Bot Connect Dashboard (Abschnitt 7.3) bewusst ohne
zusätzliches Login-System auskommt: wer lokalen/SSH-Zugriff auf den Pi hat, hat ohnehin
schon dieses Vertrauensniveau.

---

## 2. Iroh 1.0 / dumbpipe — P2P-Tunnel für Fernzugriff auf die Mycodo-Weboberfläche

### 2.1 Was ist Iroh, was ist dumbpipe

**Iroh** (Version 1.0, veröffentlicht 15.06.2026 von n0-computer) ist eine
Peer-to-Peer-Networking-Bibliothek. Kernidee: Geräte werden über einen kryptografischen
Public Key ("Dial Keys, not IPs") statt über eine IP-Adresse angesprochen. Iroh versucht
zuerst eine direkte Verbindung per NAT-Hole-Punching über QUIC herzustellen; gelingt das
nicht, wird automatisch auf einen öffentlichen Relay-Server von n0 ausgewichen. Der
gesamte Datenverkehr ist dabei durchgehend Ende-zu-Ende verschlüsselt (QUIC/TLS 1.3) —
auch wenn er über einen Relay läuft, kann dieser den Inhalt nicht mitlesen.

Iroh selbst ist "nur" eine Bibliothek (Rust-Kern, Bindings für mehrere Sprachen), kein
fertiges Endnutzer-Programm. **`dumbpipe`** ist ein von n0 selbst bereitgestelltes
Beispiel-CLI-Tool auf Basis von Iroh, das genau eine Aufgabe erfüllt: eine TCP-Verbindung
zwischen zwei Rechnern durchreichen (vergleichbar mit `netcat`, nur ortsunabhängig über
Public Keys statt IP-Adressen).

**Praxis-Erfahrung aus diesem Projekt (siehe auch Abschnitt 7.5):** Sowohl auf dem Pi als
auch auf mindestens einem Client-Gerät wurde wiederholt `Warning: Failed to connect to the
home relay` beobachtet, und der `dumbpipe`-Prozess stürzt gelegentlich mit `ERROR
iroh::socket: Endpoint dropped without calling Endpoint::close` ab. Trotzdem funktionieren
Verbindungen in den meisten Fällen (vermutlich weil direktes NAT-Hole-Punching gelingt,
auch wenn der Relay-Fallback selbst gerade nicht erreichbar ist) — bei manchen
Netzwerk-Kombinationen (v.a. Mobilfunk mit Carrier-NAT) kann der Verbindungsaufbau aber
spürbar länger dauern oder ganz fehlschlagen, wenn weder Hole-Punching noch Relay klappen.

### 2.2 Architektur dieses Setups (Mycodo, unverändert seit Ersteinrichtung)

```
Handy (Termux/Debian, mobile Daten)              Raspberry Pi (Heimnetz)
┌────────────────────────────┐                   ┌──────────────────────────┐
│ Browser -> https://127.0.0.1:8443               │ nginx (Mycodo) auf :443   │
│      │                      │                   │      ^                    │
│      v                      │   Iroh/QUIC        │      │                    │
│ dumbpipe connect-tcp  <─────┼── verschlüsselt ───┼─> dumbpipe listen-tcp     │
│ (zufällige Identität)       │   (P2P oder Relay)  │  (feste Identität,       │
│                              │                   │   systemd-Dienst)         │
└────────────────────────────┘                   └──────────────────────────┘
```

Die lauschende Seite (Pi) hat eine **feste** kryptografische Identität (Secret Key), damit
das Verbindungs-"Ticket" dauerhaft gültig bleibt. Die verbindende Seite (Handy) braucht
keine feste Identität, hier reicht eine bei jedem Start neu erzeugte, zufällige.

### 2.3 Voraussetzung: Rust-Toolchain (auf beiden Seiten identisch)

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup-init.sh
sh /tmp/rustup-init.sh -y --default-toolchain stable
```

- `curl --proto '=https' --tlsv1.2 -sSf`: Lädt das Installationsskript herunter.
  `--proto '=https'` erzwingt, dass curl ausschließlich HTTPS verwendet (kein Downgrade
  auf HTTP möglich, selbst wenn eine Umleitung das versuchen würde). `--tlsv1.2` erzwingt
  mindestens TLS 1.2. `-sSf`: `-s` (silent, keine Fortschrittsanzeige), `-S` (bei Fehlern
  trotzdem die Fehlermeldung zeigen), `-f` (bei HTTP-Fehlercode fehlschlagen statt eine
  Fehlerseite als Datei zu speichern).
- `sh ... -y --default-toolchain stable`: Führt den Installer nicht-interaktiv aus.
  `-y` bestätigt automatisch alle Rückfragen. `--default-toolchain stable` installiert
  den stabilen Rust-Release-Kanal (kein Nightly/Beta nötig für dieses Projekt).
- Ergebnis: `rustc`/`cargo` liegen unter `~/.cargo/bin/`, aktivierbar in einer neuen
  Shell-Sitzung per `source "$HOME/.cargo/env"`.

Aktuell installiert:
- Pi: `rustc 1.98.0`. **Wichtiger Nachtrag (siehe Abschnitt 8):** `uname -m` auf dem Pi
  liefert `aarch64` (64-Bit-Kernel), `dpkg --print-architecture` aber `armhf` — der Pi
  läuft also mit 64-Bit-Kernel, aber 32-Bit-Userland/OS. Für reinen CLI-Gebrauch (wie hier)
  spielt das keine Rolle, wurde aber beim Android-Cross-Compile-Versuch (Abschnitt 8.1)
  relevant.
- Handy: `rustc 1.98.0` (Ziel-Architektur `aarch64-unknown-linux-gnu`, echtes 64-Bit).

### 2.4 Bekanntes Problem auf diesem Pi: kaputtes IPv6 → Downloads schlagen fehl

**Symptom:** `cargo install` schlägt wiederholt mit Fehlern wie `Could not resolve
hostname: static.crates.io` oder `transfer too slow` fehl, obwohl das Internet
grundsätzlich funktioniert.

**Ursache:** Der DNS-Server liefert für viele Domains (u. a. `static.crates.io`)
bevorzugt IPv6-Adressen (AAAA-Records) aus. Auf diesem Pi ist IPv6 aber nicht
funktionsfähig (`ping -6` liefert "Netzwerk nicht erreichbar"). Cargos HTTP-Client
versucht dadurch wiederholt zuerst IPv6, läuft in einen Timeout, bevor (wenn überhaupt)
auf IPv4 zurückgefallen wird — das äußert sich als zufällig wirkende Netzwerkfehler.

**Fix** (in `/etc/gai.conf`, macht die Systembibliothek `glibc` bei der Adressauswahl
IPv4 gegenüber IPv6 bevorzugen):

```bash
sudo cp /etc/gai.conf /etc/gai.conf.bak_$(date +%Y%m%d_%H%M%S)
echo "precedence ::ffff:0:0/96  100" | sudo tee -a /etc/gai.conf
```

- `cp ... .bak_$(date ...)`: Backup der Originaldatei mit Zeitstempel im Dateinamen,
  bevor etwas verändert wird.
- `echo "..." | sudo tee -a /etc/gai.conf`: Hängt die Zeile ans Ende der Datei an
  (`tee -a` statt `>>`, weil `sudo echo ... >> datei` wegen der Shell-Umleitung NICHT
  mit erhöhten Rechten schreiben würde — die Umleitung selbst läuft als normaler
  Benutzer. `tee` bekommt die Rechte dagegen korrekt von `sudo` übergeben).
- `precedence ::ffff:0:0/96  100`: Weist der (IPv4-in-IPv6 gemappten) Adressklasse eine
  hohe Präferenz-Zahl zu, wodurch `getaddrinfo()` (die Standard-Namensauflösungsfunktion,
  die so gut wie jedes Programm inkl. `cargo` nutzt) IPv4-Adressen bevorzugt zurückgibt,
  wenn beide Familien verfügbar sind.
- Betrifft das gesamte System, nicht nur `cargo` — ein Standard-Workaround für Systeme
  mit fehlerhaftem IPv6.

Falls IPv6 auf dem Pi später repariert wird, kann die angehängte Zeile aus
`/etc/gai.conf` wieder entfernt werden (oder das Backup zurückspielen).

### 2.5 `dumbpipe` kompilieren (auf beiden Seiten identisch)

```bash
source "$HOME/.cargo/env"
CARGO_NET_RETRY=10 cargo install dumbpipe
```

- `source "$HOME/.cargo/env"`: Lädt `cargo`/`rustc` in den `PATH` der aktuellen Shell
  (nötig, weil eine neue SSH-Sitzung die `.bashrc`-Änderungen von `rustup` nicht
  automatisch übernimmt, wenn sie nicht interaktiv ist).
- `CARGO_NET_RETRY=10`: Erhöht die Anzahl automatischer Wiederholungsversuche bei
  Netzwerkfehlern beim Herunterladen von Paketen (Standard ist niedriger) — zusätzliche
  Absicherung gegen kurze Netzwerk-Aussetzer während des Builds.
- `cargo install dumbpipe`: Lädt den Quellcode des Pakets `dumbpipe` (Version 0.39.0)
  von crates.io, kompiliert es im Release-Modus und legt das fertige Programm unter
  `~/.cargo/bin/dumbpipe` ab. Es gibt keine fertigen ARM-Binärdateien, daher muss aus
  Quellcode kompiliert werden — auf dem Pi 4 (4 Kerne, 8 GB RAM) dauert das ca. 15-30
  Minuten, auf einem aktuellen Handy mit 8 Kernen deutlich schneller.

### 2.6 Persistente Identität für den Pi (lauschende Seite)

Damit das Verbindungs-Ticket nach einem Neustart des Dienstes/Pi gleich bleibt, braucht
der Pi einen dauerhaft gespeicherten geheimen Schlüssel statt eines bei jedem Start neu
zufällig erzeugten:

```bash
mkdir -p ~/.config/dumbpipe
SECRET=$(openssl rand -hex 32)
echo "IROH_SECRET=$SECRET" > ~/.config/dumbpipe/env
chmod 600 ~/.config/dumbpipe/env
```

- `openssl rand -hex 32`: Erzeugt 32 kryptografisch zufällige Bytes und gibt sie als
  64-stelligen Hex-String aus. 32 Byte = 256 Bit, die von Iroh erwartete Schlüssellänge
  für einen Ed25519-artigen Secret Key.
- `IROH_SECRET=$SECRET`: `dumbpipe` liest diese Umgebungsvariable aus (siehe
  `dumbpipe --help`: *"For all subcommands, you can specify a secret key using the
  IROH_SECRET environment variable. If you don't, a random one will be generated."*).
  Aus diesem Secret leitet sich deterministisch die öffentliche Identität (NodeId) ab,
  auf die sich das Ticket bezieht — derselbe Secret erzeugt immer dasselbe Ticket.
- `chmod 600`: Nur der Besitzer (`picom`) darf die Datei lesen/schreiben. Dieses Secret
  ist sicherheitsrelevant (siehe Abschnitt 2.9) und wird bewusst NICHT in dieser
  Dokumentationsdatei wiederholt.

**Aktueller Stand:** Ein solches Secret wurde bereits erzeugt und liegt auf dem Pi unter
`/home/picom/.config/dumbpipe/env` (Mycodo-Tunnel, weiterhin unverändert aktiv).

### 2.7 Systemd-Dienst auf dem Pi (dauerhafter Tunnel-Listener)

Datei `/etc/systemd/system/dumbpipe-mycodo.service`:

```ini
[Unit]
Description=dumbpipe iroh tunnel for Mycodo web UI (port 443)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=picom
EnvironmentFile=/home/picom/.config/dumbpipe/env
ExecStart=/home/picom/.cargo/bin/dumbpipe listen-tcp --host localhost:443
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Erklärung der Einträge:
- `After=network-online.target` / `Wants=network-online.target`: Sorgt dafür, dass der
  Dienst erst startet, wenn das Netzwerk beim Booten tatsächlich verfügbar ist.
- `Type=simple`: Der in `ExecStart` angegebene Prozess läuft im Vordergrund und IST der
  Dienst.
- `User=picom`: Der Dienst läuft mit den Rechten von `picom`, nicht als `root`.
- `EnvironmentFile=...`: Lädt die `IROH_SECRET`-Variable aus der in 2.6 erzeugten Datei.
- `ExecStart=... listen-tcp --host localhost:443`: `listen-tcp` lässt `dumbpipe` auf
  eingehende Iroh-Verbindungen warten; jede eingehende Verbindung wird lokal an
  `localhost:443` weitergereicht (Mycodo/nginx).
- `Restart=always` / `RestartSec=5`: Startet den Dienst automatisch neu, falls er
  abstürzt — **Vorsicht** bei manuellem Stoppen/Widerrufen: ein `stop` kann in seltenen
  Fällen mit einem bereits durch Absturz gequeueten automatischen Neustart kollidieren
  (siehe Abschnitt 7.5, dort wurde das beim Bot-Backend-Pendant real beobachtet und
  behoben — betrifft diesen Mycodo-Dienst grundsätzlich genauso, war hier aber bisher
  nicht praktisch relevant, da nie einzeln widerrufen).
- `WantedBy=multi-user.target`: Autostart beim Booten.

Aktivieren und starten:

```bash
sudo systemctl daemon-reload
sudo systemctl enable dumbpipe-mycodo.service
sudo systemctl start dumbpipe-mycodo.service
```

Status prüfen: `sudo systemctl status dumbpipe-mycodo.service`

### 2.8 Ticket erzeugen (auf dem Pi)

```bash
set -a
source ~/.config/dumbpipe/env
set +a
dumbpipe generate-ticket
```

- `set -a` / `set +a`: Exportiert automatisch alle in diesem Shell-Block gesetzten
  Variablen in die Umgebung von Kindprozessen. `set +a` schaltet das danach wieder ab.
- `dumbpipe generate-ticket`: Berechnet aus dem aktuell gesetzten `IROH_SECRET` das
  zugehörige Ticket, deterministisch — bei gleichem Secret immer dasselbe Ergebnis.

**Aktueller Stand:** Ein Ticket wurde bereits erzeugt und ist im Verbindungs-Skript auf
dem Handy hinterlegt (siehe 2.9). Der Wert wird hier bewusst nicht wiederholt.

### 2.9 Verbindungsseite (Handy, per Termux/Debian)

Skript unter `/root/connect_mycodo.sh` (in der Termux/Debian-Umgebung auf dem Handy):

```bash
#!/bin/bash
TICKET="<das erzeugte Ticket aus 2.8>"
exec "$HOME/.cargo/bin/dumbpipe" connect-tcp --addr 127.0.0.1:8443 "$TICKET"
```

**Nutzung:**
1. `/root/connect_mycodo.sh` ausführen (bleibt im Vordergrund/Hintergrund aktiv).
2. Im normalen Handy-Browser `https://127.0.0.1:8443` öffnen.
3. Zertifikatswarnung bestätigen (selbstsigniertes Zertifikat).
4. Mycodo-Login erscheint.

### 2.10 Sicherheitseinordnung (Kurzfassung)

- Die Tunnel-Verbindung selbst ist durchgehend verschlüsselt (QUIC/TLS 1.3), auch über
  einen Relay-Server.
- Das Ticket ist ein **Bearer-Credential**: Wer es kennt, kann sich verbinden — es gibt
  keine Geräte-Bindung oder Ablaufzeit. **Genau dieser Umstand war der Auslöser für den
  kompletten Umbau des Discord-Bot-Tunnels auf ein Multi-User-System, siehe Abschnitt 7**
  — für Mycodo (nur ein Nutzer, der Owner selbst) ist das Bearer-Modell weiterhin
  akzeptabel.
- Verlust/Verdacht auf Kompromittierung: Secret-Datei löschen, neues Secret erzeugen,
  Dienst neu starten, neues Ticket erzeugen und verteilen.

### 2.11 Nachtrag (28.08.2026): Entscheidung zum Zertifikat (Mycodo)

Kein neuer öffentlicher Domain-/DDNS-Aufwand — es bleibt beim selbstsignierten
Zertifikat, das Mycodo selbst mitbringt (gültig bis 05.03.2033). Der eigentliche
Übertragungsweg von unterwegs läuft ohnehin über den Iroh-Tunnel, der unabhängig vom
Website-Zertifikat sein eigenes Ende-zu-Ende-verschlüsseltes QUIC/TLS-1.3 bereitstellt.

---

## 3. Discord-Bot-Backend per Iroh/dumbpipe — HISTORISCH, siehe Abschnitt 7 für den aktuellen Weg

> **Dieser gesamte Abschnitt beschreibt den Zustand VOR dem 31.08.2026.** Der hier
> beschriebene einzelne, geteilte `dumbpipe-discordbot.service` mit einem einzigen Ticket
> für alle wurde am 31.08.2026 komplett deaktiviert und entfernt (Unit-Datei + Secret
> gelöscht). Grund: das Ticket ließ sich nicht einzeln widerrufen — ein grundlegender
> Sicherheitsmangel, sobald mehr als eine Person Zugriff braucht. **Für den aktuellen,
> tatsächlich nutzbaren Weg zum Discord-Bot-Backend siehe Abschnitt 7.** Dieser Abschnitt
> bleibt nur als technischer Hintergrund (gleiches dumbpipe/Iroh-Funktionsprinzip) stehen.

Gleiches Prinzip wie in Abschnitt 2, nur mit einem anderen Ziel: nicht Mycodos
nginx/Port 443, sondern das Flask-Backend des Discord-Bots (heute: `backend/app.py` im
Projekt `BlackforestBot-v2`, über nginx auf Port 5000/127.0.0.1 → waitress auf Port
5001/127.0.0.1, siehe `BlackforestBot-Dokumentation.md` Abschnitt 1 für die aktuelle
Backend-Architektur).

Der damalige Dienst `dumbpipe-discordbot.service` leitete `localhost:5000` weiter, mit
einem einzigen Secret unter `~/.config/dumbpipe/env-bot` (mittlerweile gelöscht). Das
Verbindungsskript `/root/connect_discordbot.sh` auf dem Handy (falls noch vorhanden) mit
dem alten Ticket **funktioniert seit dem 31.08.2026 nicht mehr** — der Dienst existiert
nicht mehr.

---

## 4. Claude Code auf dem Handy einrichten (Termux)

So wird die Umgebung aufgesetzt, in der auch diese Anleitung erstellt wurde. Claude Code
läuft dabei nicht direkt in Android, sondern in einer vollwertigen Linux-Umgebung
(Debian, via `proot-distro`), die innerhalb von Termux läuft.

### 4.1 Termux installieren

Termux aus **F-Droid** installieren (https://f-droid.org/packages/com.termux/), NICHT
aus dem Google Play Store — die dortige Version ist veraltet und wird nicht mehr
gepflegt.

### 4.2 Basis-Pakete und `proot-distro`

In Termux öffnen und ausführen:

```bash
pkg update && pkg upgrade -y
pkg install -y proot-distro
```

- `pkg update && pkg upgrade -y`: Aktualisiert Termuxs eigene Paketliste und installierte
  Pakete, bevor Neues installiert wird.
- `proot-distro`: Ein Werkzeug, das komplette Linux-Distributionen (hier: Debian) in
  einem Unterverzeichnis installiert und mittels `proot` nutzbar macht.

### 4.3 Debian-Distribution installieren und betreten

```bash
proot-distro install debian
proot-distro login debian
```

### 4.4 Node.js installieren (innerhalb der Debian-Umgebung)

```bash
apt update && apt install -y nodejs npm
node --version
```

### 4.5 Claude Code installieren

```bash
npm install -g @anthropic-ai/claude-code
```

### 4.6 Anmelden und starten

```bash
claude
```

### 4.7 Praktischer Hinweis aus diesem Setup

Diese Umgebung lief während der Einrichtung unter `Debian GNU/Linux 13 (trixie)`,
erreichbar u. a. an den Android-App-UID-Gruppen `aid_u0_a481_cache`/`aid_all_a481`. SSH-
Schlüssel, `~/.cargo`, und alle in Abschnitt 2 erzeugten Dateien liegen innerhalb dieser
Debian/`proot`-Umgebung, nicht im "nackten" Termux selbst.

---

## 5. Zusammenfassung: beteiligte Dateien und Pfade

| Was | Wo |
|---|---|
| SSH Public/Private Key (Handy) | `~/.ssh/id_ed25519[.pub]` (in der Debian/proot-Umgebung) |
| Autorisierte Keys (Pi) | `/home/picom/.ssh/authorized_keys` |
| SSH-Server-Konfiguration (Pi) | `/etc/ssh/sshd_config` |
| Iroh-Secret Mycodo-Tunnel (Pi, persistent) | `/home/picom/.config/dumbpipe/env` (chmod 600) |
| dumbpipe-Binary (Pi) | `/home/picom/.cargo/bin/dumbpipe` |
| dumbpipe-Binary (Handy) | `~/.cargo/bin/dumbpipe` (in der Debian/proot-Umgebung) |
| systemd-Dienst Mycodo-Tunnel (Pi) | `/etc/systemd/system/dumbpipe-mycodo.service` |
| IPv6-Fix (Pi) | `/etc/gai.conf` (letzte Zeile) |
| Mycodo-Zertifikat (selbstsigniert, bis 2033 gültig) | `/opt/Mycodo/mycodo/mycodo_flask/ssl_certs/server.crt` |
| Verbindungs-Skript Mycodo (Handy) | `/root/connect_mycodo.sh` (in der Debian/proot-Umgebung) |
| Discord-Bot-Zertifikat (selbstsigniert, bis 2036 gültig) | `~/Desktop/BlackforestBot-v2/backend/ssl/server.crt` |
| App-Zugänge (Discord-Bot-Tunnel, pro Person) | `~/.config/dumbpipe/env-person-<slug>` + `dumbpipe-discordbot-<slug>.service`, siehe Abschnitt 7 |
| Verwaltungs-Skript App-Zugänge (CLI) | `~/Desktop/BlackforestBot-v2/scripts/manage_bot_access.sh` |
| Bot Connect Dashboard (lokales Web-UI für App-Zugänge) | `~/Desktop/BlackforestBot-v2/bot-connect-dashboard/`, nur `127.0.0.1:5050` |
| Deploy Key fürs GitHub-Repo (Pi, nur Git-Push) | `~/.ssh/github_android_build[.pub]` |
| Diese Anleitung | `~/Desktop/Fernzugriff-Setup-Anleitung.md` (Pi) + `docs/Fernzugriff-Setup-Anleitung.md` im GitHub-Repo |

---

## 6. Nachtrag (28.08.2026): Discord-Bot komplett neu gebaut

Der Discord-Bot und sein Backend wurden komplett neu aufgesetzt unter
`~/Desktop/BlackforestBot-v2/`. Der alte Bot (`My-Discord-Bot/Bot1/BlackforestBot1.1.py`)
und das alte Backend (`WebLogin/app2.py`) wurden gestoppt, aber **nicht gelöscht** —
sie liegen unverändert weiter auf der Platte, zusätzlich existiert ein volles Backup
unter `~/Backups/discordbot-backup_20260828_214040.tar.gz`.

Für alles Bot-spezifische (Architektur, Befehle, Datenbank, Berechtigungen,
Start/Stop, OAuth-Setup) siehe `~/Desktop/BlackforestBot-Dokumentation.md` bzw.
`docs/BlackforestBot-Dokumentation.md` im GitHub-Repo.

---

## 7. Nachtrag (31.08.2026): Bot-Ökosystem mit vier Teilen — Multi-User-Zugang zum Bot-Backend

Das Projekt besteht inzwischen aus vier eigenständigen Teilen:

1. **Bot** — der Discord-Bot-Prozess selbst.
2. **Bot Backend** — das Flask-Web-Backend, über den Iroh-Tunnel übers Internet
   erreichbar (Port 5000/8500, siehe unten).
3. **Bot Connect App** — die neue Android-App (ersetzt den manuellen
   Termux+dumbpipe-CLI-Weg für normale Nutzer, siehe 7.2).
4. **Bot Connect Dashboard** — ein separates, bewusst NUR lokal erreichbares Web-Tool zur
   Verwaltung der App-Zugänge (siehe 7.3).

### 7.1 Warum der alte, geteilte Tunnel abgeschafft wurde

Der in Abschnitt 3 beschriebene `dumbpipe-discordbot.service` nutzte EIN Ticket für
buchstäblich jeden, der es kannte — es gab keine Möglichkeit, einer einzelnen Person den
Zugriff zu entziehen, ohne allen anderen ebenfalls das Ticket zu erneuern. Ein
Sicherheitsvorfall (ein zweiter Discord-User bekam unerklärt Admin-Rechte im Backend,
siehe Audit-Log-Vorfall vom 29.08.2026) machte diese Schwäche konkret sichtbar. Am
31.08.2026 wurde der Dienst komplett gestoppt, deaktiviert und seine Dateien gelöscht.

### 7.2 Bot Connect App (Android)

Schlanke Kotlin-App (`android-app/` im Repo, Package `de.blackforest.bottunnel`,
minSdk 26). Bündelt das für Android cross-kompilierte `dumbpipe`-Binary
(`jniLibs/arm64-v8a/libdumbpipe.so`, gebaut per GitHub Actions — lokales Cross-Compile
auf ARM-Hosts scheiterte, siehe Abschnitt 8), startet es beim App-Start als Subprozess
mit dem persönlichen Ticket, zeigt eine WebView auf `https://127.0.0.1:8500` (lokal auf
dem Handy — der Tunnel-Client lauscht dort). Zertifikats-Pinning gegen das bekannte
Backend-Zertifikat (SHA-256-Fingerprint fest in `MainActivity.kt`). Tunnel-Prozess wird
beim Schließen/in-den-Hintergrund-Gehen der App automatisch beendet — kein Hintergrund-
Dienst, keine Dauer-Benachrichtigung.

Vollständige Details, Installation, Architektur: `android-app/README.md` im Repo.

**Bekanntes, noch nicht vollständig gelöstes Problem (Stand 31.08.2026):** Manche
Verbindungsversuche enden mit `ERR_TIMED_OUT` in der App-WebView. Serverseitig ließ sich
keine erfolgreiche eingehende Verbindung während der Testfenster nachweisen — vermutlich
eine Kombination aus den in Abschnitt 2.1 beschriebenen Relay-Instabilitäten und
grundsätzlich langsamerem/schwierigerem NAT-Hole-Punching über echte
Mobilfunk-/Internet-Strecken (im Unterschied zu Tests, bei denen Client und Server im
selben Netz liefen). Die App wartet inzwischen aktiv (bis zu 45s) auf einen einsatzbereiten
Tunnel statt nur kurz zu warten, und protokolliert die `dumbpipe`-Ausgabe für bessere
Diagnose — das grundlegende Netzwerk-/Relay-Problem selbst ist damit aber nicht
zwangsläufig behoben. Wird weiter untersucht.

### 7.3 Bot Connect Dashboard (lokales Verwaltungs-Tool)

Eigener, komplett getrennter Flask-Prozess auf **Port 5050**, gebunden **ausschließlich
an `127.0.0.1`** — kein nginx-Eintrag, kein dumbpipe-Tunnel dafür, damit er strukturell
NIE übers Internet erreichbar ist (wichtig: Port 5001, das Haupt-Backend, ist trotz
"nur 127.0.0.1" über den Tunnel doch aus dem Internet erreichbar, siehe 7.4 — das
Dashboard braucht deshalb einen wirklich separaten, ungetunnelten Port). Kein
Login-System — die Netzwerk-Isolation selbst ist die Zugriffskontrolle, analog zu SSH.

Zugriff: lokal auf dem Pi (`http://127.0.0.1:5050`) oder per SSH-Port-Forwarding:
```bash
ssh -L 5050:127.0.0.1:5050 picom@192.168.178.30
```

Funktion: Zugänge (Name, verknüpfte Discord-User-ID, Ticket) erstellen und widerrufen,
per Dropdown aus der aktuellen Discord-Mitgliederliste. Beim Widerruf wird zusätzlich
eine eventuell bestehende Backend-Login-Session der Person invalidiert (siehe 7.4).

Details: `bot-connect-dashboard/README.md` im Repo.

### 7.4 Sicherheits-Mechanismus: Session-Invalidierung bei Widerruf

Reiner Netzwerk-Widerruf (Tunnel kappen) reicht nicht: Eine bereits bestehende
Discord-OAuth-Login-Session im Backend (Browser-Cookie) bliebe sonst technisch gültig,
selbst nachdem der Tunnel-Zugang entzogen wurde. Deshalb ist jeder App-Zugang jetzt an
eine konkrete Discord-User-ID gebunden; beim Widerruf wird zusätzlich ein Zeitstempel in
der Tabelle `user_session_invalidations` gesetzt. Das Backend prüft bei JEDEM
authentifizierten Request, ob die aktuelle Session VOR diesem Zeitstempel ausgestellt
wurde — falls ja, wird sie serverseitig verworfen und ein erneuter Login erzwungen.

### 7.5 Wichtige technische Erkenntnis: Port 5001 ist trotz "nur 127.0.0.1" tunnel-erreichbar

Beim Bau des Dashboards wurde zunächst angenommen, der bestehende interne Backend-Port
(127.0.0.1:5001) hätte denselben Vertrauens-Level wie ein neuer, rein lokaler Port. Das
ist FALSCH: Der dumbpipe-Tunnel-Dienst für das Bot-Backend leitet ebenfalls an
`localhost:5000` (nginx) weiter, welches wiederum an `127.0.0.1:5001` durchreicht — für
nginx (und ProxyFix/Flask) sehen echte Internet-Nutzer über den Tunnel und rein lokale
Aufrufe auf Port 5001 IDENTISCH aus (beide kommen als `127.0.0.1` an, da der Tunnel selbst
schon auf Loopback terminiert, bevor nginx ins Spiel kommt). Eine neue, ungeschützte Route
auf Port 5001 wäre deshalb genauso gut aus dem Internet erreichbar wie lokal. Lösung: ein
komplett separater dritter Port (**5052**, `backend/internal_app.py`), für den nginx
buchstäblich keinen `proxy_pass`-Eintrag besitzt — nur DAS ist echte, strukturelle
Isolation. Port 5052 liefert nur die Discord-Mitgliederliste fürs Dashboard-Dropdown,
sonst nichts.

**Vollständige Port-Übersicht des Discord-Bot-Systems:**

| Port | Bindung | Zweck | Übers Internet (Tunnel) erreichbar? |
|---|---|---|---|
| 5000 | `127.0.0.1` (nginx, TLS) | Öffentlicher Einstiegspunkt fürs Backend | Ja (das ist der Sinn) |
| 5001 | `127.0.0.1` (waitress, intern) | Eigentliche Flask-App, von nginx durchgereicht | Ja, indirekt über 5000 — **trotz "nur lokal" wirkt es faktisch öffentlich** |
| 5050 | `127.0.0.1` (Flask-Dev-Server) | Bot Connect Dashboard | **Nein**, kein nginx/dumbpipe-Eintrag |
| 5052 | `127.0.0.1` (waitress, intern) | Interne Mitgliederliste fürs Dashboard | **Nein**, kein nginx/dumbpipe-Eintrag |
| 8500 (auf dem Handy) | `127.0.0.1` (dumbpipe connect-tcp) | Lokaler Client-Port der Bot Connect App | N/A (das ist der Client, nicht der Server) |

### 7.6 Bekannter Bug + Fix: Race Condition zwischen Widerruf und dumbpipe-Absturz-Neustart

`dumbpipe` stürzt gelegentlich selbstständig ab (siehe 2.1) und wird durch
`Restart=always` automatisch neu gestartet. Ein Widerruf, der genau in das ~5-Sekunden-
Fenster zwischen einem Absturz und dem bereits gequeueten automatischen Neustart fiel,
konnte dazu führen, dass der Dienst Sekunden nach einem erfolgreichen `stop` wieder aktiv
wurde — mit Resten (Secret-Datei, Unit-Datei), die einen späteren Neuanlage-Versuch
blockierten. Fix (in `scripts/tunnel_access_core.py`): `stop` wird jetzt mehrfach mit
`systemctl is-active` verifiziert, bevor die Dateien gelöscht werden.

---

## 8. Nachtrag (31.08.2026): Android-Build-Pipeline (GitHub Actions statt lokal)

### 8.1 Warum nicht lokal cross-kompiliert wird

Der Android-NDK stellt seine vorkompilierte Toolchain offiziell nur für
`linux-x86_64`-Hosts bereit. Sowohl der Pi (`armhf`-Userland, siehe 2.3) als auch der für
die Entwicklung genutzte Host sind ARM-basiert — die NDK-Toolchain lässt sich dort nicht
direkt ausführen. Ein Versuch, das per QEMU-x86_64-Emulation zu umgehen, scheiterte an
einem echten QEMU-Übersetzungsfehler in einer Krypto-Bibliothek (`ring`-Crate,
handgeschriebene ARMv8-Assemblerdatei). Lösung: der Build läuft komplett auf einem
echten x86_64-GitHub-Actions-Runner.

### 8.2 Workflow-Aufbau

`.github/workflows/build-android.yml`, zwei Jobs:
1. `build-dumbpipe`: installiert Rust + Android-NDK-Target, `cargo-ndk`, den Android-NDK
   (`nttld/setup-ndk`), klont `n0-computer/dumbpipe` frisch (kein Vendoring im eigenen
   Repo), baut für `aarch64-linux-android`/`arm64-v8a`, lädt das Binary als Artefakt hoch.
2. `build-apk`: lädt das Binary herunter, legt es als `jniLibs/arm64-v8a/libdumbpipe.so`
   ab (mit `useLegacyPackaging = true` in `app/build.gradle.kts`, sonst würde Android das
   "unechte" .so komprimiert in der APK belassen statt als ausführbare Datei zu
   extrahieren), baut die Debug-APK per Gradle, lädt sie als Artefakt hoch.

Trigger: Push auf `main` mit Änderungen unter `android-app/**` oder am Workflow selbst,
oder manuell per `workflow_dispatch`.

### 8.3 Zugang zum GitHub-Repo

Push erfolgt über einen repo-eigenen **Deploy Key** (`~/.ssh/github_android_build` auf
dem Pi, nur Schreibzugriff auf genau dieses eine Repo), nicht über ein Personal Access
Token. Workflow-Status/Artefakte werden über `gh` CLI abgerufen (per Device-Code-Login
auf dem Pi autorisiert, Account `Lemm1988`).

Repo: `github.com/Lemm1988/Blackforest-Discord-Bot` (privat). Enthält den kompletten
Projektcode (`bot/`, `backend/`, `android-app/`, `bot-connect-dashboard/`, `scripts/`)
sowie diese Dokumentation unter `docs/`.
