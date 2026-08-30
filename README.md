# BlackforestBot Android Build

Eigenstaendiges Hilfs-Repo, getrennt vom Hauptprojekt (`BlackforestBot-v2` auf dem Pi).
Zweck: Bereitstellung des `dumbpipe`-Binaries (Iroh-P2P-Tunnel-Tool) fuer Android
(Ziel-Architektur `aarch64-linux-android` / ABI `arm64-v8a`), als Grundlage fuer eine
schlanke Android-App, mit der Mods/Owner ohne Termux-Umweg per Iroh-Tunnel auf das
Discord-Bot-Backend zugreifen koennen.

## Warum ein eigenes Repo, warum GitHub Actions

Lokale Cross-Compile-Versuche (sowohl der Pi als auch der Arbeits-Host sind ARM-basiert,
der Android-NDK bietet aber offiziell nur eine x86_64-Toolchain) scheiterten selbst unter
QEMU-x86_64-Emulation an einem Emulations-Bug in der `ring`-Krypto-Crate (handgeschriebene
ARMv8-Assembler-Datei, die QEMUs x86_64-Uebersetzung nicht korrekt verarbeitet). Deshalb
laeuft der eigentliche Cross-Compile-Build stattdessen auf einem echten x86_64-Runner via
GitHub Actions (`.github/workflows/build-android.yml`).

## Ablauf

1. Push auf `main` (oder manueller Trigger `workflow_dispatch`) startet den Workflow.
2. Der Workflow installiert Rust + das Android-Target, `cargo-ndk` sowie den Android-NDK
   (ueber die Community-Action `nttld/setup-ndk`), klont den offiziellen `dumbpipe`-Quellcode
   (`n0-computer/dumbpipe`) frisch von GitHub (kein Vendoring in diesem Repo) und baut ihn
   per `cargo ndk -t arm64-v8a build --release`.
3. Das fertige Binary wird als Workflow-Artefakt (`dumbpipe-android-arm64`) bereitgestellt
   und manuell ueber die GitHub-Weboberflaeche (Actions-Tab des Laufs) heruntergeladen.

## Zugriff

Push erfolgt ueber einen repo-eigenen **Deploy Key** (nur Schreibzugriff auf genau dieses
Repo, kein Personal Access Token noetig). Der private Schluessel liegt ausschliesslich
lokal auf dem Pi (`~/.ssh/github_android_build`), niemals in diesem Repo.

## Naechster Schritt (noch nicht Teil dieses Repos)

Das gebaute Binary wird in eine schlanke Android-Kotlin-App gebundlet, die es beim
App-Start als Subprozess mit einem persoenlichen Iroh-Ticket startet, eine WebView auf
das Bot-Backend zeigt und den Tunnel beim Schliessen der App wieder beendet.
