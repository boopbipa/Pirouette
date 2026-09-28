"""Découpage d'un cours en chapitres, pour ne réviser qu'une partie du cours.

Deux étapes :
1. `detect_chapters` repère tout de suite les titres évidents (« Chapitre 2 », « II. », titres Word…) ;
2. l'IA affine ensuite : on lui envoie seulement les lignes qui ressemblent à des titres (numérotées),
   elle choisit celles qui ouvrent un chapitre (`CHAPTERS_SCHEMA`), et `chapters_from_ai` valide sa réponse.

Un chapitre est stocké comme {"title", "line", "chars"} : il commence à la ligne `line` du texte extrait
et va jusqu'au chapitre suivant.
"""

from __future__ import annotations

import re
from collections import Counter

MIN_CHAPTERS, MAX_CHAPTERS = 2, 60
INTRO_MIN_CHARS = 600      # texte avant le 1er chapitre : au-delà, il devient « Début du document »
MAX_TITLE = 120
MIN_CHAPTER_CHARS = 300    # taille médiane minimale des chapitres repérés sans IA
MAX_CANDIDATES = 300       # lignes envoyées à l'IA : tient dans la fenêtre d'un modèle local

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
WORD_NUMBERS = {"premier": 1, "première": 1, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
                "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}

MARKER = re.compile(r"^\[(Page|Diapo) \d+\]$")  # repères ajoutés à l'extraction des PDF / PowerPoint
TOC_LINE = re.compile(r"(\.{3,}|…+|_{3,}|\t|\s{3,})\s*\d+$")  # ligne de sommaire : « Titre ........ 12 »
KEYWORD = re.compile(
    r"^(chapitre|chap\.?|chapter|partie|part|leçon|lecon|lesson|module|séance|seance|séquence|sequence|"
    r"thème|theme|unité|unite|titre|livre|cours|tp|td)\s*(n°|no\.?)?\s*"
    r"([0-9]{1,3}|[IVXLC]{1,6}|premier|première|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\b",
    re.IGNORECASE)
ROMAN_HEADING = re.compile(r"^([IVXLC]{1,6})\s*[.)\-–—]\s+\S")
NUMBER_HEADING = re.compile(r"^([0-9]{1,2})\s*[.)\-–—]\s+\S")  # « 2. Titre » mais pas « 2.1 Titre »
MARKDOWN = re.compile(r"^(#{1,3})\s+(\S.*)$")


def _roman(value: str) -> int:
    total = 0
    for i, char in enumerate(value):
        number = ROMAN[char]
        total += -number if i + 1 < len(value) and ROMAN[value[i + 1]] > number else number
    return total


def _number(value: str) -> int:
    value = value.lower()
    if value.isdigit():
        return int(value)
    if value in WORD_NUMBERS:
        return WORD_NUMBERS[value]
    return _roman(value.upper())


def _clean_title(line: str) -> str:
    title = MARKDOWN.sub(r"\2", line).strip(" #:-–—*\t")
    return title[:MAX_TITLE].strip()


def _heading_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Lignes candidates : courtes, pas un repère de page, pas une ligne de sommaire, pas un en-tête répété."""
    counts = Counter(line.strip() for line in lines)
    result = []
    for index, raw in enumerate(lines):
        line = raw.strip().strip("*").strip()  # « ***Titre*** » : le gras / l'italique ne compte pas
        if not 2 <= len(line) <= MAX_TITLE or MARKER.match(line) or TOC_LINE.search(line):
            continue
        if counts[line] >= 3:  # en-tête ou pied de page répété sur chaque page
            continue
        if not re.search(r"[^\W\d_]", line):  # au moins une lettre
            continue
        result.append((index, line))
    return result


def _numbered_family(candidates, pattern, to_number) -> list[tuple[int, str]]:
    """Titres numérotés d'une même famille, dans l'ordre croissant (le sommaire, s'il est répété, est écarté)."""
    last: dict[int, tuple[int, str]] = {}
    for index, line in candidates:
        match = pattern.match(line)
        if match:
            last[to_number(match)] = (index, line)  # on garde la dernière occurrence : après le sommaire
    found = sorted(last.values())
    numbers = [to_number(pattern.match(line)) for _, line in found]
    return found if numbers == sorted(numbers) else []


def detect_chapters(text: str) -> list[dict]:
    """Repérage sans IA des chapitres évidents. Renvoie [] si le document n'en a pas d'évidents."""
    lines = text.split("\n")
    candidates = _heading_lines(lines)
    families = [
        _numbered_family(candidates, KEYWORD, lambda m: _number(m.group(3))),
        [(i, line) for i, line in candidates if (m := MARKDOWN.match(line)) and len(m.group(1)) == 1],
        _numbered_family(candidates, ROMAN_HEADING, lambda m: _roman(m.group(1))),
        [(i, line) for i, line in candidates if (m := MARKDOWN.match(line)) and len(m.group(1)) == 2],
        _numbered_family(candidates, NUMBER_HEADING, lambda m: int(m.group(1))),
    ]
    for family in families:
        if MIN_CHAPTERS <= len(family) <= MAX_CHAPTERS:
            chapters = build_chapters(lines, [(index, _clean_title(line)) for index, line in family])
            # Un vrai chapitre a du contenu ; des éléments de liste (« 1. Mélanger ») n'en ont presque pas.
            sizes = sorted(c["chars"] for c in chapters)
            if chapters and sizes[len(sizes) // 2] >= MIN_CHAPTER_CHARS:
                return chapters
    return []


def build_chapters(lines: list[str], starts: list[tuple[int, str]]) -> list[dict]:
    """Chapitres à partir des lignes de début : ajoute le texte d'introduction et la taille de chaque partie."""
    starts = sorted({index: title for index, title in starts if title}.items())
    if len(starts) < MIN_CHAPTERS:
        return []
    intro = "\n".join(lines[:starts[0][0]]).strip()
    intro = "\n".join(line for line in intro.split("\n") if not MARKER.match(line.strip())).strip()
    if len(intro) >= INTRO_MIN_CHARS:
        starts.insert(0, (0, "Début du document"))
    else:
        starts[0] = (0, starts[0][1])  # le petit texte d'avant (titre du cours…) rejoint le 1er chapitre
    chapters = []
    for position, (index, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        chapters.append({"title": title, "line": index, "chars": len("\n".join(lines[index:end]).strip())})
    return chapters


def chapter_text(text: str, chapters: list[dict], position: int) -> str:
    lines = text.split("\n")
    end = chapters[position + 1]["line"] if position + 1 < len(chapters) else len(lines)
    return "\n".join(lines[chapters[position]["line"]:end]).strip()


# ---------- Avec l'IA ----------

def ai_candidates(text: str) -> list[tuple[int, str]]:
    """Lignes proposées à l'IA. S'il y en a trop, on garde les plus « titre » en conservant l'ordre."""
    lines = text.split("\n")
    candidates = _heading_lines(lines)
    if len(candidates) <= MAX_CANDIDATES:
        return candidates

    def score(item: tuple[int, str]) -> int:
        index, line = item
        points = 0
        if KEYWORD.match(line) or MARKDOWN.match(line) or ROMAN_HEADING.match(line) or NUMBER_HEADING.match(line):
            points += 4
        if index > 0 and MARKER.match(lines[index - 1].strip()):
            points += 2  # titre de diapo, haut de page
        if line.isupper():
            points += 2
        if len(line) <= 60 and not line.endswith((".", ",", ";")):
            points += 1
        return points

    ranked = sorted(candidates, key=lambda item: -score(item))
    keep = {index for index, _ in ranked[:MAX_CANDIDATES]}
    return [item for item in candidates if item[0] in keep]


CHAPTERS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"line": {"type": "integer"}, "title": {"type": "string"}},
                "required": ["line", "title"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["chapters"],
    "additionalProperties": False,
}

CHAPTERS_SYSTEM = """Tu aides un étudiant à découper son cours en chapitres pour réviser partie par partie.
On te donne les lignes du cours qui ressemblent à des titres, chacune précédée de son numéro de ligne.
Repère les CHAPITRES : le plus haut niveau de titre qui découpe le cours en grandes parties (en général 2 à 20).
- Ignore le sommaire / la table des matières : prends la ligne où le chapitre commence vraiment (souvent la plus loin).
- Ignore les sous-parties, les titres de figures ou de tableaux, les en-têtes et pieds de page.
- Pour chaque chapitre, donne le numéro de ligne exact (parmi ceux fournis) et un titre court et clair,
  en gardant la numérotation du cours si elle existe (ex. « Chapitre 2 — La photosynthèse »).
- Si le cours n'a pas de vrais chapitres, renvoie une liste vide.
Réponds uniquement avec le JSON demandé."""


def build_chapters_prompt(candidates: list[tuple[int, str]], filename: str) -> str:
    listing = "\n".join(f"{index}: {line}" for index, line in candidates)
    return f"""Fichier : {filename}
Lignes qui ressemblent à des titres (numéro de ligne: texte) :
<titres>
{listing}
</titres>"""


def chapters_from_ai(text: str, answer: dict, candidates: list[tuple[int, str]]) -> list[dict]:
    """Valide la réponse de l'IA : lignes connues seulement. Renvoie [] si elle n'est pas exploitable."""
    known = {index: line for index, line in candidates}
    starts = []
    for item in (answer or {}).get("chapters") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        if index in known:
            title = _clean_title(str(item.get("title") or "")) or _clean_title(known[index])
            starts.append((index, title))
    if not MIN_CHAPTERS <= len(starts) <= MAX_CHAPTERS:
        return []
    return build_chapters(text.split("\n"), starts)
