"""Repérage des définitions d'un cours grâce à sa mise en forme.

À l'import, le gras et l'italique sont gardés en Markdown (« ***Terme*** » = gras + italique). L'étudiant choisit
(Réglages → Tes définitions) la règle qui correspond à sa façon de mettre en forme ses cours, par exemple :
un titre seul sur sa ligne, en gras et italique, suivi de sa définition. Les définitions repérées servent ensuite
de base aux questions de cours et aux flashcards.
"""

from __future__ import annotations

import re

from .chapters import KEYWORD

# Règles proposées, de la plus précise à la plus large.
RULES = [
    {"id": "gras_italique", "label": "Un titre seul sur sa ligne, en gras et italique, puis sa définition",
     "heading": re.compile(r"^\*\*\*(?P<term>[^*]+?)\*\*\*$")},
    {"id": "gras", "label": "Un titre seul sur sa ligne, en gras, puis sa définition",
     "heading": re.compile(r"^\*\*(?!\*)(?P<term>[^*]+?)\*\*$")},
    {"id": "italique", "label": "Un titre seul sur sa ligne, en italique, puis sa définition",
     "heading": re.compile(r"^\*(?!\*)(?P<term>[^*]+?)\*$")},
    {"id": "deux_points", "label": "« Terme : définition » en début de ligne",
     "inline": re.compile(r"^\**(?P<term>[^:*\n]{2,60}?)\**\s*:\s+(?P<definition>\S.{15,})$")},
]
RULE_IDS = {rule["id"] for rule in RULES}
MAX_TERM_WORDS = 10
MAX_DEFINITION_CHARS = 900
MARKER = re.compile(r"^\[(Page|Diapo) \d+\]$")


def plain(text: str) -> str:
    """Retire les marques de gras / italique."""
    return re.sub(r"\*{1,3}", "", text).strip()


def extract_definitions(text: str, rule_id: str) -> list[dict]:
    """Définitions {term, definition, line} trouvées avec la règle donnée."""
    rule = next((r for r in RULES if r["id"] == rule_id), None)
    if rule is None:
        return []
    lines = [line.strip() for line in text.split("\n")]
    found: list[dict] = []
    if "inline" in rule:
        for index, line in enumerate(lines):
            match = rule["inline"].match(line)
            if match and len(match["term"].split()) <= MAX_TERM_WORDS and not KEYWORD.match(plain(match["term"])):
                found.append({"term": plain(match["term"]), "definition": plain(match["definition"]), "line": index})
        return found

    def is_heading(line: str) -> bool:
        return bool(rule["heading"].match(line)) or line.startswith("#")

    for index, line in enumerate(lines):
        match = rule["heading"].match(line)
        if not match:
            continue
        term = match["term"].strip()
        if len(term.split()) > MAX_TERM_WORDS or KEYWORD.match(term):
            continue  # une phrase entière ou un titre de chapitre, pas un terme
        body: list[str] = []
        for following in lines[index + 1:]:
            if not following:
                if body:
                    break  # la définition s'arrête au premier paragraphe vide
                continue
            if is_heading(following) or MARKER.match(following):
                break
            body.append(plain(following))
        definition = " ".join(body)[:MAX_DEFINITION_CHARS].strip()
        if definition:  # un titre sans texte dessous : un titre de partie, pas une définition
            found.append({"term": term, "definition": definition, "line": index})
    return found


def analyse(text: str) -> dict:
    """Résultat de chaque règle sur un extrait de cours, et la règle qui en trouve le plus."""
    results = [{"id": r["id"], "label": r["label"], "definitions": extract_definitions(text, r["id"])} for r in RULES]
    best = max(results, key=lambda r: len(r["definitions"]))
    return {"rules": results, "best": best["id"] if best["definitions"] else None}


def definitions_for(text: str, rule_id: str | None) -> list[dict]:
    """Définitions d'un cours avec la règle choisie ; en automatique, la règle qui en trouve au moins 3."""
    if rule_id in RULE_IDS:
        return extract_definitions(text, rule_id)
    if rule_id == "aucune":
        return []
    result = analyse(text)
    best = next((r for r in result["rules"] if r["id"] == result["best"]), None)
    return best["definitions"] if best and len(best["definitions"]) >= 3 else []


def definitions_block(text: str, rule_id: str | None, limit: int = 40) -> str:
    """Liste des définitions à glisser dans la demande faite à l'IA (vide s'il n'y en a pas)."""
    definitions = definitions_for(text, rule_id)[:limit]
    if not definitions:
        return ""
    listing = "\n".join(f"- {d['term']} : {d['definition'][:300]}" for d in definitions)
    return f"\n<definitions>\n{listing}\n</definitions>"
