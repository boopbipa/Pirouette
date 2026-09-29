"""S'échanger des quiz et des flashcards par un simple fichier texte.

Export :
- un quiz devient un .txt lisible par tout le monde (numéros, propositions A. B. C. D., « ✓ » sur la bonne réponse,
  explication, phrase du cours), que Pirouette sait relire à l'identique ;
- des flashcards deviennent un .txt « recto <tabulation> verso », avec l'en-tête qu'Anki comprend : importable dans
  Anki, Quizlet, Numbers ou Excel.

Import (tolérant) : un fichier Pirouette, mais aussi un quiz écrit à la main (« 1. Question ? » puis « a) … » avec
« ✓ », « * » ou « Réponse : b »), ou des flashcards venant d'Anki, de Quizlet ou d'un tableur (tabulation, « :: »,
« ; », ou des blocs « Q : … / R : … »).
"""

from __future__ import annotations

import re

from .quiz import BLANK, QUESTION_TYPES, normalize_question

TYPE_LABELS = {"qcm": "QCM", "vrai_faux": "Vrai/Faux", "reponse_courte": "Réponse courte", "texte_a_trous": "Texte à trous"}
LABEL_TYPES = {re.sub(r"\W", "", v.lower()): k for k, v in TYPE_LABELS.items()} | {"vraifaux": "vrai_faux", "vf": "vrai_faux"}
MARK = "✓"
LETTERS = "ABCDEFGH"


# ---------- Export ----------

def quiz_to_text(quiz: dict, course_name: str = "") -> str:
    lines = ["Pirouette · quiz", f"Titre : {quiz.get('custom_title') or quiz.get('title') or 'Quiz'}"]
    if course_name:
        lines.append(f"Cours : {course_name}")
    if quiz.get("scope"):
        lines.append(f"Chapitres : {' ; '.join(quiz['scope'])}")
    lines.append("")
    for number, q in enumerate(quiz.get("questions", []), start=1):
        lines.append(f"{number}. [{TYPE_LABELS.get(q['type'], q['type'])}] {q['question']}")
        if q["type"] == "qcm":
            for letter, choice in zip(LETTERS, q.get("choices") or []):
                lines.append(f"   {letter}. {choice}{'  ' + MARK if choice == q['answer'] else ''}")
        else:
            lines.append(f"   Réponse : {q['answer']}")
        if q.get("explanation"):
            lines.append(f"   Explication : {q['explanation']}")
        if q.get("source"):
            lines.append(f"   Source : {q['source']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def cards_to_text(cards: list[dict], course_name: str = "") -> str:
    """Recto <tab> verso, une carte par ligne. Les lignes « # » sont l'en-tête d'Anki (ignoré par les autres)."""
    clean = lambda text: re.sub(r"\s+", " ", str(text)).strip()  # noqa: E731
    head = ["#separator:tab", "#html:false"] + ([f"#deck:{clean(course_name)}"] if course_name else [])
    return "\n".join(head + [f"{clean(c['front'])}\t{clean(c['back'])}" for c in cards]) + "\n"


# ---------- Import ----------

QUESTION_LINE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s*(?:\[([^\]]+)\]\s*)?(.+?)\s*$")
CHOICE_LINE = re.compile(r"^\s*([*✓✔]\s*)?([A-Ha-h])\s*[.)]\s*(.+?)\s*$")
FIELD_LINE = re.compile(r"^\s*(réponse|reponse|bonne réponse|explication|source|answer)\s*:\s*(.*)$", re.IGNORECASE)
HEADER_LINE = re.compile(r"^\s*(titre|cours|chapitres?)\s*:\s*(.+)$", re.IGNORECASE)
GOOD = re.compile(r"\s*(✓|✔|\(bonne réponse\)|\(correct\)|\*)\s*$", re.IGNORECASE)


def parse(text: str) -> dict:
    """Devine s'il s'agit d'un quiz ou de flashcards. Renvoie {"kind": "quiz", "title", "chapters", "questions",
    "skipped"} ou {"kind": "cards", "cards", "skipped"} ; {"kind": None} si rien n'est reconnu."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    if sum(bool(QUESTION_LINE.match(line)) for line in text.split("\n")) >= 1 and _looks_like_quiz(text):
        return _parse_quiz(text)
    cards, skipped = _parse_cards(text)
    if cards:
        return {"kind": "cards", "cards": cards, "skipped": skipped}
    return {"kind": None}


def _looks_like_quiz(text: str) -> bool:
    """Un quiz : des questions numérotées suivies de propositions ou d'une ligne « Réponse : »."""
    lines = text.split("\n")
    return any(CHOICE_LINE.match(line) or FIELD_LINE.match(line) for line in lines) or "Pirouette · quiz" in text


