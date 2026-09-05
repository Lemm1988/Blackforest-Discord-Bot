"""Zeigt, auf welchen Wegen eine Rolle vergeben wird — und wo sich die Wege widersprechen.

Der Bot verteilt Rollen und Rechte an fünf getrennten Stellen, die bis heute
nichts voneinander wissen:

    1. Benutzerverwaltung    user_permissions        Recht direkt an eine Person
    2. Rollen-Zuordnung      permission_roles        Recht  ->  Rolle
    3. Rollen-Zuordnung      role_permissions        Rolle  ->  Recht
    4. Reaktionsrollen       reaction_roles          Reaktion auf Nachricht -> Rolle
    5. Verdiente Rollen      earned_roles            Leistung -> Rolle

Jede Stelle für sich ist verständlich. Zusammen ergeben sie einen Zustand, den
niemand mehr überblickt: Weg 2 und Weg 3 zeigen in entgegengesetzte Richtungen
und können auseinanderlaufen, und die Wege 4 und 5 vergeben Rollen, die über
Weg 3 wiederum Backend-Rechte mitbringen können. Patrick am 04.09.2026: „ich
kann an mehreren stellen im bot rechte und rollendinge einstellen die irgendwie
unabhängig voneinander laufen. […] rolle ohne passende berechtigung soll nicht
passieren."

Dieses Modul rechnet nur. Es liest keine Datenbank, kennt kein discord.py und
schreibt nirgends etwas — es bekommt den Zustand übergeben und gibt zurück, was
daran auffällt. Deshalb ist es ohne Server und ohne Discord-Verbindung prüfbar,
und deshalb kann eine Warnung hier nie versehentlich etwas verändern.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rechteabgleich import NUR_VON_HAND

#: Die vier Wege, die eine ROLLE vergeben oder ihr Bedeutung geben.
#:
#: Weg 1 (Recht direkt an eine Person) fehlt hier bewusst: Er vergibt keine
#: Rolle und taucht deshalb in einer Übersicht je Rolle nicht auf. Für ihn gibt
#: es `handverliehene_rechte()` weiter unten.
RECHT_ZU_ROLLE = "recht_zu_rolle"
ROLLE_ZU_RECHTEN = "rolle_zu_rechten"
REAKTION = "reaktion"
VERDIENT = "verdient"

WEG_LABELS = {
    RECHT_ZU_ROLLE: "Recht bringt die Rolle",
    ROLLE_ZU_RECHTEN: "Rolle bringt Rechte",
    REAKTION: "Reaktion",
    VERDIENT: "Verdient",
}

#: Nur diese drei Wege vergeben tatsächlich eine Rolle. `ROLLE_ZU_RECHTEN`
#: leitet umgekehrt Rechte aus einer bereits vorhandenen Rolle ab und ist
#: deshalb kein Vergabeweg — sonst würde jede Rolle mit Rechten fälschlich als
#: „mehrfach vergeben" gemeldet.
VERGEBENDE_WEGE = (RECHT_ZU_ROLLE, REAKTION, VERDIENT)

HOCH = "hoch"
MITTEL = "mittel"
HINWEIS = "hinweis"

#: Reihenfolge für die Anzeige. Was den Betrieb bricht, steht oben.
_RANG = {HOCH: 0, MITTEL: 1, HINWEIS: 2}


@dataclass(frozen=True)
class Weg:
    """Ein einzelner Eintrag, der eine Rolle betrifft."""

    art: str
    detail: str

    @property
    def label(self) -> str:
        return WEG_LABELS.get(self.art, self.art)


@dataclass
class Rollenzeile:
    """Alles, was über eine Rolle eingestellt ist, in einer Zeile."""

    rolle_id: str
    name: str
    verwaltbar: bool = True
    vorhanden: bool = True
    wege: list[Weg] = field(default_factory=list)
    rechte: list[str] = field(default_factory=list)

    @property
    def vergebende_wege(self) -> list[Weg]:
        return [w for w in self.wege if w.art in VERGEBENDE_WEGE]


@dataclass(frozen=True)
class Befund:
    """Ein Widerspruch zwischen zwei Wegen, in verständlicher Form."""

    art: str
    schwere: str
    rolle_id: str
    titel: str
    text: str
    empfehlung: str


def _z(text) -> str:
    """Setzt einen Wert in deutsche Anfuehrungszeichen.

    Nur damit die beiden Zeichen nicht in jedem f-String einzeln getippt
    werden muessen - das oeffnende und das schliessende sehen sich aehnlich
    genug, dass ein ASCII-Zeichen dazwischen sofort den String beendet.
    """
    return "\u201e" + str(text) + "\u201c"


def _rollenverwaltete_rechte(rolle_zu_rechten) -> set[str]:
    """Jedes Recht, das irgendeine Rolle mitbringt.

    Genau diese Menge nimmt `rechteabgleich` jemandem wieder weg, wenn keine
    seiner Rollen sie deckt. Ein Recht, das hier nicht vorkommt, überlebt jede
    Rollenänderung — das ist der Unterschied zwischen einer harmlosen
    Unstimmigkeit und einer, die im Betrieb wirklich zuschlägt.
    """
    ergebnis: set[str] = set()
    for rechte in rolle_zu_rechten.values():
        ergebnis |= set(rechte)
    return ergebnis - NUR_VON_HAND


def uebersicht(
    *,
    rollen,
    recht_zu_rolle,
    rolle_zu_rechten,
    reaktionsrollen=(),
    verdiente_rollen=(),
) -> list[Rollenzeile]:
    """Eine Zeile je Rolle, die überhaupt in einer der Einstellungen vorkommt.

    Rollen ohne jeden Eintrag bleiben weg. Der Server hat über dreißig davon,
    und eine Übersicht, in der dreißig leere Zeilen die fünf interessanten
    verstecken, hätte ihren Zweck verfehlt.
    """
    bekannt = {str(r["id"]): r for r in rollen}
    zeilen: dict[str, Rollenzeile] = {}

    def zeile(rolle_id: str) -> Rollenzeile:
        rolle_id = str(rolle_id)
        if rolle_id not in zeilen:
            rolle = bekannt.get(rolle_id)
            zeilen[rolle_id] = Rollenzeile(
                rolle_id=rolle_id,
                name=rolle["name"] if rolle else f"Rolle {rolle_id}",
                verwaltbar=bool(rolle["verwaltbar"]) if rolle else False,
                vorhanden=rolle is not None,
            )
        return zeilen[rolle_id]

    for recht, rolle_id in sorted(recht_zu_rolle.items()):
        if rolle_id:
            zeile(rolle_id).wege.append(Weg(RECHT_ZU_ROLLE, recht))

    for rolle_id, rechte in rolle_zu_rechten.items():
        z = zeile(rolle_id)
        z.rechte = sorted(rechte)
        if z.rechte:
            z.wege.append(Weg(ROLLE_ZU_RECHTEN, f"{len(z.rechte)} Rechte"))

    for regel in reaktionsrollen:
        emoji = regel.get("emoji") or "?"
        aktiv = "" if regel.get("enabled", 1) else " (abgeschaltet)"
        zeile(regel["role_id"]).wege.append(Weg(REAKTION, f"{emoji}{aktiv}"))

    for regel in verdiente_rollen:
        art = regel.get("bedingung_art", "?")
        schwelle = regel.get("schwelle", "?")
        wie = "automatisch" if regel.get("automatisch") else "auf Abruf"
        zeile(regel["role_id"]).wege.append(Weg(VERDIENT, f"{art} ab {schwelle}, {wie}"))

    return sorted(zeilen.values(), key=lambda z: (not z.vorhanden, z.name.lower()))


def befunde(
    *,
    rollen,
    recht_zu_rolle,
    rolle_zu_rechten,
    reaktionsrollen=(),
    verdiente_rollen=(),
) -> list[Befund]:
    """Alle Widersprüche zwischen den Wegen, das Schlimmste zuerst."""
    zeilen = uebersicht(
        rollen=rollen,
        recht_zu_rolle=recht_zu_rolle,
        rolle_zu_rechten=rolle_zu_rechten,
        reaktionsrollen=reaktionsrollen,
        verdiente_rollen=verdiente_rollen,
    )
    nach_id = {z.rolle_id: z for z in zeilen}
    rollenverwaltet = _rollenverwaltete_rechte(rolle_zu_rechten)
    gefunden: list[Befund] = []

    # 1. Der Rückweg fehlt: Ein Recht holt eine Rolle, die dieses Recht nicht
    #    mitbringt. Das ist kein Schönheitsfehler, sondern ein Kreislauf. Wer
    #    das Recht von Hand vergibt, bekommt die Rolle; beim nächsten Abgleich
    #    sieht der Bot eine Rolle, die dieses Recht nicht deckt, und nimmt es
    #    wieder weg. Übrig bleibt die Rolle ohne das Recht, das sie ausgelöst
    #    hat - genau der Zustand, den Patrick ausschliessen wollte.
    for recht, rolle_id in sorted(recht_zu_rolle.items()):
        if not rolle_id or recht in NUR_VON_HAND:
            continue
        rolle_id = str(rolle_id)
        if recht in set(rolle_zu_rechten.get(rolle_id, ())):
            continue
        z = nach_id.get(rolle_id)
        name = z.name if z else f"Rolle {rolle_id}"
        wird_entzogen = recht in rollenverwaltet
        gefunden.append(Befund(
            art="rueckweg_fehlt",
            schwere=HOCH if wird_entzogen else MITTEL,
            rolle_id=rolle_id,
            titel=f"{_z(recht)} holt {name}, aber {name} bringt {_z(recht)} nicht mit",
            text=(
                f"Wer {_z(recht)} bekommt, erhält dadurch die Rolle {name}. Die Rolle "
                f"selbst gibt dieses Recht aber nicht zurück."
                + (
                    f" Da {_z(recht)} an anderer Stelle an eine Rolle gebunden ist, nimmt "
                    "der Rechteabgleich es beim nächsten Lauf wieder weg — die Rolle "
                    "bleibt, das Recht ist weg."
                    if wird_entzogen else
                    " Das Recht bleibt bestehen, die beiden Karten widersprechen sich aber."
                )
            ),
            empfehlung=(
                f"Unter {_z('Rolle bringt Berechtigung mit')} bei {name} das Recht "
                f"{_z(recht)} anhaken — oder die Zuordnung {_z(recht + ' → ' + name)} "
                f"entfernen, falls sie "
                "so nicht gemeint war."
            ),
        ))

    # 2. is_admin über eine Rolle. Das Speichern verhindert es heute, aber ein
    #    Altbestand aus der Zeit davor würde stumm weiterwirken.
    for rolle_id, rechte in sorted(rolle_zu_rechten.items()):
        verbotene = sorted(set(rechte) & NUR_VON_HAND)
        if not verbotene:
            continue
        z = nach_id.get(str(rolle_id))
        name = z.name if z else f"Rolle {rolle_id}"
        gefunden.append(Befund(
            art="admin_ueber_rolle",
            schwere=HOCH,
            rolle_id=str(rolle_id),
            titel=f"{name} bringt {', '.join(verbotene)} mit",
            text=(
                "Wer diese Rolle auf dem Server vergeben darf, vergibt damit volle "
                "Backend-Rechte. Das ist als Weg nicht vorgesehen."
            ),
            empfehlung="Das Recht bei der Rolle abwählen und einzeln in der Benutzerverwaltung vergeben.",
        ))

    for z in zeilen:
        # 3. Eingetragen, aber die Rolle gibt es auf dem Server nicht mehr.
        if not z.vorhanden:
            gefunden.append(Befund(
                art="verwaist", schwere=MITTEL, rolle_id=z.rolle_id,
                titel=f"Rolle {z.rolle_id} gibt es auf dem Server nicht mehr",
                text=(
                    "Sie ist noch in "
                    + ", ".join(sorted({w.label for w in z.wege}))
                    + " eingetragen. Diese Einträge greifen nie."
                ),
                empfehlung="Eintrag entfernen oder auf eine vorhandene Rolle umstellen.",
            ))
            continue

        # 4. Der Bot kommt an die Rolle nicht heran. Discord erlaubt nur Rollen
        #    unterhalb der eigenen - eine Zuordnung darüber greift stillschweigend nie.
        if z.vergebende_wege and not z.verwaltbar:
            gefunden.append(Befund(
                art="unerreichbar", schwere=HOCH, rolle_id=z.rolle_id,
                titel=f"{z.name} liegt über der Rolle des Bots",
                text=(
                    "Discord lässt einen Bot nur Rollen unterhalb seiner eigenen vergeben. "
                    "Diese Zuordnung ist eingestellt, greift aber nie, und es gibt keine "
                    "Fehlermeldung dazu."
                ),
                empfehlung=(
                    "In den Discord-Servereinstellungen die Bot-Rolle über "
                    f"{z.name} ziehen — oder eine andere Rolle zuordnen."
                ),
            ))

        # 5. Mehrere Wege vergeben dieselbe Rolle. Sie wissen nichts voneinander
        #    und können sich gegenseitig überschreiben.
        arten = {w.art for w in z.vergebende_wege}
        if len(arten) > 1:
            gefunden.append(Befund(
                art="doppelter_weg", schwere=MITTEL, rolle_id=z.rolle_id,
                titel=f"{z.name} wird auf {len(arten)} Wegen vergeben",
                text=(
                    "Beteiligt sind: "
                    + ", ".join(WEG_LABELS[a] for a in sorted(arten))
                    + ". Die Wege wissen nichts voneinander — nimmt einer die Rolle weg, "
                    "kann der andere sie unmittelbar danach wieder vergeben."
                ),
                empfehlung="Einen der Wege entfernen, damit die Rolle eine eindeutige Herkunft hat.",
            ))

        # 6. Eine automatisch vergebene Rolle bringt Backend-Rechte mit. Oft
        #    genau so gewollt (Ideenschmied darf Fragen einreichen) - aber es
        #    heisst, dass eine Leistung im Spiel ein Recht im Backend auslöst.
        #    Deshalb Hinweis, nicht Warnung.
        automatisch = {w.art for w in z.wege} & {REAKTION, VERDIENT}
        if automatisch and z.rechte:
            ausloeser = " und ".join(WEG_LABELS[a] for a in sorted(automatisch))
            gefunden.append(Befund(
                art="kette", schwere=HINWEIS, rolle_id=z.rolle_id,
                titel=f"{z.name} wird automatisch vergeben und bringt Rechte mit",
                text=(
                    f"Über {ausloeser} bekommt man {z.name}, und damit im Backend: "
                    + ", ".join(z.rechte)
                    + ". Wer die Bedingung erfüllt, erhält diese Rechte ohne weiteres Zutun."
                ),
                empfehlung="Nichts zu tun, wenn das so gewollt ist — sonst die Rechte der Rolle abwählen.",
            ))

    return sorted(gefunden, key=lambda b: (_RANG.get(b.schwere, 9), b.titel))


def handverliehene_rechte(*, eigene_rechte, rolle_zu_rechten) -> list[str]:
    """Rechte, die eine Person hat, die aber an keiner Rolle hängen (Weg 1).

    Diese überleben jede Rollenänderung — bewusst, sonst könnte ein Admin
    niemandem mehr gezielt etwas geben, ohne eine Rolle zu verleihen. Sichtbar
    sein sollten sie trotzdem: Sie sind der einzige Weg, auf dem jemand ein
    Recht behält, obwohl seine Rollen es nicht hergeben.
    """
    return sorted(set(eigene_rechte) - _rollenverwaltete_rechte(rolle_zu_rechten) - NUR_VON_HAND)
