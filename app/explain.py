"""« Explique-moi » : l'IA réexplique une question ou une carte à partir du passage du cours d'où elle vient."""

from __future__ import annotations

from .grounding import normalize

EXPLAIN_SCHEMA: dict = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
    "additionalProperties": False,
}

EXPLAIN_SYSTEM = """Tu es un tuteur bienveillant qui aide un étudiant à comprendre une notion de son cours.

Règles :
- Appuie-toi uniquement sur l'extrait du cours fourni. N'ajoute aucune connaissance extérieure.
- Explique simplement pourquoi la bonne réponse est la bonne, avec d'autres mots que le cours si ça aide.
- Si l'étudiant a donné une autre réponse, explique en une phrase ce qui ne va pas dans la sienne, sans le juger.
- Termine par une courte astuce pour retenir (un lien, une image, un exemple tiré du cours).
- 3 à 6 phrases, en français, sans titre ni liste, en t'adressant à l'étudiant (« tu »).
- Réponds uniquement avec le JSON demandé."""

CONTEXT_CHARS = 2500


def course_excerpt(course_text: str, source: str, question: str) -> str:
    """Le passage du cours autour de la phrase source (ou, sans source, les paragraphes les plus proches de la question)."""
    paragraphs = [p for p in course_text.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    target = normalize(source)
    index = None
    if len(target) >= 12:
        probe = target[: min(len(target), 60)]
        index = next((i for i, p in enumerate(paragraphs) if probe in normalize(p)), None)
    if index is None:
        from .focus import best_chunk

        index = best_chunk(f"{question} {source}", paragraphs)
    # Le paragraphe trouvé et ses voisins, dans la limite de CONTEXT_CHARS.
    start = end = index
    text = paragraphs[index]
    while len(text) < CONTEXT_CHARS and (start > 0 or end < len(paragraphs) - 1):
        if start > 0:
            start -= 1
        if end < len(paragraphs) - 1:
            end += 1
        text = "\n\n".join(paragraphs[start:end + 1])
    return text[: CONTEXT_CHARS * 2]


def build_explain_prompt(excerpt: str, question: str, expected: str, given: str) -> str:
    return f"""Extrait du cours :
<cours>
{excerpt}
</cours>

Question : {question}
Bonne réponse : {expected}
Réponse de l'étudiant : {given.strip() or "(pas de réponse)"}"""
