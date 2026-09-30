"""Pirouette dans l'app Claude (Claude Desktop), via MCP.

L'app Claude pour Mac sait se brancher sur des outils locaux (protocole MCP) : elle lance `Pirouette --mcp` et
lui parle en JSON-RPC par l'entrée et la sortie standard. Claude peut alors lister tes cours, lire un chapitre,
et enregistrer dans Pirouette les quiz et les flashcards qu'il écrit — avec ton abonnement Claude, sans clé API.

Tout ce que Claude envoie passe par les mêmes contrôles que les quiz de l'IA locale : chaque question doit citer
une phrase du cours (« source ») et sa réponse doit venir du cours ; les questions trop faciles et les doublons
sont écartés, et Claude reçoit la raison pour corriger.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from . import __version__
from .chapters import chapter_text
from .grounding import Grounding, is_logistics
from .quiz import QUESTION_TYPES, SYSTEM_PROMPT, giveaway, normalize_question
from .revision import is_duplicate, normalize_cards

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PART_CHARS = 40_000   # texte du cours renvoyé par appel (le reste avec « partie »)
MAX_QUESTIONS = 40
MAX_CARDS = 60

INSTRUCTIONS = """Pirouette est l'app de révision de l'étudiant : ses cours (PDF, Word, Pages…) découpés en chapitres,
ses quiz et ses flashcards. Pour créer un quiz ou des flashcards : 1) pirouette_cours pour trouver le cours et les
chapitres, 2) pirouette_lire pour lire le texte des chapitres voulus, 3) pirouette_creer_quiz ou
pirouette_ajouter_cartes. Tout doit venir du texte du cours, avec une citation exacte (« source »). Écris en
français sauf demande contraire, et tutoie l'étudiant."""

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
        "name": "pirouette_creer_quiz",
        "description": "Enregistre un quiz dans Pirouette (l'étudiant le passe ensuite dans Réviser). Chaque question "
                       "est vérifiée : sa « source » doit être une phrase du cours (des chapitres indiqués) et sa réponse "
                       "doit venir du cours ; les questions refusées sont renvoyées avec la raison, pour les corriger "
                       "et les renvoyer dans un nouvel appel.\n\nRègles pour les questions :\n" + QUESTION_RULES,
        "inputSchema": {"type": "object", "properties": {
            "cours": {"type": "string", "description": "Identifiant du cours."},
            "titre": {"type": "string", "description": "Titre court du quiz (ex. le nom du chapitre)."},
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

    def _sources(self, course: dict, chapters: list[str] | None) -> list[tuple[str, str]]:
        wanted = {str(c) for c in chapters or [] if str(c)}
        sources = []
        for f in course["files"]:
            text = self.store.file_text(course["id"], f["id"])
            if not wanted or f["id"] in wanted:
                sources.append((f["name"], text))
                continue
            chapters_list = f.get("chapters") or []
            for position, chapter in enumerate(chapters_list):
                if f"{f['id']}-{position}" in wanted:
                    sources.append((f"{f['name']} — {chapter['title']}", chapter_text(text, chapters_list, position)))
        if not course["files"]:
            raise ToolError("Ce cours n'a pas encore de fichier : l'étudiant doit d'abord y importer son cours.")
        if not sources:
            raise ToolError("Aucun de ces chapitres n'existe dans ce cours : vérifie les clés avec pirouette_cours.")
        return sources

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
                    if f.get("chapters"):
                        for position, chapter in enumerate(f["chapters"]):
                            lines.append(f"    - chapitre « {chapter['title']} » — clé {f['id']}-{position}")
                    else:
                        lines.append(f"    - fichier « {f['name']} » (sans chapitres) — clé {f['id']}")
                if not c["files"]:
                    lines.append("    - (pas encore de fichier)")
        return "\n".join(lines)

    def pirouette_lire(self, cours: str, chapitres: list[str] | None = None, partie: int = 1) -> str:
        course = self._course(cours)
        text = "\n\n".join(f"=== {name} ===\n{body}" for name, body in self._sources(course, chapitres))
        parts = max(1, -(-len(text) // PART_CHARS))
        partie = min(max(1, int(partie or 1)), parts)
        chunk = text[(partie - 1) * PART_CHARS:partie * PART_CHARS]
        more = f"\n\n[Partie {partie}/{parts} — la suite avec partie={partie + 1}]" if partie < parts else ""
        head = f"Cours « {course['name']} »" + (f", partie {partie}/{parts}" if parts > 1 else "") + " :\n\n"
        return head + chunk + more

    def pirouette_creer_quiz(self, cours: str, titre: str, questions: list, chapitres: list[str] | None = None) -> str:
        course = self._course(cours)
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
                seen.add(key)
                previous.append(notion)
                kept.append(question)
        report = "\n".join(f"- question {n} « {label} » : {why}" for n, label, why in refused)
        if not kept:
            raise ToolError("Aucune question retenue.\n" + report)
        quiz = {"title": str(titre).strip()[:120] or "Quiz", "questions": kept, "difficulty": "moyen",
                **self._meta(course, sources, chapitres)}
        key = self.store.chapter_key(quiz)
        existing = self.store.chapter_quiz(key) if key else None
        if existing:  # un quiz existe déjà sur ces chapitres : on l'alimente
            merged, added = self.store.add_to_quiz(existing["id"], kept)
            done = f"{added} question{'s' if added > 1 else ''} ajoutée{'s' if added > 1 else ''} au quiz « {merged['title']} » " \
                   f"(déjà créé sur ces chapitres, {len(merged['questions'])} questions en tout)."
        else:
            self.store.save_quiz(quiz)
            done = f"Quiz « {quiz['title']} » enregistré dans Pirouette : {len(kept)} question{'s' if len(kept) > 1 else ''}."
        done += f" L'étudiant le trouve dans Réviser › {course['name']} (et dans Mes cours › {course['name']} › Quiz)."
        return done + (f"\n\nQuestions refusées ({len(refused)}) — corrige-les et renvoie-les dans un nouveau quiz si tu veux :\n{report}"
                       if refused else "")

    def pirouette_ajouter_cartes(self, cours: str, cartes: list, chapitres: list[str] | None = None) -> str:
        course = self._course(cours)
        sources = self._sources(course, chapitres)
        grounding = Grounding("\n\n".join(body for _, body in sources))
        deck = self.store.get_doc(course["id"], "cards") or {"cards": []}
        items = [{"front": c.get("recto", ""), "back": c.get("verso", ""), "source": c.get("source", "")}
                 for c in cartes[:MAX_CARDS] if isinstance(c, dict)]
        rejected: list[str] = []
        new = normalize_cards([{"cards": items}], MAX_CARDS, deck["cards"], grounding, rejected)
        scope = self._meta(course, sources, chapitres)["scope"]
        for card in new:
            card.update(scope=scope, created_at=datetime.now().isoformat(timespec="seconds"))
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
            text, error = getattr(tools, name)(**args), False
        except ToolError as exc:
            text, error = str(exc), True
        except TypeError as exc:
            text, error = f"Paramètres invalides : {exc}", True
        except Exception as exc:  # une erreur inattendue ne doit pas couper la conversation
            text, error = f"Erreur de Pirouette : {exc}", True
        return ok({"content": [{"type": "text", "text": text}], "isError": error})
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
