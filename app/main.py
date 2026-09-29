"""Serveur de Pirouette : cours, quiz, fiches, flashcards et définitions."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import threading
import urllib.parse
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from . import backup, claude_desktop, exchange, local_ai, plan as plans, reminder, srs, updater  # noqa: E402
from .grounding import Grounding  # noqa: E402
from .jobs import Jobs  # noqa: E402
from .chapters import (CHAPTERS_SCHEMA, CHAPTERS_SYSTEM, ai_candidates, build_chapters_prompt,  # noqa: E402
                       chapter_text, chapters_from_ai, detect_chapters)
from .definitions import RULE_IDS, analyse, definitions_for  # noqa: E402
from .explain import EXPLAIN_SCHEMA, EXPLAIN_SYSTEM, build_explain_prompt, course_excerpt  # noqa: E402
from .extract import EXTRACT_VERSION, ConversionError, UnsupportedFileError, extract_text  # noqa: E402
from .providers import claude_provider, ollama_provider  # noqa: E402
from .providers.base import ProviderError  # noqa: E402
from .focus import (MANUAL_SCHEMA, MANUAL_SYSTEM, best_chunk, build_manual_prompt, focus_text,  # noqa: E402
                    parse_manual_lines)
from .partiel import BATCH, GRADE_SCHEMA, GRADE_SYSTEM, build_grade_prompt, read_grades  # noqa: E402
from .quiz import COURSE_SHARES, QUESTION_TYPES, QuizOptions, assemble_quiz, chunk_text, normalize_question  # noqa: E402
from .revision import is_duplicate, normalize_cards  # noqa: E402
from .storage import NotFound, Store  # noqa: E402

ROOT = Path(__file__).resolve().parent
# Claude est mis de côté pour l'instant : seule l'IA locale est proposée. PIROUETTE_CLAUDE=1 le réactive.
CLAUDE_ENABLED = os.getenv("PIROUETTE_CLAUDE", "1") != "0"  # Claude (clé API) proposé à côté de l'IA locale
PROVIDERS = {"local": ollama_provider} | ({"claude": claude_provider} if CLAUDE_ENABLED else {})
MAX_TOP_UPS = 3  # demandes supplémentaires au plus quand il manque des questions ou des cartes

store = Store(Path(os.getenv("QUIZZ_DATA_DIR", ROOT.parent / "data")))
jobs = Jobs()


def _apply_settings() -> None:
    """La clé enregistrée dans les réglages de l'app a priorité sur celle du fichier .env."""
    settings = store.get_settings()
    key = settings.get("anthropic_api_key")
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
    ollama_provider.THINKING = bool(settings.get("local_thinking", False))
    ollama_provider.CONTEXT = settings.get("local_context") or None


_apply_settings()

app = FastAPI(title="Pirouette")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.exception_handler(NotFound)
async def not_found(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse({"detail": "Élément introuvable"}, status_code=404)


PALETTE_KEYS = ("accent", "ink", "soft", "d_accent", "d_ink", "d_soft")


def appearance_css(palette: dict | None) -> str:
    """Variables CSS de la couleur d'accent choisie (modes clair et sombre). Même calcul que paletteCss() côté page."""
    if not palette:
        return ""
    p = palette
    return (f":root{{--accent:{p['accent']};--accent-ink:{p['ink']};--accent-soft:{p['soft']}}}"
            f"@media (prefers-color-scheme: dark){{:root{{--accent:{p['d_accent']};"
            f"--accent-ink:{p['d_ink']};--accent-soft:{p['d_soft']}}}}}")


@app.get("/")
async def index() -> HTMLResponse:
    # La couleur choisie est insérée directement dans la page : pas de « flash » de la couleur par défaut.
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace("/*APPEARANCE*/", appearance_css(store.get_settings().get("appearance")))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/manifest.webmanifest")
async def manifest() -> FileResponse:
    # Servi à la racine pour que l'app installée (Dock) couvre tout le site.
    return FileResponse(ROOT / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(ROOT / "static" / "brand" / "icon-192.png", media_type="image/png")


# ---------- Profil et statistiques (tout reste sur cet ordinateur) ----------

class ProfileIn(BaseModel):
    name: str = ""


@app.get("/api/profile")
async def get_profile() -> dict:
    return store.get_profile()


@app.put("/api/profile")
async def save_profile(body: ProfileIn) -> dict:
    return store.save_profile(body.name)


class SettingsIn(BaseModel):
    name: str | None = None
    api_key: str | None = None  # "" pour supprimer la clé enregistrée
    local_thinking: bool | None = None  # laisser les modèles « qui réfléchissent » (qwen3…) réfléchir avant de répondre
    local_context: int | None = None    # mémoire de lecture de l'IA locale, en tokens ; 0 = automatique
    definition_rule: str | None = None  # comment repérer les définitions ; "auto" = deviner, "aucune" = ne pas repérer
    reminder_time: str | None = None    # rappel quotidien « HH:MM » (Mac) ; "" pour l'arrêter
    new_per_day: int | None = None      # nouvelles cartes par jour dans la révision du jour
    backup_mode: str | None = None      # sauvegarde automatique : "open" (à l'ouverture), "week", "off"


def _settings_view() -> dict:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    saved = store.get_settings().get("anthropic_api_key")
    return {
        "name": store.get_profile()["name"],
        "claude": {
            "configured": claude_provider.is_configured(),
            "saved_in_app": bool(saved),
            "hint": f"sk-ant-…{key[-4:]}" if claude_provider.is_configured() and len(key) > 8 else "",
            "model": claude_provider.default_model(),
        },
        "claude_enabled": CLAUDE_ENABLED,
        "local_thinking": bool(store.get_settings().get("local_thinking", False)),
        "definition_rule": store.get_settings().get("definition_rule", "auto"),
        "appearance": store.get_settings().get("appearance"),
        "version": updater.__version__,
        "reminder_time": store.get_settings().get("reminder_time", ""),
        "reminder_supported": reminder.supported(),
        "new_per_day": store.new_per_day(),
        "data_dir": str(store.root.resolve()),
        "backup_mode": store.get_settings().get("backup_mode", "week"),
        "backup_dir": str(backup.backup_dir(store)),
        "last_backup": store.get_settings().get("last_backup"),
        "desktop": os.getenv("PIROUETTE_DESKTOP") == "1",
    }


@app.get("/api/settings")
async def get_settings() -> dict:
    return _settings_view()


@app.put("/api/settings")
async def save_settings(body: SettingsIn) -> dict:
    if body.name is not None:
        store.save_profile(body.name)
    if body.local_context is not None:
        allowed = {c["tokens"] for c in local_ai.CONTEXTS}
        if body.local_context and body.local_context not in allowed:
            raise HTTPException(400, "Taille de contexte inconnue.")
        store.save_settings(local_context=body.local_context or None)
        ollama_provider.CONTEXT = body.local_context or None
    if body.definition_rule is not None:
        if body.definition_rule not in RULE_IDS | {"auto", "aucune"}:
            raise HTTPException(400, "Règle de définitions inconnue.")
        store.save_settings(definition_rule=None if body.definition_rule == "auto" else body.definition_rule)
    if body.reminder_time is not None:
        if body.reminder_time and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", body.reminder_time):
            raise HTTPException(400, "Heure invalide (ex. 19:00).")
        if not reminder.supported():
            raise HTTPException(400, "Le rappel quotidien fonctionne dans l'app Mac.")
        if body.reminder_time:
            hour, minute = map(int, body.reminder_time.split(":"))
            reminder.install(hour, minute)
        else:
            reminder.uninstall()
        store.save_settings(reminder_time=body.reminder_time or None)
    if body.new_per_day is not None:
        store.save_settings(new_per_day=max(0, min(body.new_per_day, 200)))
    if body.backup_mode is not None:
        if body.backup_mode not in backup.MODES:
            raise HTTPException(400, "Fréquence de sauvegarde inconnue.")
        store.save_settings(backup_mode=body.backup_mode)
    if body.local_thinking is not None:
        store.save_settings(local_thinking=body.local_thinking)
        ollama_provider.THINKING = body.local_thinking
    if body.api_key is not None:
        key = body.api_key.strip()
        if key and not key.startswith("sk-ant-"):
            raise HTTPException(400, "Une clé API Anthropic commence par « sk-ant- ».")
        store.save_settings(anthropic_api_key=key)
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)
    return _settings_view()


# ---------- Branchement à l'app Claude (MCP, voir app/mcp_server.py) ----------

def _claude_app_view() -> dict:
    return claude_desktop.status() | {"supported": sys.platform == "darwin" and os.getenv("PIROUETTE_DESKTOP") == "1"}


@app.get("/api/claude-app")
async def claude_app_status() -> dict:
    return _claude_app_view()


@app.post("/api/claude-app")
async def claude_app_install() -> dict:
    if not _claude_app_view()["supported"]:
        raise HTTPException(400, "Le branchement à l'app Claude se fait depuis l'app Pirouette pour Mac.")
    try:
        claude_desktop.install()
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _claude_app_view()


@app.delete("/api/claude-app")
async def claude_app_remove() -> dict:
    try:
        claude_desktop.uninstall()
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _claude_app_view()


@app.post("/api/backup")
async def backup_now() -> dict:
    try:
        folder = await run_in_threadpool(backup.make, store)
    except OSError as exc:
        raise HTTPException(500, f"La sauvegarde n'a pas pu se faire : {exc}") from exc
    return {"path": str(folder)} | _settings_view()


@app.post("/api/backup/open")
async def open_backups() -> dict:
    """Ouvre le dossier des sauvegardes dans le Finder (app Mac)."""
    folder = backup.backup_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(folder)], capture_output=True, timeout=10)
    return {"path": str(folder)}


