"""Logique commune aux deux moteurs : schéma, prompt, découpage et validation du quiz."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field

QUESTION_TYPES = ("qcm", "vrai_faux", "reponse_courte", "texte_a_trous")
BLANK = "_____"  # le trou d'une phrase à compléter
# « cours » : définition, composition, terme à retrouver ; « reflexion » : pourquoi, conséquence, différence, cas concret.
QUESTION_KINDS = ("cours", "reflexion")
# Part minimale de questions de cours, au choix sur la page de création du quiz.
COURSE_SHARES = {"equilibre": 0.4, "beaucoup": 0.7, "que": 1.0}

DIFFICULTIES = {
    "facile": "Questions de restitution : définitions, faits clés, vocabulaire.",
    "moyen": "Mélange de restitution et de compréhension : relier des notions, expliquer un mécanisme.",
    "difficile": "Questions d'application et d'analyse : cas pratiques, pièges fréquents, raisonnement en plusieurs étapes.",
}

# Schéma JSON du quiz, partagé par Claude (structured outputs) et Ollama (paramètre `format`).
QUIZ_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(QUESTION_TYPES)},
                    "kind": {"type": "string", "enum": list(QUESTION_KINDS)},
                    "question": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "source": {"type": "string"},
                    "key_terms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"term": {"type": "string"}, "definition": {"type": "string"}},
                            "required": ["term", "definition"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["type", "kind", "question", "choices", "answer", "explanation", "source", "key_terms"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "questions"],
    "additionalProperties": False,
}


def quiz_schema(types: list[str]) -> dict:
    """Le schéma n'autorise que les types cochés : le modèle ne peut pas produire une question qui serait écartée."""
    schema = copy.deepcopy(QUIZ_SCHEMA)
    schema["properties"]["questions"]["items"]["properties"]["type"]["enum"] = [t for t in QUESTION_TYPES if t in types]
    return schema


SYSTEM_PROMPT = """Tu es un enseignant expérimenté qui prépare des quiz de révision à partir des cours d'un étudiant.

Règles :
- Chaque question porte sur une information présente dans le cours fourni. N'utilise AUCUNE connaissance extérieure :
  la question, la bonne réponse et l'explication doivent se trouver dans le cours, avec ses mots. Si le cours parle
  d'un sujet sans donner un détail (un mécanisme, un nom, une date), ne pose pas de question sur ce détail.
- Vise les notions importantes (définitions, mécanismes, formules, dates, causes/conséquences), pas les détails anecdotiques ni la mise en page.
- Ignore l'organisation du cours : modalités d'évaluation (notes, pourcentages, coefficients, partiels, dossiers à
  rendre), calendrier, horaires, salles, contacts, plan du cours, bibliographie. Seul le contenu à apprendre compte.
- Questions claires, autonomes, sans ambiguïté, et sans répétition entre elles.
- Sauf pour "texte_a_trous", l'énoncé est une vraie question, courte (une ou deux phrases) et terminée par « ? ».
  Jamais une phrase inachevée qui s'arrête sur un article ou une élision (« d' », « l' », « le », « une », « des »…) :
  ça trahirait la réponse.
- L'énoncé ne donne pas la réponse : il ne contient ni le terme attendu ni sa définition, et ne recopie pas la phrase
  du cours. On doit avoir appris le cours pour répondre.
- Varie les formes, sans en abuser d'aucune : « Qu'est-ce que… ? » (les choix sont des définitions), « Comment
  appelle-t-on… ? » (les choix sont des termes), « Pourquoi… ? », « Quelle est la conséquence de… ? », « Quelle est la
  différence entre… ? », un petit cas concret à analyser.
- "qcm" : exactement 4 propositions dans "choices", une seule correcte ; "answer" reprend mot pour mot la bonne
  proposition. Les distracteurs sont plausibles et tirés du même cours. Les 4 propositions ont la même forme
  grammaticale, une longueur proche et s'accordent toutes avec l'énoncé : aucun indice (voyelle, genre, pluriel,
  longueur, mot repris de l'énoncé) ne doit désigner la bonne réponse.
- "vrai_faux" : "choices" vaut ["Vrai", "Faux"] et "answer" vaut "Vrai" ou "Faux".
- "reponse_courte" : "choices" est une liste vide et "answer" est la réponse attendue, courte.
- "texte_a_trous" : "question" recopie une phrase importante du cours où UN mot ou groupe de mots essentiel (un terme
  technique, un nom, un chiffre ; jamais un petit mot) est remplacé par _____ ; "answer" est exactement ce qui manque,
  tel qu'écrit dans le cours (1 à 5 mots) ; "choices" est une liste vide ; "source" est la phrase complète.
- "explanation" justifie la réponse en une ou deux phrases, directement (sans « Le cours précise que… » ni
  « Selon le cours… » : on sait que tout vient du cours).
- "source" : recopie mot pour mot, sans rien changer, la phrase du cours qui contient la réponse. Si aucune phrase
  du cours ne contient la réponse, n'écris pas cette question. Chaque question est vérifiée : une source qui n'est
  pas dans le cours fait rejeter la question.
- "key_terms" : de 0 à 3 termes techniques ou spécialisés apparaissant dans la question, la réponse ou l'explication, qu'un étudiant pourrait ne pas connaître, chacun avec une définition simple en une phrase. Liste vide s'il n'y en a pas.
- Réponds uniquement avec le JSON demandé."""


