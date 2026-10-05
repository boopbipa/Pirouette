"""Pirouette dans l'app Claude (Claude Desktop), via MCP.

L'app Claude pour Mac sait se brancher sur des outils locaux (protocole MCP) : elle lance `Pirouette --mcp` et
lui parle en JSON-RPC par l'entrée et la sortie standard. Claude peut alors lister tes cours, lire un chapitre,
et enregistrer dans Pirouette les quiz et les flashcards qu'il écrit — avec ton abonnement Claude, sans clé API.

Tout ce que Claude envoie passe par les mêmes contrôles que les quiz de l'IA locale : chaque question doit citer
une phrase du cours (« source ») et sa réponse doit venir du cours ; les questions trop faciles et les doublons
sont écartés, et Claude reçoit la raison pour corriger.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import datetime

from . import __version__
from . import figures as figures_module
from .chapters import MAX_CHAPTERS, build_chapters, chapter_text
from .grounding import Grounding, is_logistics
from .quiz import QUESTION_TYPES, SYSTEM_PROMPT, giveaway, normalize_question
from .revision import is_duplicate, normalize_cards

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PART_CHARS = 40_000   # texte du cours renvoyé par appel (le reste avec « partie »)
MAX_QUESTIONS = 40
MAX_CARDS = 60
MAX_FIGURES = 4

INSTRUCTIONS = """Pirouette est l'app de révision de l'étudiant : ses cours (PDF, Word, Pages…) découpés en chapitres,
ses quiz et ses flashcards. Pour créer un quiz ou des flashcards : 1) pirouette_cours pour trouver le cours et les
chapitres, 2) pirouette_lire pour lire le texte des chapitres voulus, 3) pirouette_creer_quiz ou
pirouette_ajouter_cartes. Un fichier « pas encore découpé » se découpe d'abord avec pirouette_decouper. Si la demande donne déjà l'identifiant du cours et les clés des chapitres (demande copiée
depuis Pirouette), saute l'étape 1 et ne lis que ces chapitres : c'est plus rapide et bien moins coûteux. Tout doit
venir du texte du cours, avec une citation exacte (« source »). Le texte signale les schémas et images par
« [Figure …] » : ne les regarde (pirouette_figure) que si le texte ne suffit pas ; une question qui a besoin de la
figure la cite dans « figure » (Pirouette l'affichera). Écris en français sauf demande contraire, tutoie
l'étudiant, et termine par un résumé court."""

QUESTION_RULES = SYSTEM_PROMPT.split("Règles :", 1)[1].rsplit("- Réponds uniquement", 1)[0].strip()


def _chapters_prop(what: str) -> dict:
    return {"type": "array", "items": {"type": "string"},
            "description": f"Clés des chapitres {what} (données par pirouette_cours). Vide : tout le cours."}


TOOLS = [
    {
        "name": "pirouette_cours",
        "description": "Liste les semestres et les cours de l'étudiant dans Pirouette, avec pour chaque cours son "
                       "identifiant, ses chapitres (et leur clé), ses quiz et ses flashcards.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "pirouette_lire",
        "description": "Renvoie le texte d'un cours (ou de certains chapitres), tel que Pirouette l'a extrait des "
                       "fichiers. Long cours : il arrive en plusieurs parties (paramètre « partie »).",
        "inputSchema": {"type": "object", "properties": {
            "cours": {"type": "string", "description": "Identifiant du cours (pirouette_cours)."},
            "chapitres": _chapters_prop("à lire"),
            "partie": {"type": "integer", "minimum": 1, "description": "Partie à lire (1 par défaut)."},
        }, "required": ["cours"], "additionalProperties": False},
    },
    {
        "name": "pirouette_decouper",
        "description": "Découpe en chapitres un fichier du cours qui ne l'est pas encore (pirouette_cours l'indique « pas "
                       "encore découpé »). Lis d'abord le fichier (pirouette_lire avec sa clé), repère ses grandes parties "
                       "(chapitres, parties, CM : pas chaque sous-titre), puis donne pour chacune son titre et sa ligne de "
                       "début recopiée mot pour mot depuis le texte. Pirouette renvoie la clé de chaque chapitre, à "
                       "utiliser ensuite dans « chapitres ». Un fichier déjà découpé garde ses chapitres.",
        "inputSchema": {"type": "object", "properties": {
            "cours": {"type": "string", "description": "Identifiant du cours."},
            "fichier": {"type": "string", "description": "Clé du fichier à découper (donnée par pirouette_cours)."},
            "chapitres": {"type": "array", "minItems": 2, "maxItems": MAX_CHAPTERS, "items": {
                "type": "object",
                "properties": {
                    "titre": {"type": "string", "description": "Titre court du chapitre."},
                    "debut": {"type": "string", "description": "La ligne du texte où il commence (son titre dans le "
                                                               "cours), recopiée mot pour mot."},
                },
                "required": ["titre", "debut"],
            }, "description": "Les chapitres, dans l'ordre du cours."},
        }, "required": ["cours", "fichier", "chapitres"], "additionalProperties": False},
    },
    {
        "name": "pirouette_creer_quiz",
        "description": "Enregistre un quiz dans Pirouette (l'étudiant le passe ensuite dans Réviser). Chaque question "
                       "est vérifiée : sa « source » doit être une phrase du cours (des chapitres indiqués) et sa réponse "
                       "doit venir du cours ; les questions refusées sont renvoyées avec la raison. Pour compléter un "
                       "quiz déjà enregistré (remplacer des questions refusées, en ajouter), passe son identifiant dans "
                       "« quiz » : les questions s'y ajoutent au lieu de créer un nouveau quiz.\n\nRègles pour les "
                       "questions :\n" + QUESTION_RULES,
        "inputSchema": {"type": "object", "properties": {
            "cours": {"type": "string", "description": "Identifiant du cours."},
            "titre": {"type": "string", "description": "Titre court du quiz (ex. le nom du chapitre)."},
            "quiz": {"type": "string", "description": "Identifiant d'un quiz existant à compléter (donné quand un quiz "
                                                       "est enregistré). Vide : nouveau quiz."},
            "chapitres": _chapters_prop("sur lesquels porte le quiz"),
            "questions": {"type": "array", "maxItems": MAX_QUESTIONS, "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(QUESTION_TYPES)},
                    "kind": {"type": "string", "enum": ["cours", "reflexion"],
                             "description": "« cours » : définition, terme à retrouver ; « reflexion » : le reste."},
                    "question": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "source": {"type": "string", "description": "La phrase du cours, recopiée mot pour mot."},
                    "figure": {"type": "string", "description": "Facultatif : repère d'une figure (« f1a2b3c4:12 ») "
                                                                "que Pirouette affichera avec la question, si elle en "
                                                                "a besoin (schéma à légender…)."},
                },
                "required": ["type", "question", "choices", "answer", "explanation", "source"],
            }},
        }, "required": ["cours", "titre", "questions"], "additionalProperties": False},
    },
    {
        "name": "pirouette_ajouter_cartes",
        "description": "Ajoute des flashcards à un cours dans Pirouette. Recto : une question ou un terme ; verso : "
                       "la réponse, courte, avec les mots du cours ; source : la phrase du cours recopiée mot pour mot. "
                       "Les cartes qui ne viennent pas du cours ou qui existent déjà sont refusées (avec la raison).",
        "inputSchema": {"type": "object", "properties": {
            "cours": {"type": "string", "description": "Identifiant du cours."},
            "chapitres": _chapters_prop("d'où viennent les cartes"),
            "cartes": {"type": "array", "maxItems": MAX_CARDS, "items": {
                "type": "object",
                "properties": {"recto": {"type": "string"}, "verso": {"type": "string"}, "source": {"type": "string"}},
                "required": ["recto", "verso", "source"],
            }},
        }, "required": ["cours", "cartes"], "additionalProperties": False},
    },
    {
        "name": "pirouette_figure",
        "description": "Montre une ou plusieurs figures du cours (schéma, image, graphique, tableau), repérées dans le "
                       "texte de pirouette_lire par « [Figure …] ». Chaque image coûte cher : ne la demande que si le "
                       "texte autour ne suffit pas (schéma à légender, graphique à lire, étapes d'un cycle…). Pour un "
                       "PDF, la figure est la page entière.",
        "inputSchema": {"type": "object", "properties": {
            "cours": {"type": "string", "description": "Identifiant du cours."},
            "figures": {"type": "array", "minItems": 1, "maxItems": MAX_FIGURES, "items": {"type": "string"},
                        "description": "Les repères, sans les crochets : « f1a2b3c4:12 »."},
        }, "required": ["cours", "figures"], "additionalProperties": False},
    },
    {
        "name": "pirouette_difficultes",
        "description": "Les flashcards que l'étudiant oublie le plus et les questions de quiz qu'il rate, pour lui "
                       "réexpliquer ces notions ou lui proposer un exercice ciblé.",
        "inputSchema": {"type": "object", "properties": {
            "cours": {"type": "string", "description": "Identifiant d'un cours (sinon : tous les cours en cours)."},
        }, "additionalProperties": False},
    },
]