class AppearanceIn(BaseModel):
    palette: dict | None = None  # None : revenir à la couleur par défaut


@app.put("/api/appearance")
async def save_appearance(body: AppearanceIn) -> dict:
    palette = body.palette
    if palette is not None:
        if set(palette) != set(PALETTE_KEYS) or not all(
                isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) for v in palette.values()):
            raise HTTPException(400, "Couleur invalide.")
        palette = {k: palette[k].upper() for k in PALETTE_KEYS}
    store.save_settings(appearance=palette)
    return {"appearance": palette, "css": appearance_css(palette)}


@app.post("/api/reminder/test")
async def test_reminder() -> dict:
    if not reminder.supported():
        raise HTTPException(400, "Le rappel quotidien fonctionne dans l'app Mac.")
    text = reminder.daily_text(store) or "Rien de prévu aujourd'hui : tout est à jour."
    return {"shown": reminder.notify(text), "message": text}


@app.post("/api/settings/test-claude")
async def test_claude() -> dict:
    try:
        name = await claude_provider.check_key()
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": f"Clé valide : accès à {name}."}


@app.get("/api/stats")
async def stats() -> dict:
    return store.stats() | {"next_exam": store.next_exam()}


@app.get("/api/review")
async def review_cards() -> list[dict]:
    """Les cartes à revoir de tous les cours (page Réviser)."""
    return store.review_cards()


# ---------- Assistant IA locale (Ollama) ----------

@app.get("/api/ollama/status")
async def ollama_status() -> dict:
    return await local_ai.status()


@app.post("/api/ollama/open")
async def ollama_open() -> dict:
    return {"opened": local_ai.open_ollama()}


class PullIn(BaseModel):
    model: str


@app.post("/api/ollama/pull")
async def ollama_pull(body: PullIn) -> StreamingResponse:
    if body.model not in {m["name"] for m in local_ai.MODELS}:
        raise HTTPException(400, "Modèle inconnu.")

    async def events():
        async for event in local_ai.pull(body.model):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/api/config")
async def config() -> dict:
    ollama_models = await ollama_provider.list_models()
    return {
        "default_provider": os.getenv("QUIZZ_DEFAULT_PROVIDER", "local") if CLAUDE_ENABLED else "local",
        "local": {
            "running": ollama_models is not None,
            "available": bool(ollama_models),
            "models": ollama_models or [],
            "default_model": ollama_provider.default_model(),
        },
        "claude": {
            "enabled": CLAUDE_ENABLED,
            "available": CLAUDE_ENABLED and claude_provider.is_configured(),
            "default_model": claude_provider.default_model(),
        },
    }


# ---------- Cours ----------

class CourseIn(BaseModel):
    name: str


@app.get("/api/courses")
async def list_courses() -> list[dict]:
    return store.list_courses()


@app.post("/api/courses")
async def create_course(body: CourseIn) -> dict:
    return store.create_course(body.name)


@app.get("/api/courses/{course_id}")
async def get_course(course_id: str) -> dict:
    for f in store.get_course(course_id)["files"]:
        # Fichiers lus avec une ancienne version de l'extraction : relus depuis l'original (gras, italique…).
        if f.get("extract_version", 1) < EXTRACT_VERSION:
            await run_in_threadpool(_reextract, course_id, f)
        # Fichiers déposés avant l'arrivée des chapitres : repérage automatique, une fois.
        elif "chapters" not in f:
            store.set_chapters(course_id, f["id"], detect_chapters(store.file_text(course_id, f["id"])), "auto")
    rule = store.get_settings().get("definition_rule")
    course = store.get_course(course_id)
    for f in course["files"]:
        f["definitions"] = len(definitions_for(store.file_text(course_id, f["id"]), rule))
    cards = (store.get_doc(course_id, "cards") or {}).get("cards", [])
    known = sum(c["status"] == "known" for c in cards)
    return course | {
        "quizzes": store.list_quizzes(course_id),
        "cards": {"total": len(cards), "known": known, "review": len(cards) - known},
        "exam": store.exam_info(course),
        "outdated": sum(len(v) for v in store.outdated_items(course_id).values()) if course["files"] else 0,
        "revision": {"today": len(store.today_cards([course_id])),
                     "weak": len(store.weak_cards([course_id])) + len(store.weak_questions([course_id]))},
    }


def _reextract(course_id: str, entry: dict) -> None:
    original = store.original_file(course_id, entry["id"])
    try:
        text = extract_text(*original) if original else None
    except Exception:  # fichier illisible avec la nouvelle version : on garde l'ancien texte
        text = None
    if not text:
        store.replace_text(course_id, entry["id"], store.file_text(course_id, entry["id"]), EXTRACT_VERSION)
        return
    # Mêmes lignes qu'avant (seules les marques de gras / italique s'ajoutent) : les chapitres de l'IA restent valables.
    chapters = detect_chapters(text) if entry.get("chapters_by") != "ai" else None
    store.replace_text(course_id, entry["id"], text, EXTRACT_VERSION, chapters)


@app.patch("/api/courses/{course_id}")
async def rename_course(course_id: str, body: CourseIn) -> dict:
    return store.rename_course(course_id, body.name)


class MoveIn(BaseModel):
    folder_id: str | None = None  # None : sortir le cours de son dossier


@app.put("/api/courses/{course_id}/folder")
async def move_course(course_id: str, body: MoveIn) -> dict:
    return store.move_course(course_id, body.folder_id)


# ---------- Dossiers de cours (un semestre, une UE…) ----------

class FolderIn(BaseModel):
    name: str | None = None
    archived: bool | None = None
    exam_week: str | None = None  # début de la semaine des partiels (AAAA-MM-JJ), "" pour l'effacer


@app.get("/api/folders")
async def list_folders() -> list[dict]:
    return store.list_folders()


@app.post("/api/folders")
async def create_folder(body: FolderIn) -> dict:
    if not (body.name or "").strip():
        raise HTTPException(400, "Donne un nom au semestre.")
    return store.create_folder(body.name)


@app.patch("/api/folders/{folder_id}")
async def update_folder(folder_id: str, body: FolderIn) -> dict:
    try:
        return store.update_folder(folder_id, body.name, body.archived, body.exam_week)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class ExamIn(BaseModel):
    date: str = ""  # début de la semaine des partiels, "" pour l'effacer


@app.put("/api/courses/{course_id}/exam")
async def set_exam(course_id: str, body: ExamIn) -> dict:
    try:
        course = store.set_exam_week(course_id, body.date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"exam": store.exam_info(course)}


@app.delete("/api/folders/{folder_id}")
async def delete_folder(folder_id: str) -> dict:
    store.delete_folder(folder_id)
    return {"deleted": folder_id}


@app.delete("/api/courses/{course_id}")
async def delete_course(course_id: str) -> dict:
    store.delete_course(course_id)
    return {"deleted": course_id}