@dataclass
class QuizOptions:
    num_questions: int = 10
    difficulty: str = "moyen"
    types: list[str] = field(default_factory=lambda: list(QUESTION_TYPES))
    language: str = "français"
    avoid: list[str] = field(default_factory=list)  # questions déjà posées (quand on en redemande)
    course_share: float = COURSE_SHARES["equilibre"]  # part minimale de questions de cours (définitions…)
    definition_rule: str | None = None  # comment repérer les définitions (Réglages → Tes définitions)
    focus: str = ""  # thème précis demandé par l'étudiant (« les systèmes nerveux et leurs fonctions »)
    cover: bool = False  # couvrir tout le texte (banque de questions), plutôt qu'un petit quiz


COVER_MIN, COVER_MAX = 10, 40
CHARS_PER_QUESTION = 450  # un chapitre de 9 000 caractères (≈ 4 pages) → 20 questions


CARDS_MIN, CARDS_MAX = 10, 50
CHARS_PER_CARD = 500


def cards_coverage_size(text: str, definitions: int = 0) -> int:
    """Nombre de flashcards pour couvrir un texte : une par définition repérée, et assez pour sa longueur (10 à 50)."""
    return max(CARDS_MIN, min(CARDS_MAX, max(definitions, round(len(text) / CHARS_PER_CARD))))


def coverage_size(text: str, definitions: int = 0) -> int:
    """Nombre de questions pour couvrir tout un texte : une par définition repérée, et assez pour sa longueur
    (entre 10 et 40)."""
    return max(COVER_MIN, min(COVER_MAX, max(definitions, round(len(text) / CHARS_PER_QUESTION))))


def course_quota(n_questions: int, share: float) -> int:
    """Nombre minimal de questions de cours parmi n (arrondi au-dessus : 40 % de 10 → 4, 70 % de 5 → 4)."""
    return min(n_questions, math.ceil(round(n_questions * share, 6)))