class ToolError(Exception):
    """Erreur expliquée à Claude (mauvais identifiant, rien de valable…)."""


class Pirouette:
    """Les outils, sur les données de Pirouette."""

    def __init__(self, store):
        self.store = store

    def _course(self, course_id: str) -> dict:
        try:
            return self.store.get_course(str(course_id))
        except Exception:
            raise ToolError(f"Cours introuvable : « {course_id} ». Utilise pirouette_cours pour voir les identifiants.")

    def _sources(self, course: dict, chapters: list[str] | None, reading: bool = False) -> list[tuple[str, str]]:
        """Les textes (nom, texte) des chapitres voulus. Pour la lecture par Claude (reading), le texte est allégé
        (en-têtes répétés, numéros de page) et marque les figures ; pour vérifier les citations, il reste tel quel."""
        wanted = {str(c) for c in chapters or [] if str(c)}
        sources = []
        for f in course["files"]:
            text = self.store.file_text(course["id"], f["id"])
            prepare = self._reader(course["id"], f["id"], text) if reading else (lambda body: body)
            if not wanted or f["id"] in wanted:
                sources.append((f["name"], prepare(text)))
                continue
            chapters_list = f.get("chapters") or []
            for position, chapter in enumerate(chapters_list):
                if f"{f['id']}-{position}" in wanted:
                    sources.append((f"{f['name']} — {chapter['title']}", prepare(chapter_text(text, chapters_list, position))))
        if not course["files"]:
            raise ToolError("Ce cours n'a pas encore de fichier : l'étudiant doit d'abord y importer son cours.")
        if not sources:
            raise ToolError("Aucun de ces chapitres n'existe dans ce cours : vérifie les clés avec pirouette_cours.")
        return sources

    def _reader(self, course_id: str, file_id: str, text: str):
        repeated = figures_module.boilerplate(text)
        pages = self.store.figure_index(course_id, file_id).get("pages", {})
        return lambda body: figures_module.add_markers(figures_module.compact(body, repeated), file_id, pages)

    def _meta(self, course: dict, sources: list, chapters: list[str] | None) -> dict:
        scope = [name.split(" — ", 1)[-1] for name, _ in sources] if chapters else []
        return {"provider": "claude-app", "model": "Claude (app)", "language": "français", "course_id": course["id"],
                "course_name": course["name"], "course_version": course["version"],
                "sources": [name for name, _ in sources], "scope": scope}

    # ---- Outils ----
    def pirouette_cours(self) -> str:
        folders = {f["id"]: f for f in self.store.list_folders()}
        courses = self.store.list_courses()
        if not courses:
            return "Aucun cours pour l'instant : l'étudiant doit en créer un dans Pirouette (Mes cours → + Nouveau cours)."
        lines = []
        groups = [(f, [c for c in courses if c.get("folder_id") == fid]) for fid, f in folders.items()]
        groups.append((None, [c for c in courses if c.get("folder_id") not in folders]))
        for folder, members in groups:
            if not members:
                continue
            title = f"Semestre « {folder['name']} »" + (" (archivé)" if folder.get("archived") else "") if folder else "Sans semestre"
            lines.append(f"## {title}")
            for c in members:
                lines.append(f"- {c['name']} — identifiant {c['id']} — {c['quiz_count']} quiz, {c['card_count']} flashcards")
                for f in c["files"]:
                    if f.get("chapters_by") == "none":
                        lines.append(f"    - fichier « {f['name']} » (pas encore découpé en chapitres : pirouette_decouper) "
                                     f"— clé {f['id']}")
                    elif f.get("chapters"):
                        for position, chapter in enumerate(f["chapters"]):
                            lines.append(f"    - chapitre « {chapter['title']} » — clé {f['id']}-{position}")
                    else:
                        lines.append(f"    - fichier « {f['name']} » (sans chapitres) — clé {f['id']}")
                if not c["files"]:
                    lines.append("    - (pas encore de fichier)")
        return "\n".join(lines)

    def pirouette_lire(self, cours: str, chapitres: list[str] | None = None, partie: int = 1) -> str:
        course = self._course(cours)
        text = "\n\n".join(f"=== {name} ===\n{body}" for name, body in self._sources(course, chapitres, reading=True))
        parts = max(1, -(-len(text) // PART_CHARS))
        partie = min(max(1, int(partie or 1)), parts)
        chunk = text[(partie - 1) * PART_CHARS:partie * PART_CHARS]
        more = f"\n\n[Partie {partie}/{parts} — la suite avec partie={partie + 1}]" if partie < parts else ""
        head = f"Cours « {course['name']} »" + (f", partie {partie}/{parts}" if parts > 1 else "") + " :\n\n"
        return head + chunk + more

    def pirouette_decouper(self, cours: str, fichier: str, chapitres: list) -> str:
        course = self._course(cours)
        entry = next((f for f in course["files"] if f["id"] == str(fichier).strip()), None)
        if entry is None:
            raise ToolError(f"Fichier introuvable : « {fichier} ». Utilise la clé du fichier donnée par pirouette_cours.")
        if entry.get("chapters_by") != "none":
            keys = ", ".join(f"{entry['id']}-{i} ({c['title']})" for i, c in enumerate(entry.get("chapters") or []))
            raise ToolError(f"« {entry['name']} » est déjà découpé : garde ses chapitres"
                            + (f" — {keys}." if keys else " (gardé d'un seul bloc par l'étudiant : utilise la clé du fichier)."))
        lines = self.store.file_text(course["id"], entry["id"]).split("\n")
        flat = [_flat(line) for line in lines]
        starts, missing, after = [], [], -1
        for item in chapitres[:MAX_CHAPTERS]:
            if not isinstance(item, dict):
                continue
            title, start = str(item.get("titre", "")).strip()[:120], _flat(str(item.get("debut", "")))
            index = _find_line(flat, start, after) if start else None
            if index is None:
                missing.append(title or str(item.get("debut", ""))[:80])
                continue
            starts.append((index, title or lines[index].strip()[:120]))
            after = index
        if missing:
            raise ToolError("Lignes de début introuvables dans le texte (recopie-les mot pour mot, dans l'ordre du cours) : "
                            + " ; ".join(f"« {m} »" for m in missing) + ". Rien n'a été enregistré.")
        found = build_chapters(lines, starts)
        if not found:
            raise ToolError("Il faut au moins deux chapitres distincts. Rien n'a été enregistré.")
        self.store.set_chapters(course["id"], entry["id"], found, "ai", revision=entry["revisions"])
        keys = "\n".join(f"- {entry['id']}-{i} : {c['title']}" for i, c in enumerate(found))
        return (f"« {entry['name']} » découpé en {len(found)} chapitres (l'étudiant les voit dans Pirouette). "
                f"Clés à utiliser dans « chapitres » :\n{keys}")

    def pirouette_creer_quiz(self, cours: str, titre: str, questions: list, chapitres: list[str] | None = None,
                             quiz: str = "") -> str:
        course = self._course(cours)
        target = None
        if quiz:
            try:
                target = self.store.get_quiz(str(quiz))
            except Exception:
                raise ToolError(f"Quiz introuvable : « {quiz} ».")
            if target.get("course_id") != course["id"]:
                raise ToolError("Ce quiz appartient à un autre cours.")
            chapitres = chapitres or None
        sources = self._sources(course, chapitres)
        grounding = Grounding("\n\n".join(body for _, body in sources))
        previous = [{"front": q["question"], "back": "" if q["type"] == "vrai_faux" else q["answer"]}
                    for summary in self.store.list_quizzes(course["id"])
                    for q in self.store.get_quiz(summary["id"]).get("questions", [])]
        kept, refused, seen = [], [], set()
        for number, raw in enumerate(questions[:MAX_QUESTIONS], start=1):
            question = normalize_question(raw, list(QUESTION_TYPES)) if isinstance(raw, dict) else None
            label = str((raw or {}).get("question", ""))[:120] if isinstance(raw, dict) else ""
            if question is None:
                refused.append((number, label, "format invalide (type, propositions, ou réponse absente des propositions)"))
                continue
            key = "".join(ch for ch in question["question"].lower() if ch.isalnum())
            notion = {"front": question["question"], "back": "" if question["type"] == "vrai_faux" else question["answer"]}
            if key in seen:
                refused.append((number, label, "en double dans ce quiz"))
            elif not grounding.quote_found(question["source"]):
                refused.append((number, label, "la « source » n'est pas une phrase du cours (recopie-la mot pour mot)"))
            elif question["type"] != "vrai_faux" and not grounding.words_found(question["answer"]):
                refused.append((number, label, "la réponse ne vient pas du cours"))
            elif is_logistics(question["source"], question["question"], question["answer"]):
                refused.append((number, label, "organisation du cours (notes, examens, calendrier…), rien à apprendre"))
            elif giveaway(question):
                refused.append((number, label, "trop facile : l'énoncé donne la réponse"))
            elif is_duplicate(**notion, others=previous):
                refused.append((number, label, "déjà posée dans un autre quiz de ce cours"))
            else:
                if raw.get("figure"):
                    try:
                        entry, figure_key = self._figure(course, raw["figure"])
                        question["figure"] = f"{entry['id']}:{figure_key}"
                    except ToolError:
                        pass  # repère inconnu : la question reste, sans image
                seen.add(key)
                previous.append(notion)
                kept.append(question)
        report = "\n".join(f"- question {n} « {label} » : {why}" for n, label, why in refused)
        if not kept:
            raise ToolError("Aucune question retenue.\n" + report)
        new_quiz = {"title": str(titre).strip()[:120] or "Quiz", "questions": kept, "difficulty": "moyen",
                    **self._meta(course, sources, chapitres)}
        key = self.store.chapter_key(new_quiz)
        existing = target or (self.store.chapter_quiz(key) if key else None)
        if existing:  # quiz demandé, ou déjà créé sur ces chapitres : on l'alimente
            merged, added = self.store.add_to_quiz(existing["id"], kept)
            quiz_id = merged["id"]
            done = f"{added} question{'s' if added > 1 else ''} ajoutée{'s' if added > 1 else ''} au quiz « {merged['title']} » " \
                   f"({len(merged['questions'])} questions en tout)."
        else:
            quiz_id = self.store.save_quiz(new_quiz)["id"]
            done = f"Quiz « {new_quiz['title']} » enregistré dans Pirouette : {len(kept)} question{'s' if len(kept) > 1 else ''}."
        done += f" Identifiant du quiz : {quiz_id}. L'étudiant le trouve dans Réviser › {course['name']}."
        return done + (f"\n\nQuestions refusées ({len(refused)}) — corrige-les (sans le demander à l'étudiant) et renvoie-les "
                       f"avec quiz=\"{quiz_id}\" : elles s'ajouteront à ce quiz.\n{report}" if refused else "")

    def pirouette_ajouter_cartes(self, cours: str, cartes: list, chapitres: list[str] | None = None) -> str:
        course = self._course(cours)
        sources = self._sources(course, chapitres)
        grounding = Grounding("\n\n".join(body for _, body in sources))
        deck = self.store.get_doc(course["id"], "cards") or {"cards": []}
        items = [{"front": c.get("recto", ""), "back": c.get("verso", ""), "source": c.get("source", "")}
                 for c in cartes[:MAX_CARDS] if isinstance(c, dict)]
        rejected: list[str] = []
        new = normalize_cards([{"cards": items}], MAX_CARDS, deck["cards"], grounding, rejected)
        meta = self._meta(course, sources, chapitres)
        scope = meta["scope"]
        for card in new:
            # `origin` : « fichier — chapitre », pour retrouver le cours (fichier) de la carte dans la matière
            card.update(scope=scope, origin=meta["sources"], created_at=datetime.now().isoformat(timespec="seconds"))
        if new:
            deck = self.store.get_doc(course["id"], "cards") or {"cards": []}
            deck.update(course_name=course["name"], course_id=course["id"], cards=[*deck["cards"], *new])
            self.store.save_doc(course["id"], "cards", deck)
        duplicates = len(items) - len(new) - len(rejected)
        lines = [f"{len(new)} flashcard{'s' if len(new) > 1 else ''} ajoutée{'s' if len(new) > 1 else ''} au cours « {course['name']} »."]
        if rejected:
            lines.append("Refusées, pas tirées du cours (la « source » doit être une phrase du cours recopiée mot pour mot) : "
                         + " ; ".join(f"« {r[:80]} »" for r in rejected))
        if duplicates > 0:
            lines.append(f"{duplicates} carte{'s' if duplicates > 1 else ''} ignorée{'s' if duplicates > 1 else ''} : vide ou déjà présente.")
        if not new:
            raise ToolError("\n".join(lines))
        return "\n".join(lines)

    def _figure(self, course: dict, ref: str) -> tuple[dict, str]:
        """Le fichier et la clé d'un repère « fichier:clé » ; ToolError s'il n'existe pas."""
        file_id, _, key = str(ref).strip().strip("[]").removeprefix("Figure ").partition(":")
        entry = next((f for f in course["files"] if f["id"] == file_id), None)
        pages = self.store.figure_index(course["id"], file_id).get("pages", {}) if entry else {}
        if not any(key in keys for keys in pages.values()):
            raise ToolError(f"Figure introuvable : « {ref} ». Utilise un repère « [Figure …] » donné par pirouette_lire.")
        return entry, key

    def pirouette_figure(self, cours: str, figures: list) -> list:
        course = self._course(cours)
        content = []
        for ref in list(figures)[:MAX_FIGURES]:
            entry, key = self._figure(course, ref)
            name, data = self.store.original_file(course["id"], entry["id"])
            try:
                image, mime = figures_module.render(name, data, key)
            except ValueError as exc:
                content.append({"type": "text", "text": f"Figure {ref} : impossible de l'afficher ({exc})."})
                continue
            where = f"page {key}" if "." not in key else f"diapo {key.split('.')[0]}"
            content.append({"type": "text", "text": f"Figure {ref} — « {entry['name']} », {where} :"})
            content.append({"type": "image", "data": base64.b64encode(image).decode(), "mimeType": mime})
        return content

    def pirouette_difficultes(self, cours: str = "") -> str:
        ids = [self._course(cours)["id"]] if cours else self.store.active_course_ids()
        cards = self.store.weak_cards(ids)[:15]
        questions = self.store.weak_questions(ids)[:10]
        if not cards and not questions:
            return "Rien de difficile pour l'instant : pas de carte souvent oubliée ni de question ratée."
        lines = []
        if cards:
            lines.append("Flashcards les plus oubliées :")
            lines += [f"- [{e['course']['name']}] {e['card']['front']} → {e['card']['back']} "
                      f"(oubliée {e['card'].get('lapses', 0)} fois)" for e in cards]
        if questions:
            lines.append("Questions de quiz ratées :")
            for e in questions:
                q = e["quiz"]["questions"][e["index"]]
                lines.append(f"- [{e['quiz'].get('course_name', '')}] {q['question']} → {q['answer']} "
                             f"(ratée {e['stat']['wrong']} fois, réussie {e['stat']['right']} fois)")
        return "\n".join(lines)


def _flat(text: str) -> str:
    """Une ligne réduite à ses lettres et chiffres, en minuscules (espaces, puces, # ou ponctuation ne comptent pas)."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _find_line(flat: list[str], start: str, after: int) -> int | None:
    """La première ligne après `after` qui est ce début (ou qui commence par lui, s'il est assez long)."""
    for index in range(after + 1, len(flat)):
        if flat[index] == start:
            return index
    if len(start) >= 8:
        for index in range(after + 1, len(flat)):
            if flat[index].startswith(start) or (len(flat[index]) >= 8 and start.startswith(flat[index])):
                return index
    return None


# ---------- Protocole (JSON-RPC 2.0 sur l'entrée / la sortie standard, un message par ligne) ----------

def handle(tools: Pirouette, message: dict) -> dict | None:
    """Réponse à un message du client MCP (None pour une notification)."""
    method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if msg_id is None:
        return None  # notifications (initialized, cancelled…) : rien à répondre

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "pirouette", "title": "Pirouette", "version": __version__},
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": f"Outil inconnu : {name}"}}
        try:
            result, error = getattr(tools, name)(**args), False
        except ToolError as exc:
            result, error = str(exc), True
        except TypeError as exc:
            result, error = f"Paramètres invalides : {exc}", True
        except Exception as exc:  # une erreur inattendue ne doit pas couper la conversation
            result, error = f"Erreur de Pirouette : {exc}", True
        content = result if isinstance(result, list) else [{"type": "text", "text": result}]
        return ok({"content": content, "isError": error})
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Méthode inconnue : {method}"}}


def serve(store, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    tools = Pirouette(store)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}}
        else:
            reply = handle(tools, message) if isinstance(message, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()