@app.post("/api/courses/{course_id}/files")
async def upload_files(course_id: str, files: list[UploadFile] = File(...)) -> dict:
    store.get_course(course_id)
    extracted = []
    # On lit tout avant d'enregistrer : un fichier illisible n'entraîne pas d'import partiel.
    for upload in files:
        name = upload.filename or "fichier"
        data = await upload.read()
        try:
            # Dans un fil à part : la conversion d'un fichier Pages / Keynote peut prendre plusieurs secondes.
            text = await run_in_threadpool(extract_text, name, data)
        except (UnsupportedFileError, ConversionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # fichier corrompu, PDF protégé…
            raise HTTPException(400, f"Lecture impossible de {name} : {exc}") from exc
        if not text:
            raise HTTPException(400, f"Aucun texte trouvé dans {name}. Un PDF scanné (images) doit d'abord passer par un OCR.")
        extracted.append((name, data, text))
    results = {name: store.add_file(course_id, name, data, text, detect_chapters(text), EXTRACT_VERSION)
               for name, data, text in extracted}
    return {"results": results, "course": store.get_course(course_id)}


@app.post("/api/courses/{course_id}/files/{file_id}/chapters")
async def find_chapters(course_id: str, file_id: str, provider: str = Form("local"), model: str = Form("")) -> dict:
    """L'IA repère les chapitres du fichier. Si elle n'en trouve pas, le repérage automatique est conservé."""
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Moteur inconnu : {provider}")
    entry = next((f for f in store.get_course(course_id)["files"] if f["id"] == file_id), None)
    if entry is None:
        raise NotFound(file_id)
    text = store.file_text(course_id, file_id)
    candidates = ai_candidates(text)
    chapters = []
    if len(candidates) >= 2:
        try:
            answer = await PROVIDERS[provider].ask(CHAPTERS_SYSTEM, CHAPTERS_SCHEMA,
                                                   build_chapters_prompt(candidates, entry["name"]), model or None)
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        chapters = chapters_from_ai(text, answer, candidates)
    if not chapters:
        chapters = detect_chapters(text)
    course = store.set_chapters(course_id, file_id, chapters, "ai", revision=entry["revisions"])
    return {"course": course, "found": len(chapters)}


@app.get("/api/courses/{course_id}/outdated")
async def outdated(course_id: str) -> dict:
    """Après une nouvelle version d'un fichier : les questions et cartes qui ne correspondent plus au cours."""
    return store.outdated_items(course_id)


class OutdatedIn(BaseModel):
    keep: list[str] = []            # clés des éléments à garder quand même
    questions: list[dict] = []      # à supprimer : [{"quiz_id", "index"}]
    cards: list[str] = []           # à supprimer : identifiants des cartes


@app.post("/api/courses/{course_id}/outdated")
async def resolve_outdated(course_id: str, body: OutdatedIn) -> dict:
    if body.keep:
        store.keep_outdated(course_id, body.keep)
    if body.questions or body.cards:
        store.remove_outdated(course_id, body.questions, body.cards)
    return store.outdated_items(course_id)


@app.get("/api/courses/{course_id}/files/{file_id}/definitions")
async def file_definitions(course_id: str, file_id: str) -> list[dict]:
    rule = store.get_settings().get("definition_rule")
    return definitions_for(store.file_text(course_id, file_id), rule)


@app.post("/api/definitions/analyse")
async def analyse_definitions(file: UploadFile | None = File(None), text: str = Form("")) -> dict:
    """Calibrage : ce que chaque règle repère dans un extrait de cours (fichier importé ou passage collé)."""
    if file is not None:
        try:
            text = await run_in_threadpool(extract_text, file.filename or "extrait.txt", await file.read())
        except (UnsupportedFileError, ConversionError) as exc:
            raise HTTPException(400, str(exc)) from exc
    if not text.strip():
        raise HTTPException(400, "Importe un fichier ou colle un passage de ton cours.")
    result = analyse(text)
    return result | {"current": store.get_settings().get("definition_rule", "auto")}


@app.delete("/api/courses/{course_id}/files/{file_id}")
async def delete_file(course_id: str, file_id: str) -> dict:
    return store.remove_file(course_id, file_id)


# ---------- Génération (quiz, flashcards) ----------

def _prepare(course_id: str, provider: str, model: str,
             chapters: str = "") -> tuple[dict, list[tuple[str, str]], str, str]:
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Moteur inconnu : {provider}")
    course = store.get_course(course_id)
    if not course["files"]:
        raise HTTPException(400, "Ce cours n'a encore aucun fichier : dépose ton cours d'abord.")
    sources = _selected_sources(course, {c for c in chapters.split(",") if c})
    if not sources:
        raise HTTPException(400, "Aucun chapitre choisi : coche au moins un chapitre.")
    course_text = "\n\n".join(f"=== {name} ===\n{text}" for name, text in sources)
    return course, sources, course_text, model or PROVIDERS[provider].default_model()


def _selected_sources(course: dict, selection: set[str]) -> list[tuple[str, str]]:
    """Texte à utiliser : tout le cours si rien n'est choisi, sinon les fichiers entiers (« <id> »)
    et les chapitres (« <id>-<n> ») cochés."""
    sources = []
    for f in course["files"]:
        text = store.file_text(course["id"], f["id"])
        if not selection or f["id"] in selection:
            sources.append((f["name"], text))
            continue
        chapters = f.get("chapters") or []
        for position, chapter in enumerate(chapters):
            if f"{f['id']}-{position}" in selection:
                sources.append((f"{f['name']} — {chapter['title']}", chapter_text(text, chapters, position)))
    return sources


def _stream(course: dict, sources: list, course_text: str, job) -> StreamingResponse:
    """Lance `job(on_progress)` et renvoie sa progression puis son résultat en NDJSON, ligne par ligne."""
    async def events():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_progress(message: str) -> None:
            await queue.put({"type": "progress", "message": message})

        async def run() -> None:
            try:
                await queue.put({"type": "done", "result": await job(on_progress)})
            except ProviderError as exc:
                await queue.put({"type": "error", "message": str(exc)})
            except Exception as exc:
                await queue.put({"type": "error", "message": f"Erreur inattendue : {exc}"})

        chars = f"{len(course_text):,}".replace(",", " ")
        await queue.put({"type": "progress", "message": f"Cours « {course['name']} » : {chars} caractères, {len(sources)} fichier(s)"})
        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] in {"done", "error"}:
                    break
            await task
        finally:
            if not task.done():  # « Annuler » (la page a coupé la connexion) : on arrête la création, rien n'est gardé
                task.cancel()

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _meta(course: dict, provider: str, model: str, language: str, sources: list, chapters: str = "") -> dict:
    # `scope` : les chapitres choisis (vide = tout le cours), pour l'afficher dans la liste.
    scope = [name.split(" — ", 1)[-1] for name, _ in sources] if chapters else []
    return {"provider": provider, "model": model, "language": language, "course_id": course["id"],
            "course_name": course["name"], "course_version": course["version"],
            "sources": [name for name, _ in sources], "scope": scope}


@app.post("/api/courses/{course_id}/quizzes")
async def generate_quiz(
    course_id: str,
    provider: str = Form("local"),
    model: str = Form(""),
    num_questions: int = Form(10),
    difficulty: str = Form("moyen"),
    types: str = Form(",".join(QUESTION_TYPES)),
    language: str = Form("français"),
    chapters: str = Form(""),
    course_share: str = Form("equilibre"),
    focus: str = Form(""),
    manual: str = Form(""),
) -> StreamingResponse:
    course, sources, course_text, job = _quiz_job(course_id, provider, model, num_questions, difficulty, types,
                                                  language, chapters, course_share, focus, manual)
    return _stream(course, sources, course_text, job)


@app.post("/api/courses/{course_id}/quizzes/background")
async def generate_quiz_background(
    course_id: str,
    provider: str = Form("local"),
    model: str = Form(""),
    num_questions: int = Form(10),
    difficulty: str = Form("moyen"),
    types: str = Form(",".join(QUESTION_TYPES)),
    language: str = Form("français"),
    chapters: str = Form(""),
    course_share: str = Form("equilibre"),
    focus: str = Form(""),
    manual: str = Form(""),
    per_chapter: bool = Form(False),
) -> dict:
    """Crée le quiz en arrière-plan (ou un quiz par chapitre choisi) : on suit l'avancée avec /api/jobs."""
    course = store.get_course(course_id)
    units = _units(course, chapters) if per_chapter else []
    plan = units if len(units) > 1 else [(chapters, "")]
    created = []
    for key, label in plan:
        _, _, _, job = _quiz_job(course_id, provider, model, num_questions, difficulty, types, language, key,
                                 course_share, focus, "" if label else manual, title=label)
        created.append(jobs.submit({"kind": "quiz", "course_id": course_id, "course_name": course["name"],
                                    "provider": provider, "label": label or focus or "Nouveau quiz"}, job))
    return {"jobs": created}


def _units(course: dict, chapters: str) -> list[tuple[str, str]]:
    """Chapitres choisis (« <fichier>-<n> »), un fichier sans chapitres comptant comme un seul : (clé, titre)."""
    selection = {c for c in chapters.split(",") if c}
    units = []
    for f in course["files"]:
        if f.get("chapters"):
            for position, chapter in enumerate(f["chapters"]):
                key = f"{f['id']}-{position}"
                if not selection or f["id"] in selection or key in selection:
                    units.append((key, chapter["title"]))
        elif not selection or f["id"] in selection:
            units.append((f["id"], Path(f["name"]).stem))
    return units


@app.post("/api/courses/{course_id}/prepare")
async def prepare_course(
    course_id: str,
    provider: str = Form("local"),
    model: str = Form(""),
    quizzes: bool = Form(True),
    cards: bool = Form(True),
    num_questions: int = Form(10),
    cards_count: int = Form(10),
    types: str = Form("qcm,vrai_faux,reponse_courte"),
    chapters: str = Form(""),
    language: str = Form("français"),
) -> dict:
    """Tout préparer au dépôt d'un cours : un quiz et des flashcards par chapitre, en arrière-plan,
    chapitre après chapitre (le premier chapitre est prêt en premier)."""
    course = store.get_course(course_id)
    if not course["files"]:
        raise HTTPException(400, "Ce cours n'a encore aucun fichier : dépose ton cours d'abord.")
    created = []
    for key, label in _units(course, chapters):
        if quizzes:
            _, _, _, job = _quiz_job(course_id, provider, model, num_questions, "moyen", types, language, key,
                                     "equilibre", "", "", title=label)
            created.append(jobs.submit({"kind": "quiz", "course_id": course_id, "course_name": course["name"],
                                        "provider": provider, "label": label}, job))
        if cards:
            _, _, _, job = _cards_job(course_id, provider, model, language, cards_count, key, title=label)
            created.append(jobs.submit({"kind": "cards", "course_id": course_id, "course_name": course["name"],
                                        "provider": provider, "label": label}, job))
    return {"jobs": created}


