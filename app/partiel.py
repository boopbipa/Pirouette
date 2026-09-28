"""Mode partiel : correction par l'IA des réponses écrites (flashcards, réponses courtes).

L'IA juge l'idée, pas la formulation : un synonyme ou une paraphrase correcte compte juste. Chaque verdict vient avec
une courte justification, et l'étudiant peut toujours le contester dans la correction.
"""

from __future__ import annotations

VERDICTS = ("juste", "partiel", "faux")

GRADE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "grades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "reason": {"type": "string"},
                },
                "required": ["number", "verdict", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["grades"],
    "additionalProperties": False,
}

GRADE_SYSTEM = """Tu corriges la copie d'un étudiant à un partiel blanc. Pour chaque question, on te donne la réponse
attendue (tirée de son cours) et la réponse écrite par l'étudiant.

Règles :
- Juge l'idée, pas la formulation : un synonyme, une paraphrase ou un ordre différent comptent juste si le sens est
  le même. Ignore les fautes d'orthographe.
- "juste" : l'essentiel de la réponse attendue est là. "partiel" : une partie seulement, ou une idée juste mais
  incomplète. "faux" : à côté, contraire à la réponse attendue, ou réponse vide.
- Ne sois pas plus exigeant que la réponse attendue : un détail qu'elle ne contient pas n'est pas exigé.
- "reason" : une phrase courte, en français, qui s'adresse à l'étudiant (« Il manque la moelle épinière. »).
- Réponds pour chaque question, avec son numéro ("number"), uniquement avec le JSON demandé."""

BATCH = 10  # réponses corrigées par demande


def build_grade_prompt(items: list[tuple[int, dict]]) -> str:
    blocks = []
    for number, item in items:
        blocks.append(f"""Question {number} : {item.get('question', '').strip()}
Réponse attendue : {item.get('expected', '').strip()}
{f"Phrase du cours : {item['source'].strip()}" if item.get('source') else ""}
Réponse de l'étudiant : {item.get('given', '').strip() or '(vide)'}""".replace("\n\n", "\n"))
    return "\n\n".join(blocks)


def read_grades(answer: dict, numbers: list[int]) -> dict[int, dict]:
    """Verdicts valides de la réponse de l'IA, par numéro de question."""
    grades = {}
    for grade in (answer or {}).get("grades") or []:
        if not isinstance(grade, dict) or grade.get("number") not in numbers:
            continue
        verdict = str(grade.get("verdict", "")).lower()
        if verdict in VERDICTS:
            grades.setdefault(grade["number"], {"verdict": verdict, "reason": str(grade.get("reason", "")).strip()})
    return grades
