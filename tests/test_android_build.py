"""Waechter fuer den Android-Build-Workflow.

Diese beiden Schritte sehen nach Kleinkram aus, sind aber die Ursachenbehebung
fuer ein Problem, an dem die App tagelang nicht funktionierte - ohne Absturz und
ohne Fehlermeldung, sie kam schlicht nie durch. Wer sie beim Aufraeumen entfernt,
bricht die App erneut, und zwar wieder stumm. Deshalb hier festgeschrieben.

Hintergrund vollstaendig in docs/Fernzugriff-Setup-Anleitung.md, Abschnitt 9.1.
"""
from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build-android.yml"


def test_workflow_exists():
    assert WORKFLOW.exists(), "Der Android-Build-Workflow fehlt"


def test_iroh_is_lifted_past_the_lockfile_pin():
    """dumbpipes mitgelieferte Cargo.lock nagelt iroh auf 1.0.0 fest, und
    `cargo build` respektiert Lockfiles. iroh-dns 1.0.0 faengt den
    Android-JNI-DNS-Panic nur in Debug-Builds ab - im Release-Build (also in der
    APK) schlaegt er durch. Ohne dieses Update baut die Pipeline wieder eine
    APK, die beim ersten DNS-Zugriff stirbt.
    """
    inhalt = WORKFLOW.read_text()
    assert "cargo update" in inhalt and "-p iroh" in inhalt, (
        "Der Schritt 'cargo update -p iroh' fehlt - die APK wuerde wieder gegen "
        "iroh 1.0.0 gebaut, das den Android-Panic im Release nicht abfaengt."
    )


def test_panic_unwind_patch_is_present():
    """iroh faengt den JNI-Panic per catch_unwind ab - das kann bei
    `panic = "abort"` grundsaetzlich nicht funktionieren, weil dann nie
    abgewickelt wird. dumbpipe setzt abort in seinem Release-Profil, der Patch
    ist also zwingend."""
    inhalt = WORKFLOW.read_text()
    assert 'panic = "abort"' in inhalt and 'panic = "unwind"' in inhalt, (
        "Der sed-Patch von panic=abort auf unwind fehlt - iroh's catch_unwind "
        "kann dann nicht greifen und der Prozess stirbt beim ersten DNS-Zugriff."
    )


def test_obsolete_dns_resolver_patch_is_not_reintroduced():
    """Der fruehere Workaround ersetzte iroh's getesteten Fallback
    (mehrere Server, UDP und TCP) durch einen einzelnen UDP-Nameserver ohne
    Ausweichpfad. Er verhinderte zwar den Panic, aber der Verbindungsaufbau kam
    danach nicht mehr zustande - stumm, ohne Fehlermeldung."""
    inhalt = WORKFLOW.read_text()
    aktive_zeilen = [
        z for z in inhalt.splitlines()
        if z.strip() and not z.strip().startswith("#")
    ]
    aktiv = "\n".join(aktive_zeilen)
    assert "patch_dumbpipe_android.py" not in aktiv, (
        "Der DNS-Resolver-Patch ist wieder aktiv - er ist seit dem iroh-Update "
        "unnoetig und verhindert den Verbindungsaufbau."
    )