@app.get("/api/jobs")
async def list_jobs() -> list[dict]:
    return jobs.list()


@app.delete("/api/jobs/{job_id}")
async def dismiss_job(job_id: str) -> dict:
    jobs.dismiss(job_id)
    return {"deleted": job_id}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    jobs.cancel(job_id)
    return {"cancelled": job_id}


@app.post("/api/jobs/cancel")
async def cancel_all_jobs() -> dict:
    return {"cancelled": jobs.cancel_all()}


def _quiz_job(course_id: str, provider: str, model: str, num_questions: int, difficulty: str, types: str,
              language: str, chapters: str, course_share: str, focus: str, manual: str, title: str = ""):
    """Prépare la création d'un quiz : (cours, sources, texte, job) ; `job(on_progress)` crée et enregistre le quiz."""
    course, sources, course_text, model = _prepare(course_id, provider, model, chapters)
    focus = focus.strip()[:200]
    mine = parse_manual_lines(manual)
    options = QuizOptions(
        num_questions=max(1, min(num_questions, 50)),
        difficulty=difficulty,
        types=[t for t in types.split(",") if t in QUESTION_TYPES] or list(QUESTION_TYPES),
        language=language.strip() or "français",
        course_share=COURSE_SHARES.get(course_share, COURSE_SHARES["equilibre"]),
        definition_rule=store.get_settings().get("definition_rule"),
        focus=focus,
    )
    options = replace(options, num_questions=max(options.num_questions, len(mine)))

    async def job(on_progress) -> dict:
        nonlocal options
        grounding = Grounding(course_text)
        # Questions des quiz déjà créés sur ce cours (les plus récents d'abord) : à ne pas reposer.
        previous = [q for summary in store.list_quizzes(course_id) for q in store.get_quiz(summary["id"])["questions"]]
        dropped: list[str] = []
        own = await _answer_manual(provider, model, course_text, mine, options, grounding, dropped, on_progress)
        if mine and not own and options.num_questions <= len(mine):
            raise ProviderError("Aucune de tes questions n'a sa réponse dans les chapitres choisis : "
                                "vérifie les chapitres cochés, ou reformule-les avec les mots du cours.")
        text = course_text
        if focus:
            text, hits, total = focus_text(course_text, focus)
            await on_progress(f"Thème « {focus} » : {hits} passage{'s' if hits > 1 else ''} du cours sur {total} en parle{'nt' if hits > 1 else ''}"
                              if hits and text != course_text else
                              f"Thème « {focus} » : tout le cours est gardé, l'IA s'en tient au thème")
        wanted = options.num_questions - len(own)
        if wanted <= 0:
            return _save_quiz_result({"title": title or f"Mes questions · {course['name']}", "questions": own}, course,
                                     provider, model, options, sources, chapters, dropped, focus)
        options = replace(options, num_questions=wanted)
        previous = previous + own
        asked = [q["question"] for q in previous]
        first = replace(options, avoid=asked)
        parts = await PROVIDERS[provider].generate(text, first, model, on_progress)
        quiz = assemble_quiz(parts, options, course["name"], grounding, previous)
        # Des questions écartées (hors du cours, trop faciles, doublons, réponse absente des choix…) ou oubliées : on complète.
        for _ in range(MAX_TOP_UPS):
            missing = options.num_questions - len(quiz["questions"])
            if missing <= 0:
                break
            outside = len(quiz["rejected"])
            detail = (f" ({outside} écartée{'s' if outside > 1 else ''} : hors du cours, trop facile ou déjà posée)"
                      if outside else "")
            await on_progress(f"{len(quiz['questions'])} questions valides sur {options.num_questions}{detail} : "
                              f"Pirouette en demande {missing} de plus…")
            avoid = [q["question"] for q in quiz["questions"]] + quiz["rejected"] + asked
            # S'il manque surtout des questions de cours, on demande précisément celles-là.
            extra = replace(options, num_questions=missing, avoid=avoid,
                            course_share=min(1.0, quiz["missing_course"] / missing))
            parts += await PROVIDERS[provider].generate(text, extra, model, on_progress)
            quiz = assemble_quiz(parts, options, course["name"], grounding, previous)
        quiz.pop("rejected")
        quiz.pop("missing_course")
        if not quiz["questions"] and not own:
            raise ProviderError("Aucune nouvelle question valable : celles proposées étaient déjà dans tes quiz, "
                                "hors du cours ou trop faciles. Réessaie, choisis d'autres chapitres ou un autre modèle.")
        quiz["questions"] = own + quiz["questions"]
        if title:
            quiz["title"] = title  # un quiz par chapitre : le titre du chapitre
        return _save_quiz_result(quiz, course, provider, model, options, sources, chapters, dropped, focus)

    return course, sources, course_text, job


def _save_quiz_result(quiz: dict, course: dict, provider: str, model: str, options: QuizOptions, sources: list,
                      chapters: str, dropped: list[str], focus: str) -> dict:
    if focus:
        quiz["title"] = focus[0].upper() + focus[1:]
        quiz["focus"] = focus
    if dropped:
        quiz["dropped"] = dropped  # tes questions sans réponse dans le cours : signalées au début du quiz
    quiz.update(_meta(course, provider, model, options.language, sources, chapters), difficulty=options.difficulty)
    # Un quiz existe déjà sur ces chapitres : on l'alimente au lieu d'en créer un second (les questions semblables
    # sont écartées ; l'IA a déjà reçu la liste des questions existantes pour ne pas les reposer).
    key = store.chapter_key(quiz)
    existing = store.chapter_quiz(key) if key else None
    if existing:
        merged, added = store.add_to_quiz(existing["id"], quiz["questions"])
        if not added:
            raise ProviderError("Toutes les questions proposées ressemblaient à celles déjà dans le quiz de ce chapitre. "
                                "Réessaie, ou choisis un autre niveau de difficulté.")
        return merged | {"added": added, "new_questions": list(range(len(merged["questions"]) - added, len(merged["questions"])))}
    return store.save_quiz(quiz)


def _manual_type(types: list[str]) -> str:
    for qtype in ("qcm", "reponse_courte", "vrai_faux"):
        if qtype in types:
            return qtype
    return "reponse_courte"


async def _answer_manual(provider: str, model: str, course_text: str, mine: list[str], options: QuizOptions,
                         grounding: Grounding, dropped: list[str], on_progress) -> list[dict]:
    """Tes questions : l'IA cherche la réponse dans le cours et écrit les propositions. Celles dont la réponse
    n'est pas dans le cours sont écartées (et listées dans `dropped`)."""
    if not mine:
        return []
    engine = PROVIDERS[provider]
    await on_progress(f"Tes {len(mine)} question{'s' if len(mine) > 1 else ''} : Pirouette cherche les réponses dans le cours…")
    chunk_size = engine.chunk_chars() if hasattr(engine, "chunk_chars") else len(course_text) + 1
    chunks = chunk_text(course_text, chunk_size)
    groups: dict[int, list[tuple[int, str]]] = {}
    for number, text in enumerate(mine, start=1):
        groups.setdefault(best_chunk(text, chunks), []).append((number, text))
    qtype = _manual_type(options.types)
    answers: dict[int, dict] = {}
    for index, questions in groups.items():
        answer = await engine.ask(MANUAL_SYSTEM, MANUAL_SCHEMA,
                                  build_manual_prompt(chunks[index], questions, qtype, options.language), model)
        for item in (answer or {}).get("questions") or []:
            if isinstance(item, dict) and isinstance(item.get("number"), int):
                answers.setdefault(item["number"], item)
    kept = []
    for number, text in enumerate(mine, start=1):
        item = answers.get(number) or {}
        question = None
        if item.get("found"):
            question = normalize_question(item | {"type": qtype, "question": text, "key_terms": []}, [qtype])
        if question is None or not grounding.check_question(question):
            dropped.append(text)
            continue
        kept.append(question | {"manual": True})
    if dropped:
        await on_progress(f"Réponse introuvable dans le cours pour {len(dropped)} de tes questions : "
                          + " ; ".join(f"« {q} »" for q in dropped[:3]) + ("…" if len(dropped) > 3 else ""))
    return kept


