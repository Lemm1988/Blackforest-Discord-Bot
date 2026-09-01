#!/usr/bin/env python3
"""
Patcht dumbpipes src/main.rs vor dem Android-Build.

Ursache: Endpoint::builder(presets::N0) enthaelt drei unabhaengige
Adress-Aufloesungs-Mechanismen (siehe docs.iroh.computer/endpoint/presets):
PkarrPublisher::n0_dns() + PkarrResolver::n0_dns() (beide reine HTTPS-Requests,
kein Android-JNI noetig) UND zusaetzlich DnsAddressLookup::n0_dns(), die auf
Android via ndk-context/JNI-Zugriff auf LinkProperties.getDnsServers() abstuerzt
("android context was not initialized"). dumbpipe baut mit panic=abort, wodurch
selbst iroh's eigenes internes catch_unwind fuer genau diesen Fall wirkungslos
ist - der ganze Prozess stirbt.

Fix: Endpoint manuell aus presets::Minimal (nur Krypto-Setup) + Pkarr
Publisher/Resolver + normalem Relay-Modus zusammenbauen, OHNE
DnsAddressLookup - volle NAT-Traversal-/Relay-Funktionalitaet bleibt
erhalten, der Android-Absturz-Pfad wird schlicht nie mehr aufgerufen.

Wird von .github/workflows/build-android.yml aufgerufen, direkt nach dem
Klonen des dumbpipe-Quellcodes. Bricht bewusst hart ab (exit 1), falls der
erwartete Original-Code nicht mehr exakt passt (z.B. nach einem
Upstream-Update) - dann muss dieser Patch manuell nachgezogen werden, statt
still nichts zu tun.
"""
import sys

PATH = "dumbpipe-src/src/main.rs"

OLD_IMPORT = """use iroh::{
    endpoint::{presets, Accepting},
    Endpoint, EndpointAddr, SecretKey,
};"""

NEW_IMPORT = """use iroh::{
    address_lookup::{PkarrPublisher, PkarrResolver},
    endpoint::{presets, Accepting, RelayMode},
    Endpoint, EndpointAddr, SecretKey,
};"""

OLD_BUILDER = """    let mut builder = Endpoint::builder(presets::N0)
        .secret_key(secret_key)
        .alpns(alpns);"""

NEW_BUILDER = """    // ANDROID-FIX (Blackforest): presets::Minimal + manuelles Pkarr statt
    // presets::N0, um den Android-JNI-DNS-Absturzpfad (DnsAddressLookup) zu
    // vermeiden - siehe patch_dumbpipe_android.py fuer Details.
    let mut builder = Endpoint::builder(presets::Minimal)
        .address_lookup(PkarrPublisher::n0_dns())
        .address_lookup(PkarrResolver::n0_dns())
        .relay_mode(RelayMode::Default)
        .secret_key(secret_key)
        .alpns(alpns);"""


def main() -> None:
    with open(PATH, encoding="utf-8") as f:
        content = f.read()

    missing = []
    if OLD_IMPORT not in content:
        missing.append("import-Block")
    if OLD_BUILDER not in content:
        missing.append("create_endpoint-Builder-Aufbau")

    if missing:
        print(
            f"FEHLER: Erwarteter Original-Code nicht gefunden ({', '.join(missing)}) - "
            "dumbpipe hat sich upstream vermutlich geaendert, dieser Patch muss "
            "manuell an patch_dumbpipe_android.py nachgezogen werden.",
            file=sys.stderr,
        )
        sys.exit(1)

    content = content.replace(OLD_IMPORT, NEW_IMPORT)
    content = content.replace(OLD_BUILDER, NEW_BUILDER)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print("Patch erfolgreich angewendet.")


if __name__ == "__main__":
    main()
