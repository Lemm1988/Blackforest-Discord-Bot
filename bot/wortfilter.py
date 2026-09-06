"""Erkennt Schimpfwörter in einer Nachricht — auch verschleiert geschriebene.

Bis zum 06.09.2026 stand das mitten in `cogs/moderation.py`, zwischen dem
Löschen der Nachricht und dem Timeout. Prüfbar war es nur mit laufendem Bot,
und das Backend kam gar nicht heran.

Das war der Grund, warum es die **Testbox** nicht geben konnte: „Text eingeben,
sehen ob der Filter anschlägt und warum" braucht genau diese Entscheidung —
aber ohne dabei etwas zu löschen. Genau wie bei `linkfilter.py` steht hier
deshalb nur das Urteil: kein discord.py, keine Datenbank, kein Schreibzugriff.

Drei Durchläufe, in dieser Reihenfolge:

1. **Wortgrenzen-Abgleich** auf der normalisierten Nachricht. Erkennt exakte
   Treffer, Leetspeak und Homoglyphen, ohne bei einem Schimpfwort als Teil
   eines harmlosen längeren Wortes anzuschlagen — `ass` in `Klasse` schlägt
   nicht an, und `mist` in `Pferdemist` ebensowenig.
2. **Auseinandergezogene Schreibweise** (`s c h e i s s e`).
3. **Teil-verschleierte einzelne Wörter** (`sch.eiss`), nur wenn die
   aggressive Normalisierung eingeschaltet ist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "$": "s", "!": "i",
})

# Unicode-Homoglyphen (Look-alikes aus anderen Schriftsystemen, in der Praxis
# haeufig zur Filterumgehung genutzt, z.B. kyrillisches "а" statt lateinischem
# "a" - sieht optisch identisch aus, ist aber ein anderes Zeichen) auf
# lateinische Buchstaben abgebildet. Bewusst KEINE vollstaendige Unicode-
# Confusables-Datenbank (waere Overkill fuer den Zweck) - nur die in der
# Praxis haeufigsten kyrillischen/griechischen Look-alikes. Schluessel sind
# bereits kleingeschrieben, da normalize() zuerst .lower() aufruft.
HOMOGLYPH_MAP = {
    # Kyrillisch
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ѕ": "s", "ј": "j", "һ": "h", "ԁ": "d", "ѡ": "w", "к": "k",
    "м": "m", "т": "t", "в": "b",
    # Griechisch
    "α": "a", "ο": "o", "ρ": "p", "υ": "u", "ν": "v", "κ": "k", "ε": "e",
}
HOMOGLYPH_TRANSLATE = str.maketrans(HOMOGLYPH_MAP)

# Eigene Wortzeichen-Klasse statt \b, damit deutsche Umlaute/ß zuverlässig als
# Teil eines Wortes erkannt werden (nicht von der Interpretation der jeweiligen
# Python-Version/Locale abhängig).
_WORDCHARS = "a-zA-Z0-9äöüßÄÖÜ_"

# 3 oder mehr direkt wiederholte Zeichen auf 1 reduzieren (z.B. "scheeeisse" -> "scheisse").
_REPEAT_RE = re.compile(r"(.)\1{2,}")

# Sequenzen aus einzelnen Buchstaben, jeweils durch 1-3 Trennzeichen separiert
# (z.B. "s c h e i s s e" oder "s.c.h.e.i.s.s.e") - erkennt absichtlich
# auseinandergezogene Schreibweisen, ohne normale mehrbuchstabige Wörter zu treffen.
_SPACED_RUN_RE = re.compile(r"(?:[a-zäöüß][^a-zäöüß0-9]{1,3}){2,}[a-zäöüß]")
_NON_LETTER_RE = re.compile(r"[^a-zäöüß]")

# Fuer die Pro-Token-Teil-Obfuskations-Erkennung (z.B. "sch.eiss", "a-rsch"):
# entfernt fuehrende/nachfolgende Satzzeichen (normale Interpunktion am
# Wortrand, z.B. "Haus!" oder "z.B."), OHNE innenliegende Trennzeichen
# anzufassen - genau die bleiben als Erkennungsmerkmal fuer absichtliche
# Verschleierung uebrig (siehe find_matched_word() Durchlauf 3).
_TOKEN_EDGE_STRIP_RE = re.compile(r"^[^a-zäöüß]+|[^a-zäöüß]+$")

def normalize(text: str, aggressive: bool = True) -> str:
    """Kleinschreibung, Leetspeak aufgelöst, wiederholte Zeichen reduziert.

    `aggressive` steuert zusätzlich die Homoglyphen-Auflösung (Cyrillic/
    Griechisch -> Latein) - siehe badword_aggressive_normalization_enabled.
    Leetspeak/Wiederholungs-Reduktion liefen schon vor der Modernisierung
    immer, bleiben also unabhängig vom neuen Schalter aktiv (keine Verhaltens-
    änderung für bestehende, bereits als sicher erprobte Erkennung)."""
    text = text.lower().translate(LEET_MAP)
    if aggressive:
        text = text.translate(HOMOGLYPH_TRANSLATE)
    return _REPEAT_RE.sub(r"\1", text)


def _find_obfuscated_token_match(normalized: str, badwords):
    """Durchlauf 3 (neu): einzelne, mit Sonderzeichen DURCHSETZTE Wörter
    erkennen (z.B. "sch.eiss", "a-rsch-loch"), ohne über echte Wortgrenzen
    (Leerzeichen) hinweg zu vergleichen - jedes Whitespace-getrennte Token
    wird für sich betrachtet, verschiedene Wörter können sich dadurch nie
    versehentlich zu einem Treffer verbinden.

    Nur Tokens mit INNENLIEGENDEN Trennzeichen werden über den (riskanteren,
    da nicht wortgrenzen-geschützten) Teilstring-Vergleich geprüft - normale
    Interpunktion am Wortrand (z.B. "Haus!", "z.B.") wird vorher abgeschnitten
    und macht ein Token dadurch NICHT zum Kandidaten. Ein Token ganz ohne
    innenliegende Trennzeichen (das normale Wort "klasse") wird hier komplett
    übersprungen - das ist bereits durch den wortgrenzen-sicheren Durchlauf 1
    abgedeckt (verhindert das klassische "ass in klasse"-Fehlalarm-Problem)."""
    for token in normalized.split():
        core = _TOKEN_EDGE_STRIP_RE.sub("", token)
        if not core:
            continue
        stripped = _NON_LETTER_RE.sub("", core)
        if stripped == core:
            continue  # keine innenliegenden Trennzeichen - normales Wort
        for word in badwords:
            if word and word in stripped:
                return word
    return None


def find_matched_word(content: str, badwords, aggressive: bool = True):
    """Prüft eine Nachricht gegen die Schimpfwortliste.

    Gibt das erste erkannte Wort zurück, oder None. Drei Durchläufe:

    1. Wortgrenzen-Abgleich auf der normalisierten Nachricht: erkennt exakte
       Treffer, Leetspeak-/Homoglyphen-Varianten und wiederholte Buchstaben,
       OHNE bei einem Schimpfwort als Teilstring eines harmlosen, längeren
       Wortes anzuschlagen (z.B. schlägt "ass" in "Klasse" NICHT an, da "ass"
       dort keine eigene Worteinheit ist).
    2. Auseinandergezogene Schreibweise ("s c h e i s s e"): sucht gezielt nach
       Sequenzen einzelner, durch Trennzeichen separierter Buchstaben (die in
       normaler Sprache praktisch nie vorkommen) und prüft die zusammengesetzte
       Version gegen die Wortliste.
    3. (Nur bei aggressive=True) Teil-obfuskierte einzelne Wörter
       ("sch.eiss") - siehe _find_obfuscated_token_match().
    """
    if not badwords:
        return None
    normalized = normalize(content, aggressive=aggressive)

    for word in badwords:
        if not word:
            continue
        pattern = rf"(?<![{_WORDCHARS}]){re.escape(word)}(?![{_WORDCHARS}])"
        if re.search(pattern, normalized):
            return word

    for match in _SPACED_RUN_RE.finditer(normalized):
        collapsed = _NON_LETTER_RE.sub("", match.group(0))
        for word in badwords:
            if word and word in collapsed:
                return word

    if aggressive:
        obfuscated = _find_obfuscated_token_match(normalized, badwords)
        if obfuscated:
            return obfuscated

    return None


# --------------------------------------------------------------------------
# Wortstufen und Ausnahmen (06.09.2026)
#
# Patrick am 05.09.2026 auf die Frage, ob Stufen den Pflegeaufwand wert sind:
# ja, mit je eigener Reaktion. Bis dahin war jedes Wort gleich schlimm - ein
# derber Fluch loeste denselben Timeout aus wie eine Beleidigung, die einen
# Rauswurf rechtfertigt.
# --------------------------------------------------------------------------

LEICHT = "leicht"
MITTEL = "mittel"
SCHWER = "schwer"

#: Reihenfolge fuer die Anzeige und fuer den Vergleich "was wiegt schwerer".
STUFEN = (LEICHT, MITTEL, SCHWER)

STUFEN_LABELS = {
    LEICHT: "leicht",
    MITTEL: "mittel",
    SCHWER: "schwer",
}

#: Die Stufe eines Wortes, wenn keine eingetragen ist. Beim Umstieg bekamen
#: alle vorhandenen Woerter diese Stufe, und die Reaktion dafuer wurde auf den
#: bisherigen Wert gesetzt - damit sich das Verhalten nicht stillschweigend
#: aendert.
STANDARDSTUFE = MITTEL


@dataclass(frozen=True)
class Treffer:
    """Was der Filter in einer Nachricht gefunden hat."""

    wort: str = ""
    stufe: str = ""

    @property
    def getroffen(self) -> bool:
        return bool(self.wort)


def _ausnahme_spannen(normalisiert: str, ausnahmen) -> list:
    """Wo im Text stehen erlaubte Wendungen?

    Eine Ausnahme ist eine ganze Wendung, kein einzelnes Wort. Ein Treffer
    zaehlt nur dann nicht, wenn er **innerhalb** einer solchen Wendung liegt.
    Wuerde schon das blosse Vorkommen der Wendung irgendwo in der Nachricht
    genuegen, koennte man jede Beleidigung durch Anhaengen eines harmlosen
    Satzes durchschmuggeln.
    """
    spannen = []
    for wendung in ausnahmen or ():
        text = normalize(str(wendung or ""), aggressive=False).strip()
        if not text:
            continue
        start = 0
        while True:
            treffer = normalisiert.find(text, start)
            if treffer < 0:
                break
            spannen.append((treffer, treffer + len(text)))
            start = treffer + 1
    return spannen


def _liegt_in_ausnahme(spanne, ausnahme_spannen) -> bool:
    a, b = spanne
    return any(anfang <= a and b <= ende for anfang, ende in ausnahme_spannen)


def pruefe(
    content: str,
    woerter,
    *,
    aggressive: bool = True,
    ausnahmen=(),
) -> Treffer:
    """Wie find_matched_word(), aber mit Stufe und Ausnahmeliste.

    `woerter` ist eine Zuordnung {Wort: Stufe} oder eine Folge von Woertern
    (dann gilt für alle die Standardstufe).

    Die Ausnahmen greifen nur im ersten Durchlauf, dem Wortgrenzen-Abgleich.
    Wer "sch.eiss" schreibt, verschleiert absichtlich - eine erlaubte Wendung
    ist das dann nicht mehr, und die Ausnahme würde nur ein Schlupfloch
    aufmachen.
    """
    if not woerter:
        return Treffer()
    stufen = dict(woerter) if isinstance(woerter, dict) else {w: STANDARDSTUFE for w in woerter}

    normalisiert = normalize(content, aggressive=aggressive)
    erlaubte = _ausnahme_spannen(normalisiert, ausnahmen)

    for wort in stufen:
        if not wort:
            continue
        muster = rf"(?<![{_WORDCHARS}]){re.escape(wort)}(?![{_WORDCHARS}])"
        for fund in re.finditer(muster, normalisiert):
            if not _liegt_in_ausnahme(fund.span(), erlaubte):
                return Treffer(wort, stufen.get(wort) or STANDARDSTUFE)

    rest = find_matched_word(content, [w for w in stufen if w], aggressive=aggressive)
    if rest:
        return Treffer(rest, stufen.get(rest) or STANDARDSTUFE)
    return Treffer()
