"""Flashcards : schéma, prompt, validation et élimination des doublons."""

from __future__ import annotations

import re
import unicodedata
import uuid

CARDS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"front": {"type": "string"}, "back": {"type": "string"}, "source": {"type": "string"}},
                "required": ["front", "back", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["cards"],
    "additionalProperties": False,
}

CARDS_SYSTEM = """Tu crées des flashcards (cartes mémoire recto/verso) pour aider un étudiant à mémoriser son cours.

Règles :
- N'utilise que des informations présentes dans le cours fourni, avec ses mots. Aucune connaissance extérieure :
  si le cours ne donne pas un détail, ne fais pas de carte sur ce détail.
- Recto ("front") : une question courte et précise, ou un terme à définir. Une seule notion par carte.
- Verso ("back") : la réponse, courte (une à deux phrases maximum), mémorisable.
- Couvre les notions importantes de tout le cours : définitions, mécanismes, causes/conséquences, formules, dates.
- Chaque carte porte sur une notion différente : jamais deux cartes sur la même notion, même formulées autrement.
- Pas de questions auxquelles on peut répondre par oui/non.
- Ignore l'organisation du cours : modalités d'évaluation (notes, pourcentages, coefficients, partiels, dossiers à
  rendre), calendrier, horaires, salles, contacts, plan du cours, bibliographie. Seul le contenu à apprendre compte.
- "source" : recopie mot pour mot, sans rien changer, la phrase du cours qui contient la réponse. Chaque carte est
  vérifiée : une source qui n'est pas dans le cours fait rejeter la carte.
- Réponds uniquement avec le JSON demandé."""


def build_cards_prompt(course_text: str, n_cards: int, language: str, part: str = "", avoid=(),
                       definition_rule: str | None = None, focus: str = "") -> str:
    scope = f" ({part})" if part else ""
    avoid_block = ""
    if avoid:
        listing = "\n".join(f"- {front}" for front in list(avoid)[:80])
        avoid_block = f"\nCes cartes existent déjà : n'en reprends aucune, choisis d'autres notions.\n{listing}"
    return f"""Voici un cours{scope} :

<cours>
{course_text}
</cours>

Crée exactement {n_cards} flashcards sur ce cours, en {language}.{_focus_cards(focus)}{_definitions_cards(course_text, definition_rule)}{avoid_block}"""


def _focus_cards(focus: str) -> str:
    return (f"\nThème imposé : toutes les cartes portent sur « {focus} », avec ce que le cours en dit ;"
            " aucune carte sur le reste du cours.") if focus else ""


def _definitions_cards(course_text: str, rule: str | None) -> str:
    from .definitions import definitions_block

    block = definitions_block(course_text, rule)
    if not block:
        return ""
    return ("\nCes définitions ont été repérées dans le cours grâce à sa mise en forme : fais d'abord une carte par"
            " définition (recto : le terme ou « Qu'est-ce que … ? », verso : la définition), puis complète avec le reste"
            " du cours." + block)


# Mots qui ne disent rien de la notion (« Qu'est-ce que… », « Définis… ») : ignorés pour repérer les doublons.
STOPWORDS = set("""
a au aux avec ce ces cette c d de des du elle en est et il ils l la le les leur lui ne on ou par pas pour qu que
quel quelle quels quelles qui quoi s sa se ses son sont sur un une y dans comment pourquoi quand combien
definis definir definition explique expliquer donne donner cite citer decris decrire nomme nommer signifie
designe appelle appele terme notion concept
""".split())


def _words(text: str) -> frozenset[str]:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return frozenset(w for w in re.findall(r"\w+", text) if w not in STOPWORDS and len(w) > 1)


def _similarity(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def is_duplicate(front: str, back: str, others: list[dict]) -> bool:
    """Même notion qu'une carte existante : même question (reformulée) ou même réponse."""
    f, b = _words(front), _words(back)
    for other in others:
        of, ob = _words(other["front"]), _words(other["back"])
        if f == of or _similarity(f, of) >= 0.75:
            return True
        same_answer = _similarity(b, ob)
        if (len(b) >= 3 and same_answer >= 0.9) or (same_answer >= 0.8 and _similarity(f, of) >= 0.4):
            return True
    return False


def normalize_cards(parts: list[dict], limit: int, existing: list[dict] = (), grounding=None,
                    rejected: list[str] | None = None) -> list[dict]:
    """Nouvelles cartes valides, sans doublon entre elles ni avec les cartes `existing`, au plus `limit`.
    Avec `grounding`, les cartes qui ne viennent pas du cours sont écartées (leur recto est ajouté à `rejected`)."""
    kept: list[dict] = []
    for part in parts:
        for item in part.get("cards") or []:
            if len(kept) >= limit:
                return kept
            if not isinstance(item, dict):
                continue
            from .quiz import plain_text

            front, back = plain_text(item.get("front", "")), plain_text(item.get("back", ""))
            source = plain_text(item.get("source", ""))
            if not front or not back or is_duplicate(front, back, [*existing, *kept]):
                continue
            from .grounding import is_logistics

            if grounding is not None and is_logistics(front, back, source):  # organisation du cours, pas à apprendre
                if rejected is not None:
                    rejected.append(front)
                continue
            card = {"id": uuid.uuid4().hex[:8], "front": front, "back": back, "source": source,
                    "status": "new", "reviews": 0, "last_reviewed": None}
            if grounding is not None and not grounding.check_card(card):
                if rejected is not None:
                    rejected.append(front)
                continue
            kept.append(card)
    return kept