def _parse_quiz(text: str) -> dict:
    title, chapters, blocks = "", [], []
    current = None
    for line in text.split("\n"):
        if not line.strip():
            continue
        header = HEADER_LINE.match(line)
        if header and current is None:
            key, value = header.group(1).lower(), header.group(2).strip()
            if key == "titre":
                title = value
            elif key.startswith("chapitre"):
                chapters = [c.strip() for c in value.split(";") if c.strip()]
            continue
        question = QUESTION_LINE.match(line)
        if question and not CHOICE_LINE.match(line):
            current = {"label": question.group(2) or "", "question": question.group(3), "choices": [], "good": None,
                       "answer": "", "explanation": "", "source": ""}
            blocks.append(current)
            continue
        if current is None:
            continue
        field = FIELD_LINE.match(line)
        if field:
            key, value = field.group(1).lower(), field.group(2).strip()
            if key.startswith("expl"):
                current["explanation"] = value
            elif key == "source":
                current["source"] = value.strip("«» \"")
            else:
                current["answer"] = value
            continue
        choice = CHOICE_LINE.match(line)
        if choice:
            body = choice.group(3)
            good = bool(choice.group(1)) or bool(GOOD.search(body))
            body = GOOD.sub("", body).strip()
            current["choices"].append(body)
            if good:
                current["good"] = body
            continue
        if not current["choices"] and not current["answer"]:
            current["question"] += " " + line.strip()  # énoncé sur plusieurs lignes
    questions, skipped = [], 0
    for block in blocks:
        question = normalize_question(_raw_question(block), list(QUESTION_TYPES))
        if question:
            questions.append(question)
        else:
            skipped += 1
    return {"kind": "quiz", "title": title, "chapters": chapters, "questions": questions, "skipped": skipped}


def _raw_question(block: dict) -> dict:
    label = LABEL_TYPES.get(re.sub(r"\W", "", block["label"].lower()), "")
    choices, answer = block["choices"], block["good"] or block["answer"]
    # « Réponse : b » : la lettre d'une proposition
    if choices and re.fullmatch(r"[A-Ha-h]", answer or ""):
        index = LETTERS.index(answer.upper())
        answer = choices[index] if index < len(choices) else answer
    if not label:
        if choices and {c.lower() for c in choices} <= {"vrai", "faux"}:
            label = "vrai_faux"
        elif len(choices) >= 2:
            label = "qcm"
        elif BLANK in re.sub(r"_{3,}|…{2,}|\.{4,}", BLANK, block["question"]):
            label = "texte_a_trous"
        elif answer.strip().lower() in {"vrai", "faux"}:
            label = "vrai_faux"
        else:
            label = "reponse_courte"
    return {"type": label, "kind": "cours", "question": block["question"], "choices": choices, "answer": answer,
            "explanation": block["explanation"], "source": block["source"], "key_terms": []}


def _parse_cards(text: str) -> tuple[list[dict], int]:
    cards, skipped = [], 0
    lines = [line for line in text.split("\n") if line.strip() and not line.startswith("#")]
    # Blocs « Q : … » / « R : … »
    pending = None
    qa = re.compile(r"^\s*(q|question|recto)\s*:\s*(.+)$", re.IGNORECASE)
    ra = re.compile(r"^\s*(r|réponse|reponse|a|answer|verso)\s*:\s*(.+)$", re.IGNORECASE)
    if any(qa.match(line) for line in lines):
        for line in lines:
            if (m := qa.match(line)):
                pending = m.group(2).strip()
            elif (m := ra.match(line)) and pending:
                cards.append({"front": pending, "back": m.group(2).strip()})
                pending = None
        return cards, skipped
    for line in lines:
        for separator in ("\t", " :: ", "::", ";", " - "):
            if separator in line:
                front, back = line.split(separator, 1)
                # CSV Pirouette : « Recto;Verso;Cours;… » (on garde les deux premières colonnes)
                back = back.split(separator)[0] if separator == ";" else back
                front, back = front.strip().strip('"'), back.strip().strip('"')
                if front.lower() in {"recto", "front", "question"} and back.lower() in {"verso", "back", "réponse", "reponse", "answer"}:
                    break  # ligne d'en-tête
                if front and back:
                    cards.append({"front": front, "back": back})
                else:
                    skipped += 1
                break
        else:
            skipped += 1
    return cards, skipped