def build_user_prompt(course_text: str, n_questions: int, options: QuizOptions, part: str = "") -> str:
    types_label = ", ".join(f'"{t}"' for t in options.types)
    difficulty = DIFFICULTIES.get(options.difficulty, DIFFICULTIES["moyen"])
    scope = f" ({part})" if part else ""
    return f"""Voici un cours{scope} :

<cours>
{course_text}
</cours>

Génère un quiz de exactement {n_questions} questions sur ce cours.
- Types autorisés : {types_label} (varie les types si plusieurs sont autorisés).
- Niveau : {options.difficulty} — {difficulty}
- Langue des questions, réponses et explications : {options.language}.
- "title" : un titre court décrivant le thème du cours.
{_focus_block(options.focus)}{_cover_block(options.cover)}{_kind_block(n_questions, options)}{_definitions_prompt(course_text, options.definition_rule)}{_avoid_block(options.avoid)}"""


def _focus_block(focus: str) -> str:
    if not focus:
        return ""
    return (f"- Thème imposé : toutes les questions portent sur « {focus} », en s'appuyant uniquement sur ce que le"
            " cours en dit. Aucune question sur le reste du cours. Si le cours en dit peu, varie les angles (définition,"
            " rôle, composition, différence, conséquence) plutôt que de sortir du thème.\n")


def _cover_block(cover: bool) -> str:
    if not cover:
        return ""
    return ("- Couvre tout le texte, du début à la fin : chaque définition et chaque notion importante a sa question,"
            " une seule par notion. Ne laisse aucune partie du texte de côté.\n")


def _definitions_prompt(course_text: str, rule: str | None) -> str:
    from .definitions import definitions_block

    block = definitions_block(course_text, rule)
    if not block:
        return ""
    return ("\n- Les définitions ci-dessous ont été repérées dans ce cours grâce à sa mise en forme. Les questions de"
            " cours portent en priorité sur elles : définition à reconnaître, terme à retrouver à partir de sa"
            " définition, définition à réécrire, éléments qui composent une notion." + block)


def _kind_block(n_questions: int, options: QuizOptions) -> str:
    quota = course_quota(n_questions, options.course_share)
    definition_forms = ("« Qu'est-ce que … ? » (définition), « De quoi est composé … ? » / « Quels sont les éléments de"
                        " … ? » (composition), « Comment appelle-t-on … ? » (retrouver le terme à partir de sa définition)")
    if "reponse_courte" in options.types:
        definition_forms += ", « Définis … » en réponse courte (réécrire la définition du cours)"
    if quota >= n_questions:
        return (f'- Toutes les questions sont des questions de cours ("kind": "cours") : {definition_forms}.')
    return (f'- Au moins {quota} questions sur {n_questions} sont des questions de cours ("kind": "cours") : '
            f'{definition_forms}. Les autres ("kind": "reflexion") : pourquoi, conséquence, différence, cas concret.')


def _avoid_block(avoid: list[str]) -> str:
    if not avoid:
        return ""
    listing = "\n".join(f"- {q}" for q in avoid[:80])
    return ("\n- Ces questions existent déjà (dans ce quiz ou dans les quiz précédents sur ce cours) : n'en reprends"
            f" aucune, même reformulée ; porte sur d'autres notions ou sous un autre angle.\n{listing}")


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Découpe le texte en morceaux d'au plus `max_chars`, en coupant de préférence entre paragraphes."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        while len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def plan_chunks(chunks: list[str], total_questions: int) -> list[tuple[str, int]]:
    """Répartit les questions sur les morceaux, proportionnellement à leur taille.

    S'il y a plus de morceaux que de questions, on garde des morceaux régulièrement
    espacés pour couvrir l'ensemble du cours.
    """
    if len(chunks) > total_questions:
        step = len(chunks) / total_questions
        chunks = [chunks[int(i * step)] for i in range(total_questions)]
    sizes = [len(c) for c in chunks]
    total_size = sum(sizes)
    counts = [max(1, round(total_questions * s / total_size)) for s in sizes]
    # Ajuste pour tomber exactement sur le total demandé.
    while sum(counts) > total_questions:
        i = max(range(len(counts)), key=lambda k: counts[k])
        counts[i] -= 1
    while sum(counts) < total_questions:
        i = max(range(len(counts)), key=lambda k: sizes[k] / counts[k])
        counts[i] += 1
    return [(c, n) for c, n in zip(chunks, counts) if n > 0]


