#!/usr/bin/env python3
"""
Patcht dumbpipes src/main.rs vor dem Android-Build.

Ursache (per Recherche in der offiziellen iroh-Doku + iroh-Quellcode
bestaetigt, ueber mehrere Iterationen prezisiert):

Endpoint::builder(...).bind() konstruiert standardmaessig einen
DnsResolver ueber "des Host-Systems DNS-Konfiguration" (Docs:
"By default, a new DNS resolver is created which is configured to use
the host system's DNS configuration."). Dieser EINE Resolver wird laut
Doku fuer ZWEI Zwecke gleichzeitig verwendet: Relay-Hostnamen aufloesen
(IMMER noetig, unabhaengig von jeder address_lookup-Konfiguration) UND
Endpoint-Adress-Aufloesung (nur falls DnsAddressLookup konfiguriert ist).

Ein erster Patch-Versuch entfernte nur DnsAddressLookup aus dem
address_lookup-Chain (presets::N0 -> presets::Minimal + manuelles Pkarr)
- das hat den Absturz NICHT behoben, weil der Default-Resolver
weiterhin fuer die Relay-Hostnamen-Aufloesung gebraucht wird (bestaetigt
per GitHub-Code-Suche: iroh/src/net_report/reportgen.rs nutzt
DnsResolver::default() unabhaengig von address_lookup - das ist iroh's
eingebaute Netzwerk-Selbstdiagnose fuers NAT-Hole-Punching, nicht
abschaltbar).

Auf Android liest genau dieser Default-Resolver
(hickory_resolver::system_conf::read_system_conf) die System-DNS-Server
via JNI/ndk-context aus LinkProperties.getDnsServers() - das erfordert
einen echten, via JNI_OnLoad initialisierten Android-Kontext, den ein
als Subprozess gestarteter Binary (unser Fall: ProcessBuilder aus der
Android-App heraus) grundsaetzlich nie hat -> Panic
"android context was not initialized". dumbpipe baut mit panic=abort
(separat gefixt), wodurch iroh's eigenes catch_unwind fuer diesen Fall
zusaetzlich wirkungslos ist.

ECHTER Fix: Endpoint::builder() bietet dns_resolver(), um den
Default-Resolver komplett zu ersetzen - fuer ALLE internen Zwecke
(Relay-Hostnamen UND address_lookup gleichermaessig, laut Doku:
"resolving relay hostnames" + "endpoint address resolution when
DnsAddressLookup is configured"). DnsResolver::with_nameserver(addr)
konstruiert einen Resolver mit einem fest hinterlegten Nameserver OHNE
jede System-Konfiguration zu lesen - der Android-JNI-Codepfad wird
dadurch nie mehr aufgerufen, komplett unabhaengig davon welche
address_lookup-Strategie genutzt wird. presets::N0 (voller
Funktionsumfang: Pkarr-Publisher/-Resolver + DnsAddressLookup + Relay)
bleibt dabei unveraendert bestehen - nur der DAHINTERLIEGENDE Resolver
wird ausgetauscht, keine Funktionalitaet geht verloren.

Nameserver: 8.8.8.8:53 (Google) - das ist zufaellig auch der Server,
auf den iroh selbst zurueckfaellt, wenn die Android-JNI-Aufloesung
fehlschlaegt (siehe iroh-dns/src/android.rs: "will then use Google's
fallback DNS servers") - konsistent mit iroh's eigener Fallback-Wahl.

Wird von .github/workflows/build-android.yml aufgerufen, direkt nach
dem Klonen des dumbpipe-Quellcodes. Bricht bewusst hart ab (exit 1),
falls der erwartete Original-Code nicht mehr exakt passt (z.B. nach
einem Upstream-Update) - dann muss dieser Patch manuell nachgezogen
werden, statt still nichts zu tun.
"""
import sys

PATH = "dumbpipe-src/src/main.rs"

OLD_IMPORT = """use iroh::{
    endpoint::{presets, Accepting},
    Endpoint, EndpointAddr, SecretKey,
};"""

NEW_IMPORT = """use iroh::{
    dns::DnsResolver,
    endpoint::{presets, Accepting},
    Endpoint, EndpointAddr, SecretKey,
};"""

OLD_BUILDER = """    let mut builder = Endpoint::builder(presets::N0)
        .secret_key(secret_key)
        .alpns(alpns);"""

NEW_BUILDER = """    // ANDROID-FIX (Blackforest): expliziter dns_resolver() statt des
    // Default-Resolvers, der auf Android via JNI abstuerzt (fehlender
    // Android-Kontext bei Subprozess-Ausfuehrung) - siehe
    // patch_dumbpipe_android.py fuer die vollstaendige Begruendung.
    // presets::N0 bleibt ansonsten unveraendert (voller Funktionsumfang).
    let dns_nameserver: std::net::SocketAddr = "8.8.8.8:53".parse().expect("valid nameserver addr");
    let mut builder = Endpoint::builder(presets::N0)
        .dns_resolver(DnsResolver::with_nameserver(dns_nameserver))
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
