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


# ---------- Paquet : tous les quiz et flashcards d'un cours ou d'un semestre, dans un .zip ----------
#
# Pirouette - <nom>.zip
#   pirouette.json                     index : les cours, leur semestre et leurs fichiers
#   <Cours>/Quiz - <titre>.txt         chaque quiz (le même .txt qu'un export seul : lisible, importable seul)
#   <Cours>/Flashcards.txt             les cartes (format Anki / Quizlet)

PACK_INDEX = "pirouette.json"


def _file_name(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "-", name).strip(" .-")[:80] or "Sans nom"


def build_pack(courses: list[dict]) -> bytes:
    """`courses` : [{"name", "folder", "quizzes": [quiz…], "cards": [carte…]}] → contenu du .zip."""
    import io
    import json
    import zipfile

    out = io.BytesIO()
    index = {"format": "pirouette-paquet", "version": 1, "courses": []}
    used: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for course in courses:
            folder = base = _file_name(course["name"])
            n = 2
            while folder.lower() in used:  # deux cours du même nom
                folder, n = f"{base} ({n})", n + 1
            used.add(folder.lower())
            entry = {"name": course["name"], "folder": course.get("folder") or "", "quizzes": [], "cards": ""}
            names: set[str] = set()
            for quiz in course["quizzes"]:
                title = quiz.get("custom_title") or quiz.get("title") or "Quiz"
                name = stem = f"Quiz - {_file_name(title)}"
                n = 2
                while name.lower() in names:
                    name, n = f"{stem} ({n})", n + 1
                names.add(name.lower())
                path = f"{folder}/{name}.txt"
                archive.writestr(path, quiz_to_text(quiz, course["name"]))
                entry["quizzes"].append(path)
            if course["cards"]:
                entry["cards"] = f"{folder}/Flashcards.txt"
                archive.writestr(entry["cards"], cards_to_text(course["cards"], course["name"]))
            index["courses"].append(entry)
        archive.writestr(PACK_INDEX, json.dumps(index, ensure_ascii=False, indent=2))
    return out.getvalue()


def read_pack(data: bytes) -> list[dict]:
    """Contenu d'un paquet : [{"name", "folder", "quizzes": [(nom de fichier, texte)], "cards": texte}].
    Sans index (zip fait à la main) : un cours par dossier du zip. ValueError si ce n'est pas un zip lisible."""
    import io
    import json
    import zipfile

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("Ce fichier n'est pas un paquet Pirouette (.zip).") from exc

    def text(path: str) -> str:
        raw = archive.read(path)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    names = [n for n in archive.namelist() if not n.endswith("/") and "__MACOSX" not in n]
    if PACK_INDEX in names:
        index = json.loads(text(PACK_INDEX))
        return [{"name": c["name"], "folder": c.get("folder") or "",
                 "quizzes": [(p.rsplit("/", 1)[-1], text(p)) for p in c.get("quizzes", []) if p in names],
                 "cards": text(c["cards"]) if c.get("cards") in names else ""}
                for c in index.get("courses", [])]
    courses: dict[str, dict] = {}
    for path in names:
        if not path.lower().endswith((".txt", ".csv", ".tsv", ".md")):
            continue
        folder = path.rsplit("/", 1)[0] if "/" in path else "Cours importé"
        course = courses.setdefault(folder, {"name": folder.rsplit("/", 1)[-1], "folder": "", "quizzes": [], "cards": ""})
        body = text(path)
        if parse(body).get("kind") == "cards":
            course["cards"] += ("\n" if course["cards"] else "") + body
        else:
            course["quizzes"].append((path.rsplit("/", 1)[-1], body))
    return list(courses.values())


def name_key(name: str) -> str:
    """Nom comparable : sans accents, casse ni ponctuation (« Neuro-sciences » ≈ « neurosciences »)."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def best_match(name: str, candidates: list[dict]) -> dict | None:
    """Le cours existant qui porte ce nom (ou un nom très proche), sinon None."""
    from difflib import SequenceMatcher

    key = name_key(name)
    scored = [(SequenceMatcher(None, key, name_key(c["name"])).ratio(), c) for c in candidates]
    scored = [(score, c) for score, c in scored if score >= 0.85]
    return max(scored, key=lambda s: s[0])[1] if scored else None