@app.post("/api/courses/{course_id}/cards")
async def generate_cards(course_id: str, provider: str = Form("local"), model: str = Form(""),
                         language: str = Form("français"), count: int = Form(20),
                         chapters: str = Form(""), focus: str = Form("")) -> StreamingResponse:
    """Ajoute des cartes au paquet du cours, sans doublon avec celles qui existent déjà."""
    course, sources, course_text, job = _cards_job(course_id, provider, model, language, count, chapters, focus)
    return _stream(course, sources, course_text, job)


def _cards_job(course_id: str, provider: str, model: str, language: str, count: int, chapters: str, focus: str = "",
               title: str = ""):
    """Prépare l'ajout de cartes : (cours, sources, texte, job) ; `job(on_progress)` crée et enregistre les cartes."""
    course, sources, course_text, model = _prepare(course_id, provider, model, chapters)
    focus = focus.strip()[:200]
    ai_text = focus_text(course_text, focus)[0] if focus else course_text
    language = language.strip() or "français"
    count = max(1, min(count, 100))

    async def job(on_progress) -> dict:
        deck = store.get_doc(course_id, "cards") or {"cards": []}
        existing = deck["cards"]
        grounding = Grounding(course_text)
        new: list[dict] = []
        rejected: list[str] = []
        for attempt in range(1 + MAX_TOP_UPS):
            missing = count - len(new)
            if attempt:
                # Rien de neuf au tour précédent (que des doublons) : inutile d'insister.
                if missing <= 0 or not (added or outside):
                    break
                detail = f" ({len(rejected)} écartée{'s' if len(rejected) > 1 else ''} : pas dans ton cours)" if rejected else ""
                await on_progress(f"{len(new)} cartes sur {count}{detail} : Pirouette en demande {missing} de plus…")
            avoid = [c["front"] for c in [*existing, *new]] + rejected
            parts = await PROVIDERS[provider].generate_cards(
                ai_text, missing, language, model, on_progress, avoid=avoid,
                definition_rule=store.get_settings().get("definition_rule"), focus=focus)
            before = len(rejected)
            added = normalize_cards(parts, missing, [*existing, *new], grounding, rejected)
            outside = len(rejected) - before
            new += added
        if not new:
            raise ProviderError("Le modèle n'a produit aucune nouvelle carte tirée de ton cours. "
                                "Choisis d'autres chapitres, ou change de modèle.")
        meta = _meta(course, provider, model, language, sources, chapters)
        for card in new:
            card.update(scope=meta["scope"], created_at=datetime.now().isoformat(timespec="seconds"))
        # Relu juste avant d'enregistrer : on a pu réviser des cartes pendant la création.
        deck = store.get_doc(course_id, "cards") or {"cards": []}
        deck.update(course_name=course["name"], course_id=course_id, cards=[*deck["cards"], *new])
        store.save_doc(course_id, "cards", deck)
        return deck | {"added": len(new), "title": title or "Flashcards", "count": len(new)}

    return course, sources, course_text, job


@app.get("/api/courses/{course_id}/cards")
async def get_cards(course_id: str) -> dict:
    course = store.get_course(course_id)
    return store.get_doc(course_id, "cards") or {"cards": [], "course_name": course["name"], "course_id": course_id}


@app.delete("/api/courses/{course_id}/cards")
async def delete_all_cards(course_id: str) -> dict:
    store.delete_doc(course_id, "cards")
    return {"deleted": "cards"}


@app.delete("/api/courses/{course_id}/cards/{card_id}")
async def delete_card(course_id: str, card_id: str) -> dict:
    return store.remove_card(course_id, card_id)


class CardIn(BaseModel):
    front: str
    back: str

    def cleaned(self) -> tuple[str, str]:
        front, back = self.front.strip(), self.back.strip()
        if not front or not back:
            raise HTTPException(400, "Écris la question (recto) et la réponse (verso).")
        return front[:1000], back[:2000]


@app.patch("/api/courses/{course_id}/cards/{card_id}")
async def edit_card(course_id: str, card_id: str, body: CardIn) -> dict:
    return store.update_card(course_id, card_id, *body.cleaned())


@app.post("/api/courses/{course_id}/cards/manual")
async def add_card(course_id: str, body: CardIn) -> dict:
    """Carte écrite à la main. Signale (sans bloquer) une carte qui ressemble à une carte existante."""
    front, back = body.cleaned()
    existing = (store.get_doc(course_id, "cards") or {}).get("cards", [])
    similar = next((c for c in existing if is_duplicate(front, back, [c])), None)
    card = store.add_card(course_id, front, back)
    return {"card": card, "similar": similar and {"front": similar["front"], "back": similar["back"]}}


class ReviewIn(BaseModel):
    known: bool | None = None
    rating: str | None = None  # again / hard / good / easy (répétition espacée)


@app.post("/api/courses/{course_id}/cards/{card_id}/review")
async def review_card(course_id: str, card_id: str, body: ReviewIn) -> dict:
    if body.rating is not None and body.rating not in srs.RATINGS:
        raise HTTPException(400, "Réponse inconnue.")
    if body.rating is None and body.known is None:
        raise HTTPException(400, "Indique si tu savais la carte.")
    card = store.review_card(course_id, card_id, body.known, body.rating)
    return card | {"next": srs.preview(card)}


# ---------- Révision : cartes du jour, points faibles, suivi ----------

def _scope(course: str, folder: str) -> list[str]:
    """Cours concernés : un cours, les cours d'un dossier, ou tous les cours hors dossiers archivés."""
    if course:
        store.get_course(course)
        return [course]
    if folder:
        store.get_folder(folder)
        return [c["id"] for c in store.list_courses() if c.get("folder_id") == folder]
    return store.active_course_ids()


def _card_item(entry: dict) -> dict:
    course, card = entry["course"], entry["card"]
    return {"kind": "card", "course_id": course["id"], "course_name": course["name"], "card": card,
            "next": srs.preview(card)}


def _question_item(entry: dict) -> dict:
    quiz = entry["quiz"]
    return {"kind": "question", "course_id": quiz.get("course_id"), "course_name": quiz.get("course_name") or "",
            "quiz_id": quiz["id"], "quiz_title": quiz["title"], "index": entry["index"],
            "question": quiz["questions"][entry["index"]], "stat": entry["stat"]}


def _interleave(cards: list, questions: list) -> list:
    """Mélange régulier : une question de quiz toutes les quelques cartes."""
    if not questions:
        return cards
    if not cards:
        return questions
    step = (len(cards) + len(questions)) / len(questions)
    items, qi = [], 0
    for position in range(len(cards) + len(questions)):
        if qi < len(questions) and position >= round(step * qi + step / 2) - 1:
            items.append(questions[qi])
            qi += 1
        else:
            items.append(cards[position - qi])
    return items


SESSION_QUESTIONS = 10


