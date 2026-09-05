"""Leitet aus den Discord-Rollen einer Person ihre Backend-Rechte ab.

Das Gegenstück zu `rollenabgleich.py`. Beide beantworten dieselbe Frage aus
entgegengesetzter Richtung:

    rollenabgleich.py   Recht im Backend  ->  Rolle auf dem Server
    rechteabgleich.py   Rolle auf dem Server  ->  Recht im Backend

Warum es die zweite Richtung überhaupt braucht: Bis zum 05.09.2026 gab es sie
nicht — `grep -rn "on_member_update" bot/` fand keinen einzigen Treffer. Wer in
Discord von Hand die Moderator-Rolle bekam, hatte im Backend trotzdem keine
Rechte. Auf dem Server standen dadurch zwei Leute mit Moderator-Rolle und ohne
jede Berechtigung, während umgekehrt jemand alle Moderationsrechte hatte, aber
die Rolle nicht trug. Patrick am 05.09.2026: „wenn ein user berechtigungen
erhält in beide richtungen setzen."

Wie in rollenabgleich.py steht hier nur die Logik — kein discord.py, keine
Datenbank. Das Ausführen ist ein eigener Schritt und bewusst getrennt: Erst
rechnen, dann handeln, und beides einzeln prüfbar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Rechte, die niemals über eine Rolle vergeben oder entzogen werden.
#:
#: `is_admin` ist der Generalschlüssel — has_permission() lässt damit alles
#: durch. Würde er an einer Rolle hängen, könnte ein versehentlich vergebener
#: Rollenklick jemandem die volle Kontrolle über den Bot geben, und ein
#: versehentlich entfernter könnte den letzten Admin aussperren. Diese
#: Entscheidung bleibt Handarbeit in der Benutzerverwaltung.
NUR_VON_HAND = frozenset({"is_admin"})


@dataclass
class Rechtemassnahme:
    """Was sich an den Backend-Rechten einer Person ändern müsste."""

    vergeben: list[str] = field(default_factory=list)
    entziehen: list[str] = field(default_factory=list)

    ausnahmen_aufheben: list[str] = field(default_factory=list)
    """Bewusst entzogene Rechte, deren Rolle die Person gar nicht mehr hat.

    Sie werden weggeräumt, weil sie nichts mehr bedeuten: Ohne die Rolle gäbe
    es das Recht ohnehin nicht. Bliebe die Ausnahme stehen, würde eine
    Entscheidung von damals die Person auch dann noch einschränken, wenn sie
    die Rolle irgendwann neu bekommt — ohne dass irgendwo steht warum.
    """

    @property
    def leer(self) -> bool:
        return not (self.vergeben or self.entziehen or self.ausnahmen_aufheben)


def abgeleitete_rechte(rollen: set[str], rollenrechte: dict[str, set[str]]) -> set[str]:
    """Alle Rechte, die sich aus diesen Rollen zusammen ergeben.

    Mehrere Rollen addieren sich: Wer Manager UND Moderator ist, hat die
    Vereinigung beider. Ein Recht ist damit abgedeckt, sobald EINE Rolle es
    mitbringt — sonst würde eine zweite Rolle ein Recht wieder streitig machen,
    das die erste gerade vergeben hat.
    """
    ergebnis: set[str] = set()
    for rolle in rollen:
        ergebnis |= set(rollenrechte.get(rolle, ()))
    return ergebnis - NUR_VON_HAND


def rollenverwaltete_rechte(rollenrechte: dict[str, set[str]]) -> set[str]:
    """Alle Rechte, die überhaupt an einer Rolle hängen.

    Die Trennlinie der ganzen Mechanik. Ein Recht, das in KEINER Zuordnung
    vorkommt, ist reine Handarbeit und geht den Abgleich nichts an - es wird
    weder vergeben noch entzogen. Ein Recht, das an einer Rolle hängt, gilt
    dagegen nur so lange, wie eine passende Rolle es trägt.
    """
    ergebnis: set[str] = set()
    for rechte in rollenrechte.values():
        ergebnis |= set(rechte)
    return ergebnis - NUR_VON_HAND


def massnahmen(
    *,
    rollen: set[str],
    rollenrechte: dict[str, set[str]],
    eigene_rechte: set[str],
    geschuetzt: bool = False,
    spiegelrollen: set[str] | frozenset = frozenset(),
    zuordnung: dict[str, str] | None = None,
    handverliehen: set[str] | frozenset = frozenset(),
    ausnahmen: set[str] | frozenset = frozenset(),
) -> Rechtemassnahme:
    """Was am Backend zu tun wäre, damit es zu den Rollen passt.

    **Ein Recht, das an einer Rolle hängt, gilt nur mit dieser Rolle.**
    Verliert jemand die Moderator-Rolle, verliert er die Moderationsrechte —
    unabhängig davon, wer sie seinerzeit eingetragen hat. Patrick am
    05.09.2026: „die rechte die er nur als moderator hat müssen entzogen
    werden."

    Bis dahin galt die vorsichtigere Regel „der Bot nimmt nur weg, was er
    selbst gegeben hat". Sie war für den ERSTEN Durchlauf richtig — ohne sie
    hätte er jedem seine von Hand vergebenen Rechte genommen, bevor überhaupt
    jemand nachsehen konnte, ob das passt. Nachdem dieser Durchlauf einmal
    sauber gelaufen ist, steht sie der Absicht im Weg: Wer eine Rolle verliert,
    behielt die zugehörigen Rechte, wenn sie ein Mensch eingetragen hatte.

    Was die Regel NICHT tut, und das ist der Unterschied zwischen streng und
    unbrauchbar: Rechte, die in keiner einzigen Rollenzuordnung vorkommen,
    bleiben unangetastet. Ein Admin kann also weiterhin jemandem gezielt ein
    Recht geben, ohne ihm eine Rolle zu verleihen — es darf nur kein Recht
    sein, das ohnehin schon einer Rolle zugeordnet ist. Sonst wäre nicht
    entscheidbar, ob es zur Rolle gehört oder daneben steht.

    Zwei Sicherungen bleiben unverändert:

    **`is_admin` läuft nie über eine Rolle** (siehe NUR_VON_HAND).

    **Der Besitzer wird nie beschnitten** — er soll sich nicht durch eine
    Rollenänderung aus seinem eigenen Backend aussperren können.

    **Eine Rolle, die der Bot wegen eines Rechts vergeben hat, zählt nicht als
    Quelle** (`spiegelrollen`). Ohne diese Regel verstärkt sich jede Vergabe:
    Patrick gab am 05.09.2026 einem Mitglied nur `manage_badword_filter`; das
    Recht brachte über `permission_roles` die Moderator-Rolle, und aus dieser
    frisch vergebenen Rolle leitete diese Funktion drei Sekunden später alle
    fünf Moderationsrechte ab. Sein Urteil: „im bot wurden ihm alle rechte
    zugewiesen anstatt nur der schimpfwortfilter. das sollte nicht so sein."
    Eine solche Rolle ist eine Anzeige des Rechts, nicht seine Ursache.

    Damit dabei nicht das auslösende Recht selbst mitgerissen wird, ist
    geschützt, was ein Mensch vergeben hat (`handverliehen`) UND dessen
    Spiegelrolle die Person gerade trägt. Sonst nähme der Abgleich
    `manage_badword_filter` gleich wieder weg, weil keine „echte" Rolle es mehr
    deckt — übrig bliebe die Rolle ohne jedes Recht.

    Der Schutz endet mit der Rolle. Wird sie in Discord entzogen, ist sie keine
    Spiegelrolle mehr, sondern gar keine — und die erste Regel greift wieder:
    Rechte, die an ihr hingen, gehen mit.

    **Die Rolle liefert eine Grundausstattung, keine Zwangsjacke.** Wer auf dem
    Server zum Moderator gemacht wird, bekommt zunächst alles, was für die Rolle
    eingestellt ist. Davon lässt sich pro Person abweichen: `ausnahmen` sind
    Rechte, die ein Mensch bewusst entzogen hat, und sie werden nicht wieder
    nachgezogen. Patrick am 05.09.2026: „wenn man auf dem server zum Moderator
    gemacht wird hat man erstmal alle Moderatoren rechte die über die
    rechteverwaltung eingestellt wurden aber man kann auch einzelne rechte
    vergeben und entziehen."
    """
    echte_rollen = set(rollen) - set(spiegelrollen)
    zustehend = abgeleitete_rechte(echte_rollen, rollenrechte)

    # Rechte, die ein Mensch eingetragen hat und deren zugeordnete Rolle die
    # Person gerade traegt. Sie sind der Grund, warum die Rolle da ist - egal
    # ob der Bot sie gesetzt oder jemand sie von Hand vergeben hat.
    #
    # Dass hier NICHT nur Spiegelrollen zaehlen, ist Absicht: Seit dem
    # 05.09.2026 bekommt jeder Moderator seine Rechte einzeln zugewiesen
    # ("es gibt moderatoren die sich um den schimpfwortfilter kuemmern oder
    # welche die kicken koennen.. manche koennen beides andere moderatoren
    # koennen was anderes"). Wuerde der Schutz eine vom Bot gesetzte Rolle
    # verlangen, verloere jeder seine Einzelrechte, der die Rolle schon vorher
    # von Hand trug.
    ausloeser = {
        recht for recht in handverliehen
        if (zuordnung or {}).get(recht) in set(rollen)
    }

    massnahme = Rechtemassnahme()
    # Bewusst entzogene Rechte werden nicht nachgezogen. Ohne das waere jeder
    # gezielte Entzug beim naechsten Abgleich rueckgaengig gemacht, und die
    # Grundausstattung einer Rolle liesse sich fuer niemanden anpassen -
    # Patrick, 05.09.2026: "man kann auch einzelne rechte vergeben und
    # entziehen".
    massnahme.vergeben = sorted(zustehend - eigene_rechte - set(ausnahmen))
    massnahme.ausnahmen_aufheben = sorted(set(ausnahmen) - zustehend)
    if not geschuetzt:
        massnahme.entziehen = sorted(
            (eigene_rechte & rollenverwaltete_rechte(rollenrechte))
            - zustehend - ausloeser - NUR_VON_HAND
        )
    return massnahme


def begruendung(massnahme: Rechtemassnahme, rollennamen: dict[str, str] | None = None) -> str:
    """Ein Satz fürs Audit-Log und für die Übersicht.

    Wichtig, dass automatische Änderungen nachvollziehbar bleiben: Wer später
    fragt, warum jemand ein Recht verloren hat, soll die Antwort im Log finden
    und nicht raten müssen.
    """
    if massnahme.leer:
        return "keine Änderung nötig"
    teile = []
    if massnahme.vergeben:
        teile.append("erhält: " + ", ".join(massnahme.vergeben))
    if massnahme.entziehen:
        teile.append("verliert: " + ", ".join(massnahme.entziehen))
    return " · ".join(teile)
