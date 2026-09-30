"""Ce qui est nouveau dans une nouvelle version d'un cours.

Quand l'étudiant dépose une version à jour d'un fichier, Pirouette garde le texte de l'ancienne (<id>.prev.txt) et
compare les deux phrase par phrase : les phrases qui n'existaient pas forment les « passages nouveaux », rangés par
chapitre. L'IA ne travaille ensuite que sur eux, pour ajouter des questions et des cartes sans refaire le reste.
"""

from __future__ import annotations

import re

from .chapters import chapter_text

SPLIT = re.compile(r"(?<=[.!?…:;])\s+|\n+")
MARKER = re.compile(r"^\[(Page|Diapo) \d+\]$")
MIN_SENTENCE = 25   # caractères utiles : en dessous (titres courts, numéros), une phrase ne compte pas
MIN_NEWS = 150      # un chapitre « a du nouveau » à partir de 150 caractères nouveaux


def _key(sentence: str) -> str:
    return " ".join(re.findall(r"\w+", sentence.lower()))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SPLIT.split(text) if s.strip() and not MARKER.match(s.strip())]


def new_text(old: str, new: str) -> str:
    """Les phrases de `new` absentes de `old`, dans l'ordre ; les phrases qui se suivent restent groupées."""
    known = {_key(s) for s in _sentences(old)}
    passages: list[list[str]] = []
    previous_new = False
    for sentence in _sentences(new):
        key = _key(sentence)
        fresh = len(key) >= MIN_SENTENCE and key not in known
        if fresh:
            if not previous_new:
                passages.append([])
            passages[-1].append(sentence)
        previous_new = fresh
    return "\n\n".join(" ".join(p) for p in passages)


def news_by_chapter(old: str, new: str, chapters: list[dict], file_id: str) -> list[dict]:
    """[{"key", "position", "text", "chars"}] : les chapitres (ou le fichier entier) qui ont du nouveau."""
    units = [(f"{file_id}-{i}", i, chapter_text(new, chapters, i)) for i in range(len(chapters))] if chapters \
        else [(file_id, None, new)]
    found = []
    for key, position, text in units:
        fresh = new_text(old, text)
        if len(fresh) >= MIN_NEWS:
            found.append({"key": key, "position": position, "text": fresh, "chars": len(fresh)})
    return found