@app.get("/api/session")
async def session(mode: str = "today", course: str = "", folder: str = "", filter: str = "review",
                  questions: bool = True, minutes: int = 15, chapter: str = "") -> dict:
    """Une séance de révision : `today` (cartes du jour + questions de quiz à reposer), `weak` (points faibles),
    `plan` (séance du plan : les cartes les plus difficiles d'abord, à la taille choisie), `chapter` (les cartes
    d'un chapitre, pour le rétroplanning) ou `cards` (les cartes d'un cours)."""
    ids = _scope(course, folder)
    if mode == "plan":
        max_cards, max_questions = plans.session_size(minutes if minutes in plans.MINUTES else 15)
        hard = store.weak_cards(ids)[:max_cards // 3]
        seen = {e["card"]["id"] for e in hard}
        cards = [_card_item(e) for e in hard + [e for e in store.today_cards(ids) if e["card"]["id"] not in seen]][:max_cards]
        chosen = store.weak_questions(ids)[:max_questions]
        chosen += store.review_questions(ids)[:max_questions - len(chosen)]
        asked = [_question_item(e) for e in chosen] if questions else []
        random.shuffle(asked)
        return {"mode": mode, "items": _interleave(cards, asked), "cards": len(cards), "questions": len(asked)}
    if mode == "chapter":
        cards = [_card_item({"course": c, "card": card}) for c, deck in store._decks(ids) for card in deck
                 if _same_title(chapter, card.get("scope") or [])]
        return {"mode": mode, "items": cards, "cards": len(cards), "questions": 0}
    if mode == "today":
        cards = [_card_item(e) for e in store.today_cards(ids)]
        chosen = store.weak_questions(ids)[:SESSION_QUESTIONS]
        chosen += store.review_questions(ids)[:SESSION_QUESTIONS - len(chosen)]
        asked = [_question_item(e) for e in chosen] if questions else []
        random.shuffle(asked)
    elif mode == "weak":
        cards = [_card_item(e) for e in store.weak_cards(ids)[:30]]
        asked = [_question_item(e) for e in store.weak_questions(ids)[:20]] if questions else []
    elif mode == "cards":
        tests = {"review": lambda c: c["status"] != "known", "known": lambda c: c["status"] == "known"}
        test = tests.get(filter, lambda c: True)
        cards = [_card_item({"course": c, "card": card}) for c, deck in store._decks(ids) for card in deck if test(card)]
        random.shuffle(cards)
        asked = []
    else:
        raise HTTPException(400, "Type de révision inconnu.")
    return {"mode": mode, "items": _interleave(cards, asked), "cards": len(cards), "questions": len(asked)}


# ---------- Plan de révision et rétroplanning ----------

def _owner(folder: str, course: str) -> tuple[str, str]:
    if folder:
        store.get_folder(folder)
        return "dossier", folder
    if course:
        store.get_course(course)
        return "cours", course
    raise HTTPException(400, "Choisis un semestre ou un cours.")


def _hard_cards(ids: list[str], limit: int = 5) -> list[dict]:
    """Les cartes où l'on bloque le plus : souvent oubliées, ratées la dernière fois."""
    return [{"course_id": e["course"]["id"], "course": e["course"]["name"], "front": e["card"]["front"],
             "back": e["card"]["back"], "lapses": e["card"].get("lapses", 0)} for e in store.weak_cards(ids)[:limit]]


@app.get("/api/plan")
async def get_plan(folder: str = "", course: str = "") -> dict:
    kind, owner_id = _owner(folder, course)
    ids = store.scope_course_ids(kind, owner_id)
    plan = store.get_plan(kind, owner_id)
    status = plans.status(plan, store.active_days(ids), date.today()) if plan else None
    cards, questions = plans.session_size(plan["minutes"]) if plan else (0, 0)
    return {"plan": plan, "status": status, "hard": _hard_cards(ids), "weak": len(store.weak_cards(ids)),
            "session": {"cards": min(cards, len(store.today_cards(ids)) + len(store.weak_cards(ids))),
                        "questions": questions},
            "rhythms": plans.RHYTHMS, "minutes": plans.MINUTES}


class PlanIn(BaseModel):
    folder: str = ""
    course: str = ""
    every: int = 2
    minutes: int = 15


@app.put("/api/plan")
async def save_plan(body: PlanIn) -> dict:
    kind, owner_id = _owner(body.folder, body.course)
    try:
        plans.check(body.every, body.minutes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    old = store.get_plan(kind, owner_id) or {}
    start = old.get("start") if old.get("every") == body.every else None  # nouveau rythme : on repart d'aujourd'hui
    store.set_plan(kind, owner_id, {"every": body.every, "minutes": body.minutes, "start": start or date.today().isoformat()})
    return await get_plan(body.folder, body.course)


@app.delete("/api/plan")
async def delete_plan(folder: str = "", course: str = "") -> dict:
    kind, owner_id = _owner(folder, course)
    store.set_plan(kind, owner_id, None)
    return {"plan": None}


def _exam_day(kind: str, owner_id: str) -> date | None:
    owner = store.get_folder(owner_id) if kind == "dossier" else store.get_course(owner_id)
    day = owner.get("exam_week")
    if not day and kind == "cours":
        info = store.exam_info(owner)
        day = info and info["date"]
    return date.fromisoformat(day) if day else None


def _retro_units(ids: list[str], skip: set[str]) -> tuple[list[dict], list[dict]]:
    """Chapitres des cours (dans l'ordre des fichiers), sans ceux déjà vus ; et les cours."""
    courses = sorted((c for c in store.list_courses() if c["id"] in ids), key=lambda c: c["name"].lower())
    units = []
    for course in courses:
        for key, title in _units(course, ""):
            if f"{course['id']}:{key}" not in skip:
                units.append({"course_id": course["id"], "course": course["name"], "key": key,
                              "title": title or course["name"]})
    return units, [{"id": c["id"], "name": c["name"]} for c in courses if c["files"]]


def _same_title(title: str, scope: list[str]) -> bool:
    """« Chapitre 1 — La cellule » et « Chapitre 1 : La cellule » désignent le même chapitre."""
    norm = lambda t: " ".join(re.findall(r"\w+", t.lower()))  # noqa: E731
    return norm(title) in {norm(t) for t in scope}


def _retro_view(kind: str, owner_id: str) -> dict:
    ids = store.scope_course_ids(kind, owner_id)
    retro = store.get_retro(kind, owner_id)
    exam = _exam_day(kind, owner_id)
    if not retro:
        return {"retro": None, "exam": exam and exam.isoformat()}
    active = store.active_days(ids)
    today = date.today()
    quizzes = [q for q in store.list_quizzes() if q.get("course_id") in ids]
    for s in retro["sessions"]:
        day = date.fromisoformat(s["date"])
        for unit in s["units"]:
            same = [q for q in quizzes if q.get("course_id") == unit["course_id"]]
            quiz = next((q for q in same if _same_title(unit["title"], q.get("scope") or [])), None) \
                or (next((q for q in same if not q.get("scope")), None) if "-" not in unit["key"] else None)
            unit["quiz_id"] = quiz and quiz["id"]
            unit["quiz_done"] = bool(quiz and quiz.get("best_score"))
        # Séance faite : cochée à la main ; pour les chapitres, tous leurs quiz passés ; sinon, on a révisé ce jour-là.
        learned = s["kind"] == "learn" and all(u["quiz_done"] for u in s["units"])
        s.update(past=day < today, today=day == today,
                 done=bool(s.get("checked")) or learned or (s["kind"] != "learn" and day <= today and day in active))
    return {"retro": retro, "exam": exam and exam.isoformat()}


@app.get("/api/retro")
async def get_retro(folder: str = "", course: str = "") -> dict:
    return _retro_view(*_owner(folder, course))


class RetroIn(BaseModel):
    folder: str = ""
    course: str = ""
    every: int = 2
    exam: str = ""   # début de la semaine des partiels, si elle n'est pas encore connue


@app.post("/api/retro")
async def build_retro(body: RetroIn) -> dict:
    """(Re)construit le rétroplanning d'aujourd'hui aux partiels ; les chapitres des séances faites sont gardés."""
    kind, owner_id = _owner(body.folder, body.course)
    if body.every not in plans.RHYTHMS:
        raise HTTPException(400, "Rythme inconnu.")
    if body.exam:
        try:
            store.update_folder(owner_id, exam_week=body.exam) if kind == "dossier" else store.set_exam_week(owner_id, body.exam)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    exam = _exam_day(kind, owner_id)
    today = date.today()
    if not exam or exam <= today:
        raise HTTPException(400, "Indique une semaine de partiels à venir.")
    old = _retro_view(kind, owner_id)["retro"]
    kept = [s for s in (old or {}).get("sessions", []) if s["past"] or s["done"]]
    seen = {f"{u['course_id']}:{u['key']}" for s in kept if s["done"] for u in s["units"]}
    kept = [s for s in kept if s["date"] < today.isoformat() or s["done"]]  # la séance faite aujourd'hui reste
    start = today + timedelta(days=1) if any(s["date"] == today.isoformat() for s in kept) else today
    units, courses = _retro_units(store.scope_course_ids(kind, owner_id), seen)
    fresh = plans.build_retro(start, exam, body.every, units, courses)
    clean = lambda s: {k: v for k, v in s.items() if k not in {"past", "today", "done"}} | ({"checked": True} if s.get("done") else {})  # noqa: E731
    sessions = [clean(s) for s in kept] + [clean(s) for s in fresh]
    for s in sessions:
        for unit in s["units"]:
            unit.pop("quiz_id", None)
            unit.pop("quiz_done", None)
    store.set_retro(kind, owner_id, {"every": body.every, "exam": exam.isoformat(),
                                     "created": today.isoformat(), "sessions": sessions})
    return _retro_view(kind, owner_id)


class RetroCheckIn(BaseModel):
    folder: str = ""
    course: str = ""
    date: str
    done: bool = True


@app.put("/api/retro/session")
async def check_retro_session(body: RetroCheckIn) -> dict:
    kind, owner_id = _owner(body.folder, body.course)
    retro = store.get_retro(kind, owner_id)
    if not retro:
        raise HTTPException(404, "Pas de rétroplanning.")
    for s in retro["sessions"]:
        if s["date"] == body.date:
            s["checked"] = body.done
    store.set_retro(kind, owner_id, retro)
    return _retro_view(kind, owner_id)


@app.delete("/api/retro")
async def delete_retro(folder: str = "", course: str = "") -> dict:
    kind, owner_id = _owner(folder, course)
    store.set_retro(kind, owner_id, None)
    return {"retro": None}


# ---------- Maîtrise par chapitre ----------

MASTERY_LEVELS = ("a_voir", "fragile", "en_cours", "acquis")


def _chapter_mastery(cards: list[dict], quiz_scores: list[float]) -> dict:
    """Où l'on en est sur un chapitre : cartes vues, bien ancrées (rappel à 7 jours ou plus), difficiles ; meilleur
    score de ses quiz. Niveau : à voir, fragile, en cours, acquis ; et un score de 0 à 100."""
    seen = [c for c in cards if c.get("reviews")]
    solid = [c for c in seen if (c.get("interval") or 0) >= 7 and not srs.is_weak(c)]
    weak = [c for c in seen if srs.is_weak(c)]
    quiz = round(max(quiz_scores)) if quiz_scores else None
    parts = []
    if cards:
        parts.append(100 * (len(solid) + 0.5 * (len(seen) - len(solid) - len(weak))) / len(cards))
    if quiz is not None:
        parts.append(quiz)
    score = round(sum(parts) / len(parts)) if parts else 0
    if not seen and quiz is None:
        level = "a_voir"
    elif (seen and len(weak) >= max(1, 0.2 * len(seen))) or (quiz is not None and quiz < 50):
        level = "fragile"
    elif score >= 80:
        level = "acquis"
    else:
        level = "en_cours"
    return {"cards": len(cards), "seen": len(seen), "solid": len(solid), "weak": len(weak), "quiz": quiz,
            "score": score, "level": level}


@app.get("/api/mastery")
async def mastery(folder: str = "", course: str = "") -> list[dict]:
    ids = _scope(course, folder)
    quizzes = [q for q in store.list_quizzes() if q.get("course_id") in ids]
    result = []
    for c in sorted((c for c in store.list_courses() if c["id"] in ids), key=lambda c: c["name"].lower()):
        deck = (store.get_doc(c["id"], "cards") or {}).get("cards", [])
        units = _units(c, "")
        chapters = []
        for key, title in units:
            single = len(units) == 1  # un seul chapitre : toutes les cartes et tous les quiz du cours
            cards = deck if single else [card for card in deck if _same_title(title, card.get("scope") or [])]
            scores = [100 * q["best_score"]["score"] / max(q["best_score"]["total"], 1) for q in quizzes
                      if q.get("course_id") == c["id"] and q.get("best_score")
                      and (single or _same_title(title, q.get("scope") or []))]
            file_name = next((f["name"] for f in c["files"] if key == f["id"] or key.startswith(f"{f['id']}-")), "")
            chapters.append({"key": key, "title": title, "file": file_name} | _chapter_mastery(cards, scores))
        if chapters:
            result.append({"course_id": c["id"], "course": c["name"], "chapters": chapters})
    return result


@app.get("/api/progress")
async def progress(course: str = "", folder: str = "") -> dict:
    ids = _scope(course, folder)
    data = store.progress()
    exam = store.exam_info(store.get_course(course)) if course else None
    if folder:
        day = store.get_folder(folder).get("exam_week")
        exam = {"date": day, "days": (date.fromisoformat(day) - date.today()).days, "from": "dossier"} if day else None
    return data | {
        "exam": exam,
        "today": {"cards": len(store.today_cards(ids)),
                  "questions": min(SESSION_QUESTIONS, len(store.weak_questions(ids)) + len(store.review_questions(ids)))},
        "weak": {"cards": len(store.weak_cards(ids)), "questions": len(store.weak_questions(ids))},
        "hard": _hard_cards(ids),
        "new_per_day": store.new_per_day(),
    }


# ---------- Quiz ----------

@app.get("/api/quizzes")
async def list_quizzes(orphans: bool = False) -> list[dict]:
    """Tous les quiz, ou seulement ceux créés avant la section Cours (`orphans=true`)."""
    return store.list_quizzes(orphans=orphans)


@app.get("/api/quizzes/{quiz_id}")
async def get_quiz(quiz_id: str) -> dict:
    return store.get_quiz(quiz_id)


class QuizRenameIn(BaseModel):
    title: str


@app.patch("/api/quizzes/{quiz_id}")
async def rename_quiz(quiz_id: str, body: QuizRenameIn) -> dict:
    if not body.title.strip():
        raise HTTPException(400, "Donne un nom au quiz.")
    quiz = store.rename_quiz(quiz_id, body.title)
    return {"id": quiz["id"], "title": quiz["title"]}


@app.delete("/api/quizzes/{quiz_id}/questions/{index}")
async def delete_question(quiz_id: str, index: int) -> dict:
    """Retire une question jugée hors sujet ; le quiz vide est supprimé."""
    quiz = store.remove_question(quiz_id, index)
    if not quiz["questions"]:
        store.delete_quiz(quiz_id)
    return {"questions": len(quiz["questions"])}


@app.delete("/api/quizzes/{quiz_id}")
async def delete_quiz(quiz_id: str) -> dict:
    store.delete_quiz(quiz_id)
    return {"deleted": quiz_id}


class AnswersIn(BaseModel):
    answers: list[dict]  # [{"index": 3, "correct": true}]


@app.post("/api/quizzes/{quiz_id}/answers")
async def record_answers(quiz_id: str, body: AnswersIn) -> dict:
    try:
        return store.record_answers(quiz_id, body.answers)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, "Réponses mal formées.") from exc


class AttemptIn(BaseModel):
    score: int
    total: int


@app.post("/api/quizzes/{quiz_id}/attempts")
async def record_attempt(quiz_id: str, body: AttemptIn) -> dict:
    quiz = store.record_attempt(quiz_id, body.score, body.total)
    return {"attempts": quiz["attempts"]}


# ---------- Mode partiel ----------

@app.get("/api/courses/{course_id}/partiel")
async def partiel(course_id: str, questions: int = 20, cards: int = 10) -> dict:
    """Sujet d'un partiel blanc : questions de tous les quiz du cours et flashcards, mélangées."""
    store.get_course(course_id)
    chosen = store.partiel_items(course_id, max(0, min(questions, 200)), max(0, min(cards, 200)))
    items = [_question_item(e | {"stat": None}) for e in chosen["questions"]]
    items += [_card_item({"course": store.get_course(course_id), "card": c}) for c in chosen["cards"]]
    random.shuffle(items)
    return {"items": items, "available": chosen["available"], "history": store.list_partiels(course_id)[-5:]}


class PartielIn(BaseModel):
    id: str | None = None  # pour mettre à jour la note (verdict contesté)
    score: float
    points: float
    total: int
    duration: int | None = None  # secondes


@app.post("/api/courses/{course_id}/partiels")
async def save_partiel(course_id: str, body: PartielIn) -> dict:
    return store.save_partiel(course_id, body.model_dump(exclude_none=True))


class GradeIn(BaseModel):
    provider: str = "local"
    model: str = ""
    items: list[dict]  # [{"question", "expected", "given", "source"}]


@app.post("/api/partiel/grade")
async def grade_answers(body: GradeIn) -> dict:
    """L'IA corrige les réponses écrites : juste, partiel ou faux, avec une courte justification."""
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"Moteur inconnu : {body.provider}")
    engine = PROVIDERS[body.provider]
    numbered = list(enumerate(body.items, start=1))
    grades: dict[int, dict] = {}
    try:
        for start in range(0, len(numbered), BATCH):
            batch = numbered[start:start + BATCH]
            answer = await engine.ask(GRADE_SYSTEM, GRADE_SCHEMA, build_grade_prompt(batch), body.model or None)
            grades |= read_grades(answer, [n for n, _ in batch])
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    # Une réponse que l'IA a oubliée : à corriger soi-même.
    return {"grades": [grades.get(n) for n, _ in numbered]}