COURSE_INTRO = re.compile(
    r"^(?:(?:selon|d'après|d’après|dans) (?:le|ton|ce) cours,?\s*|(?:le|ton|ce) cours (?:précise|indique|explique|dit|"
    r"stipule|mentionne|affirme|souligne|décrit|définit|rappelle|montre|note)(?: bien)? (?:que |qu'|qu’|:\s*)?)",
    re.IGNORECASE)


def strip_course_intro(text: str) -> str:
    """« Le cours précise que les oligodendrocytes… » → « Les oligodendrocytes… »."""
    rest = COURSE_INTRO.sub("", text, count=1).strip()
    return rest[:1].upper() + rest[1:] if rest and rest != text else text


def plain_text(value) -> str:
    """Sans les marques de gras / italique du cours (« ***GABA*** » → « GABA »), que le modèle recopie parfois."""
    text = re.sub(r"\*{2,3}(?=\S)(.+?)(?<=\S)\*{2,3}", r"\1", str(value or ""))
    text = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", text)
    return text.strip()


def normalize_question(raw: dict, allowed_types: list[str]) -> dict | None:
    """Valide et corrige une question renvoyée par le modèle. Renvoie None si elle est inutilisable."""
    qtype = str(raw.get("type", "")).strip().lower().replace("-", "_").replace(" ", "_")
    question = plain_text(raw.get("question", ""))
    answer = plain_text(raw.get("answer", ""))
    explanation = strip_course_intro(plain_text(raw.get("explanation", "")))
    source = plain_text(raw.get("source", ""))
    kind = "cours" if str(raw.get("kind", "")).strip().lower() == "cours" else "reflexion"
    choices = [plain_text(c) for c in raw.get("choices") or [] if plain_text(c)]

    if qtype not in QUESTION_TYPES or qtype not in allowed_types or not question or not answer:
        return None

    if qtype == "vrai_faux":
        lowered = answer.lower()
        if lowered in {"vrai", "true", "v"}:
            answer = "Vrai"
        elif lowered in {"faux", "false", "f"}:
            answer = "Faux"
        else:
            return None
        choices = ["Vrai", "Faux"]
    elif qtype == "qcm":
        choices = list(dict.fromkeys(choices))  # dédoublonne en gardant l'ordre
        if len(choices) < 2:
            return None
        match = _find_choice(answer, choices)
        if match is None:
            return None
        answer = match
    elif qtype == "texte_a_trous":
        question = re.sub(r"_{3,}|…{2,}|\.{4,}", BLANK, question)
        # Un seul trou, et une réponse courte qui n'est pas déjà dans la phrase.
        if question.count(BLANK) != 1 or len(answer.split()) > 6:
            return None
        choices = []
    else:
        choices = []

    key_terms = []
    for item in raw.get("key_terms") or []:
        if isinstance(item, dict):
            term, definition = str(item.get("term", "")).strip(), str(item.get("definition", "")).strip()
            if term and definition and term.lower() not in {t["term"].lower() for t in key_terms}:
                key_terms.append({"term": term, "definition": definition})

    return {"type": qtype, "kind": kind, "question": question, "choices": choices, "answer": answer,
            "explanation": explanation, "source": source, "key_terms": key_terms[:4]}


def _find_choice(answer: str, choices: list[str]) -> str | None:
    for choice in choices:
        if choice == answer:
            return choice
    lowered = answer.lower()
    for choice in choices:
        if choice.lower() == lowered:
            return choice
    # Le modèle a parfois répondu par la lettre ("B" ou "B)").
    letter = re.fullmatch(r"([A-Da-d])[\).:]?", answer)
    if letter:
        index = ord(letter.group(1).upper()) - ord("A")
        if index < len(choices):
            return choices[index]
    # Ou par "B) texte de la réponse".
    stripped = re.sub(r"^[A-Da-d][\).:]\s*", "", answer).lower()
    for choice in choices:
        if choice.lower() == stripped:
            return choice
    return None


