# Android-App (Iroh-Tunnel-Zugang)

Teil des BlackforestBot-v2-Repos (nicht mehr eigenstaendig). Zweck: Bereitstellung des
`dumbpipe`-Binaries (Iroh-P2P-Tunnel-Tool) fuer Android (Ziel-Architektur
`aarch64-linux-android` / ABI `arm64-v8a`), als Grundlage fuer eine schlanke Android-App,
mit der Mods/Owner ohne Termux-Umweg per Iroh-Tunnel auf das Discord-Bot-Backend zugreifen
koennen. Siehe `~/Desktop/Fernzugriff-Setup-Anleitung.md` auf dem Pi fuer den bestehenden
Termux/dumbpipe-Tunnel-Mechanismus, den diese App ersetzen soll.

## Warum GitHub Actions statt lokalem Cross-Compile

Lokale Cross-Compile-Versuche (sowohl der Pi als auch der Arbeits-Host sind ARM-basiert,
der Android-NDK bietet aber offiziell nur eine x86_64-Toolchain) scheiterten selbst unter
QEMU-x86_64-Emulation an einem Emulations-Bug in der `ring`-Krypto-Crate (handgeschriebene
ARMv8-Assembler-Datei, die QEMUs x86_64-Uebersetzung nicht korrekt verarbeitet). Deshalb
laeuft der Cross-Compile-Build auf einem echten x86_64-Runner via GitHub Actions
(`.github/workflows/build-android.yml`, liegt auf Repo-Root-Ebene, wie von GitHub
vorausgesetzt).

## Ablauf

1. Push auf `main` mit Aenderungen unter `android-app/**` oder am Workflow selbst (oder
   manueller Trigger `workflow_dispatch`) startet den Build.
2. Der Workflow installiert Rust + das Android-Target, `cargo-ndk` sowie den Android-NDK
   (ueber die Community-Action `nttld/setup-ndk`), klont den offiziellen `dumbpipe`-Quellcode
   (`n0-computer/dumbpipe`) frisch von GitHub (kein Vendoring in diesem Repo) und baut ihn
   per `cargo ndk -t arm64-v8a build --release`.
3. Das fertige Binary wird als Workflow-Artefakt (`dumbpipe-android-arm64`) bereitgestellt.

## Zugriff

Push erfolgt ueber einen repo-eigenen **Deploy Key** (Schreibzugriff auf dieses eine Repo,
kein Personal Access Token noetig). Privater Schluessel liegt ausschliesslich lokal auf
dem Pi (`~/.ssh/github_android_build`), niemals in diesem Repo. Fuer den Actions-Status
selbst nutzt der Agent zusaetzlich `gh` (GitHub CLI, per Device-Code-Login autorisiert).

## Naechster Schritt (noch nicht Teil dieses Repos)

Das gebaute Binary wird in eine schlanke Android-Kotlin-App gebundlet, die es beim
App-Start als Subprozess mit einem persoenlichen Iroh-Ticket startet, eine WebView auf
das Bot-Backend zeigt und den Tunnel beim Schliessen der App wieder beendet.

## Die App selbst

- **Package:** `de.blackforest.bottunnel`, App-Name "BlackforestBot Zugang".
- **minSdk 26** (Android 8.0) fuer breite Geraeteabdeckung.
- **Erster Start:** Eingabemaske fuer das persoenliche Iroh-Ticket (Textfeld + Speichern).
  Wird via `EncryptedSharedPreferences` (Jetpack Security Crypto, AES-256) lokal verschluesselt
  gespeichert, nie im Klartext, nie fest einkompiliert.
- **Danach:** Beim App-Start (bzw. jedes Mal, wenn die App in den Vordergrund kommt) wird der
  Tunnel-Prozess automatisch gestartet, kurze Ladeanzeige, dann laedt eine WebView
  `https://127.0.0.1:8500`. Geht die App in den Hintergrund oder wird geschlossen, wird der
  Tunnel-Prozess sofort wieder beendet (`Process.destroy()`) - kein Foreground-Service, keine
  Dauer-Benachrichtigung, kein Weiterlaufen im Hintergrund.
- **Ticket aendern:** Ueber das Overflow-Menue (drei Punkte oben rechts) -> "Ticket aendern",
  z.B. nach einem Widerruf/neuen Ticket vom Owner.
- **Zertifikats-Pinning:** Das Backend nutzt ein selbstsigniertes Zertifikat. Die App vertraut
  NICHT pauschal jedem Zertifikat (kein `handler.proceed()` fuer jeden SSL-Fehler), sondern
  vergleicht in `onReceivedSslError` den SHA-256-Fingerabdruck des tatsaechlich praesentierten
  Zertifikats exakt gegen den fest hinterlegten, bekannten Fingerabdruck von
  `backend/ssl/server.crt`. **Falls das Zertifikat auf dem Pi je neu erzeugt wird, muss der
  Fingerabdruck in `MainActivity.kt` (`EXPECTED_FINGERPRINT_SHA256`) manuell aktualisiert und
  die App neu gebaut werden** - sonst verweigert die App die Verbindung.

## Installation der Debug-APK

Kein Play Store, kein eigenes Signing-Setup - die APK aus dem GitHub-Actions-Artefakt
(`blackforestbot-tunnel-debug-apk`) ist mit dem Standard-Android-Debug-Schluessel signiert.
Herunterladen, aufs Handy uebertragen, "Installation aus unbekannten Quellen" fuer die
jeweilige Quelle (Dateimanager/Downloads) erlauben, APK antippen zum Installieren.