# ---------- Mises à jour de l'app ----------

# ---------- S'échanger des quiz et des flashcards (fichier texte) ----------

def _safe_name(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "-", name).strip(" .-")[:80] or "Pirouette"


def _export(kind: str, item_id: str) -> tuple[str, str]:
    """(nom du fichier, texte) d'un quiz (`quiz`) ou des flashcards d'un cours (`cards`)."""
    if kind == "quiz":
        quiz = store.get_quiz(item_id)
        course = quiz.get("course_name") or ""
        return f"{_safe_name('Quiz - ' + (quiz.get('custom_title') or quiz['title']))}.txt", exchange.quiz_to_text(quiz, course)
    course = store.get_course(item_id)
    cards = (store.get_doc(item_id, "cards") or {}).get("cards", [])
    if not cards:
        raise HTTPException(400, "Ce cours n'a pas encore de flashcards.")
    return f"{_safe_name('Flashcards - ' + course['name'])}.txt", exchange.cards_to_text(cards, course["name"])


@app.get("/api/export/{kind}/{item_id}")
async def export_file(kind: str, item_id: str) -> PlainTextResponse:
    """Téléchargement (dans un navigateur)."""
    if kind not in {"quiz", "cards"}:
        raise HTTPException(404, "Export inconnu.")
    name, text = _export(kind, item_id)
    return PlainTextResponse(text, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})


@app.post("/api/export/{kind}/{item_id}")
async def export_to_downloads(kind: str, item_id: str) -> dict:
    """App Mac : enregistre le fichier dans Téléchargements et le montre dans le Finder."""
    if kind not in {"quiz", "cards"}:
        raise HTTPException(404, "Export inconnu.")
    name, text = _export(kind, item_id)
    folder = Path.home() / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    for n in range(2, 100):
        if not path.exists():
            break
        path = folder / f"{Path(name).stem} ({n}).txt"
    path.write_text(text, encoding="utf-8")
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], capture_output=True, timeout=10)
    return {"path": str(path), "name": path.name}