# Fin d'énoncé qui annonce la forme de la réponse (« … une phase d' » → un mot qui commence par une voyelle).
TELLTALE_ENDING = re.compile(r"(?:\b(?:d|l|qu|j|n|s|c|m|t)['’]|\b(?:le|la|les|un|une|des|du|de|au|aux|à|en))\s*[.…:]*\s*$",
                             re.IGNORECASE)


def giveaway(question: dict) -> bool:
    """Question trop facile : l'énoncé s'arrête sur un article, ou contient déjà la réponse."""
    stem = question["question"].strip()
    if question["type"] == "texte_a_trous":
        stem = stem.replace(BLANK, " ")  # le trou peut suivre un article : c'est le principe
    elif TELLTALE_ENDING.search(stem):
        return True
    if question["type"] == "vrai_faux":
        return False
    from .grounding import content_words

    answer = content_words(question["answer"])
    return bool(answer) and answer <= content_words(stem)


def assemble_quiz(parts: list[dict], options: QuizOptions, fallback_title: str, grounding=None,
                  previous: list[dict] = ()) -> dict:
    """Questions valides, sans doublon. Sont écartées (et renvoyées dans "rejected" pour être remplacées) :
    les questions trop faciles (voir `giveaway`), celles qui ne viennent pas du cours (avec `grounding`) et celles
    qui répètent, même reformulées, une question d'un quiz déjà créé sur ce cours (`previous`)."""
    from .grounding import is_logistics
    from .revision import is_duplicate

    def notion(q: dict) -> dict:
        # « Vrai » / « Faux » ne dit rien de la notion : pour ces questions, seul l'énoncé compte.
        return {"front": q["question"], "back": "" if q["type"] == "vrai_faux" else q["answer"]}

    asked = [notion(q) for q in previous]
    questions: list[dict] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for raw in part.get("questions") or []:
            if not isinstance(raw, dict):
                continue
            question = normalize_question(raw, options.types)
            if question is None:
                continue
            key = re.sub(r"\W+", "", question["question"].lower())
            if key in seen:
                continue
            seen.add(key)
            if (giveaway(question) or is_logistics(question["source"], question["question"], question["answer"])
                    or (grounding is not None and not grounding.check_question(question))
                    or is_duplicate(**notion(question), others=asked)):
                rejected.append(question["question"])
                continue
            questions.append(question)
    title = next((str(p.get("title")).strip() for p in parts if str(p.get("title") or "").strip()), fallback_title)
    # Un modèle qui ignore complètement l'étiquette « kind » : pas de quota (sinon le quiz resterait incomplet).
    labelled = any(isinstance(raw, dict) and "kind" in raw for part in parts for raw in part.get("questions") or [])
    quota = course_quota(options.num_questions, options.course_share) if labelled else 0
    chosen, missing_course = pick_with_quota(questions, options.num_questions, quota)
    return {"title": title, "questions": chosen, "rejected": rejected, "missing_course": missing_course}


def pick_with_quota(questions: list[dict], n: int, quota: int) -> tuple[list[dict], int]:
    """Garde au plus n questions dont au moins `quota` questions de cours (dans l'ordre d'origine).
    S'il n'y a pas assez de questions de cours, laisse leur place libre et renvoie combien il en manque."""
    course = [i for i, q in enumerate(questions) if q.get("kind") == "cours"]
    other = [i for i, q in enumerate(questions) if q.get("kind") != "cours"]
    keep = course[:quota]
    rest = [i for i in other + course[quota:]]  # le reste : d'abord de la réflexion, pour garder le mélange
    free = n - quota if len(keep) < quota else n - len(keep)
    keep += rest[:free]
    return [questions[i] for i in sorted(keep)], max(0, quota - len(course))
