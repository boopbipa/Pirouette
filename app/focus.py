"""Quiz ciblés : sur un thème précis, ou à partir des questions écrites par l'étudiant.

1. Thème (« les systèmes nerveux et leurs fonctions ») : `focus_text` ne garde que les passages du cours qui en
   parlent (avec un paragraphe de contexte autour), pour que l'IA ne pose pas de questions sur le reste.
2. Questions écrites à la main : l'IA cherche la réponse dans le cours et écrit les propositions (`MANUAL_SCHEMA`).
   Si le cours ne contient pas la réponse, elle le dit (« found » à false) au lieu d'inventer.
"""

from __future__ import annotations

import math
import re

from .grounding import content_words
from .quiz import QUESTION_TYPES

CONTEXT_PARAGRAPHS = 1   # paragraphes gardés avant et après un passage sur le thème
MAX_SHARE = 0.8          # au-delà, tout le cours parle du thème : on le garde en entier


def _stem(word: str) -> str:
    return word[:6]


def focus_text(course_text: str, focus: str) -> tuple[str, int, int]:
    """Passages du cours sur le thème. Renvoie (texte, passages retenus, passages en tout) ;
    le cours entier si rien ne correspond ou si presque tout correspond."""
    wanted = {_stem(w) for w in content_words(focus)}
    paragraphs = [p for p in re.split(r"\n\s*\n", course_text) if p.strip()]
    if not wanted or not paragraphs:
        return course_text, 0, len(paragraphs)
    need = max(1, math.ceil(len(wanted) / 2))  # « systèmes nerveux … fonctions » : au moins 2 des 3 mots
    hits = [i for i, p in enumerate(paragraphs) if len(wanted & {_stem(w) for w in content_words(p)}) >= need]
    if not hits:
        return course_text, 0, len(paragraphs)
    keep = sorted({j for i in hits for j in range(i - CONTEXT_PARAGRAPHS, i + CONTEXT_PARAGRAPHS + 1)
                   if 0 <= j < len(paragraphs)})
    text = "\n\n".join(paragraphs[j] for j in keep)
    if len(text) > MAX_SHARE * len(course_text):
        return course_text, len(hits), len(paragraphs)
    # Le nom du fichier (« === cours.pages === ») reste en tête : l'IA sait d'où vient le passage.
    header = paragraphs[0].split("\n", 1)[0] if paragraphs[0].startswith("=== ") and 0 not in keep else ""
    return (f"{header}\n{text}" if header else text), len(hits), len(paragraphs)


def best_chunk(question: str, chunks: list[str]) -> int:
    """Morceau du cours qui a le plus de mots en commun avec la question."""
    wanted = {_stem(w) for w in content_words(question)}
    scores = [len(wanted & {_stem(w) for w in content_words(chunk)}) for chunk in chunks]
    return max(range(len(chunks)), key=lambda i: scores[i]) if chunks else 0


MANUAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "found": {"type": "boolean"},
                    "type": {"type": "string", "enum": list(QUESTION_TYPES)},
                    "kind": {"type": "string", "enum": ["cours", "reflexion"]},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["number", "found", "type", "kind", "choices", "answer", "explanation", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}

MANUAL_SYSTEM = """Un étudiant a écrit lui-même des questions sur son cours. Pour chacune, tu écris la réponse et les
propositions, en t'appuyant uniquement sur le cours fourni.

Règles :
- Ne modifie pas la question. Réponds à chaque question, dans l'ordre, avec son numéro ("number").
- "found" : true si le cours contient la réponse, false sinon. Si false, laisse "answer", "choices", "source" et
  "explanation" vides : n'invente jamais une réponse que le cours ne donne pas.
- "type" : le type demandé. "qcm" : exactement 4 propositions dans "choices", une seule correcte, "answer" reprend mot pour
  mot la bonne proposition ; les 3 autres sont plausibles, tirées du même cours, de même forme et de longueur proche,
  mais fausses. "vrai_faux" : "choices" vaut ["Vrai", "Faux"]. "reponse_courte" : "choices" vide, "answer" courte.
- "kind" : "cours" pour une définition, une composition, un terme à retrouver ; "reflexion" sinon.
- "explanation" : une ou deux phrases qui justifient la réponse avec le cours.
- "source" : recopie mot pour mot la phrase du cours qui contient la réponse.
- Réponds uniquement avec le JSON demandé."""


def build_manual_prompt(chunk: str, questions: list[tuple[int, str]], qtype: str, language: str) -> str:
    listing = "\n".join(f"{number}. {text}" for number, text in questions)
    return f"""Voici un cours :

<cours>
{chunk}
</cours>

Questions de l'étudiant (type demandé : "{qtype}", langue : {language}) :
{listing}"""


def parse_manual_lines(text: str, limit: int = 30) -> list[str]:
    """Une question par ligne ; les puces et numéros du début sont retirés."""
    questions = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-•*]|\d{1,2}[.)])\s*", "", line).strip()
        if len(line) >= 4:
            questions.append(line[:300])
    return questions[:limit]