@app.post("/api/courses/{course_id}/import")
async def import_file(course_id: str, file: UploadFile = File(...)) -> dict:
    """Un quiz ou des flashcards reçus en .txt (de Pirouette, écrits à la main, d'Anki, de Quizlet…)."""
    course = store.get_course(course_id)
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    found = exchange.parse(text)
    if found["kind"] == "quiz":
        if not found["questions"]:
            raise HTTPException(400, "Aucune question lisible dans ce fichier.")
        # Chapitres du fichier retrouvés dans ce cours : le quiz s'y rattache (et rejoint le quiz de ces chapitres).
        titles = [title for _, title in _units(course, "")]
        scope = [t for t in titles if any(_same_title(t, [c]) for c in found["chapters"])]
        quiz = {"title": found["title"] or Path(file.filename or "Quiz").stem, "questions": found["questions"],
                "provider": "import", "model": "", "language": "français", "course_id": course_id,
                "course_name": course["name"], "course_version": course["version"], "sources": [], "scope": scope,
                "difficulty": "moyen"}
        key = store.chapter_key(quiz)
        existing = store.chapter_quiz(key) if key else None
        if existing:
            merged, added = store.add_to_quiz(existing["id"], quiz["questions"])
            return {"kind": "quiz", "quiz_id": merged["id"], "title": merged["title"], "added": added,
                    "skipped": found["skipped"] + len(quiz["questions"]) - added, "merged": True}
        saved = store.save_quiz(quiz)
        return {"kind": "quiz", "quiz_id": saved["id"], "title": saved["title"], "added": len(saved["questions"]),
                "skipped": found["skipped"], "merged": False}
    if found["kind"] == "cards":
        deck = store.get_doc(course_id, "cards") or {"cards": []}
        new = normalize_cards([{"cards": found["cards"]}], 1000, deck["cards"])
        now = datetime.now().isoformat(timespec="seconds")
        for card in new:
            card.update(scope=[], created_at=now)
        deck.update(course_name=course["name"], course_id=course_id, cards=[*deck["cards"], *new])
        store.save_doc(course_id, "cards", deck)
        return {"kind": "cards", "added": len(new), "skipped": found["skipped"] + len(found["cards"]) - len(new)}
    raise HTTPException(400, "Rien de reconnu dans ce fichier : il faut un quiz Pirouette, des questions numérotées "
                             "avec leurs propositions, ou des flashcards « recto ; verso » (une par ligne).")


@app.post("/api/quit")
async def quit_app() -> dict:
    """Arrête l'app Mac (après une mise à jour). Réponse d'abord, puis arrêt net un instant plus tard : les données
    sont déjà sur le disque, et rien ne peut plus bloquer la fermeture."""
    if os.getenv("PIROUETTE_DESKTOP") != "1":
        raise HTTPException(400, "Seulement dans l'app Mac.")
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return {"quitting": True}


@app.get("/api/update")
async def update_check() -> dict:
    return await updater.check() | {"desktop": os.getenv("PIROUETTE_DESKTOP") == "1"}


class InstallIn(BaseModel):
    url: str


@app.post("/api/update/install")
async def update_install(body: InstallIn) -> StreamingResponse:
    """Télécharge et prépare la nouvelle version ; elle remplacera l'ancienne quand Pirouette sera quittée."""
    if not body.url.startswith("https://github.com/") and not body.url.startswith("https://objects.githubusercontent.com/"):
        raise HTTPException(400, "Adresse de mise à jour inattendue.")

    async def events():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_progress(event: dict) -> None:
            await queue.put(event)

        async def run() -> None:
            try:
                await updater.install(body.url, on_progress)
                await queue.put({"type": "done"})
            except Exception as exc:  # téléchargement coupé, disque plein, droits…
                await queue.put({"type": "error", "message": f"La mise à jour n'a pas pu se faire : {exc}"})

        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] in {"done", "error"}:
                    break
            await task
        finally:
            if not task.done():  # « Annuler » (la page a coupé la connexion) : on arrête la création, rien n'est gardé
                task.cancel()

    return StreamingResponse(events(), media_type="application/x-ndjson")


# ---------- Explique-moi ----------

class ExplainIn(BaseModel):
    course_id: str | None = None
    question: str
    expected: str
    given: str = ""
    source: str = ""
    provider: str = "local"
    model: str = ""


@app.post("/api/explain")
async def explain(body: ExplainIn) -> dict:
    """L'IA réexplique la notion à partir du passage du cours d'où vient la question ou la carte."""
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"Moteur inconnu : {body.provider}")
    course_text = ""
    if body.course_id:
        course_text = "\n\n".join(text for _, text in store.course_sources(body.course_id))
    excerpt = course_excerpt(course_text, body.source, body.question) or body.source
    try:
        answer = await PROVIDERS[body.provider].ask(
            EXPLAIN_SYSTEM, EXPLAIN_SCHEMA, build_explain_prompt(excerpt, body.question, body.expected, body.given),
            body.model or None)
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    text = str((answer or {}).get("explanation", "")).strip()
    if not text:
        raise HTTPException(502, "L'IA n'a pas su expliquer cette notion. Réessaie.")
    return {"explanation": text}


# ---------- Signaler une question ----------

FEEDBACK_EMAIL = os.getenv("PIROUETTE_FEEDBACK_EMAIL", "gonnetpierrelouis@gmail.com")


class FeedbackIn(BaseModel):
    quiz_id: str
    index: int            # position de la question dans le quiz
    given: str = ""       # réponse donnée par l'utilisateur
    message: str = ""


def feedback_email(quiz: dict, question: dict, given: str, message: str) -> tuple[str, str]:
    """Sujet et texte du mail de signalement."""
    from . import __version__

    lines = [f"Cours : {quiz.get('course_name') or '—'}", f"Quiz : {quiz.get('title')}", "",
             f"Question ({question['type']}) : {question['question']}"]
    for choice in question.get("choices") or []:
        lines.append(f"  {'✓' if choice == question['answer'] else '•'} {choice}")
    lines += [f"Bonne réponse selon le quiz : {question['answer']}"]
    if given:
        lines.append(f"Réponse donnée : {given}")
    if question.get("explanation"):
        lines.append(f"Explication : {question['explanation']}")
    if question.get("source"):
        lines.append(f"Phrase du cours : « {question['source']} »")
    lines += ["", "Message :", message.strip() or "(aucun)", "",
              f"Pirouette {__version__} · modèle {quiz.get('model') or '?'} · chapitres : "
              f"{', '.join(quiz.get('scope') or []) or 'tout le cours'}"]
    subject = f"Pirouette · question signalée · {quiz.get('course_name') or quiz.get('title')}"
    return subject, "\n".join(lines)


@app.post("/api/feedback")
async def send_feedback(body: FeedbackIn) -> dict:
    """Enregistre le signalement puis prépare le mail."""
    quiz = store.get_quiz(body.quiz_id)
    if not 0 <= body.index < len(quiz["questions"]):
        raise HTTPException(400, "Question introuvable.")
    question = quiz["questions"][body.index]
    subject, text = feedback_email(quiz, question, body.given, body.message)
    store.add_feedback({"quiz_id": quiz["id"], "question": question, "given": body.given, "message": body.message})
    return _mail(subject, text)


def _mail(subject: str, text: str) -> dict:
    """Prépare le mail : Pirouette n'a pas de serveur, c'est l'app Mail qui l'envoie."""
    from urllib.parse import quote

    mailto = f"mailto:{FEEDBACK_EMAIL}?subject={quote(subject)}&body={quote(text)}"
    opened = False
    if os.getenv("PIROUETTE_DESKTOP") == "1" and sys.platform == "darwin":
        # Dans l'app, on demande à macOS d'ouvrir le mail prêt à partir dans Mail (ou l'app de mail par défaut).
        opened = subprocess.run(["open", mailto], capture_output=True).returncode == 0
    return {"mailto": mailto, "opened": opened, "to": FEEDBACK_EMAIL, "subject": subject, "body": text}


FEEDBACK_KINDS = {"idee": "Idée", "bug": "Bug", "autre": "Autre"}


class GeneralFeedbackIn(BaseModel):
    kind: str = "idee"
    message: str
    page: str = ""  # où l'on était dans l'app


@app.post("/api/feedback/general")
async def general_feedback(body: GeneralFeedbackIn) -> dict:
    """Un retour sur l'app (idée, bug…), depuis le bouton Feedback du menu."""
    from . import __version__

    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Écris ton retour avant de l'envoyer.")
    kind = FEEDBACK_KINDS.get(body.kind, "Autre")
    store.add_feedback({"kind": kind, "message": message, "page": body.page[:200]})
    text = "\n".join([message, "", f"Type : {kind}", f"Page : {body.page or '—'}", f"Pirouette {__version__}"])
    return _mail(f"Pirouette · retour ({kind})", text)
