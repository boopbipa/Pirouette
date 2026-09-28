"""Sauvegardes : une copie datée de toutes les données, plus les flashcards et les quiz en CSV.

Chaque sauvegarde est un dossier « Pirouette AAAA-MM-JJ HHhMM » (dans Documents par défaut) qui contient :
- `pirouette-donnees.zip` : tout le dossier de données (cours, fichiers, quiz, cartes, suivi) pour tout récupérer ;
- `flashcards.csv` et `quiz.csv` : lisibles dans Numbers ou Excel, et importables dans Anki.
On garde les 10 dernières. Fréquence réglable : à chaque ouverture de l'app (une fois par jour au plus), chaque
semaine, ou jamais.
"""

from __future__ import annotations

import csv
import json
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

MODES = ("open", "week", "off")
KEEP = 10
PREFIX = "Pirouette "


def default_dir() -> Path:
    return Path.home() / "Documents" / "Pirouette - Sauvegardes"


def backup_dir(store) -> Path:
    return Path(store.get_settings().get("backup_dir") or default_dir())


def due(store, now: datetime | None = None) -> bool:
    """Faut-il sauvegarder maintenant, d'après la fréquence choisie et la dernière sauvegarde ?"""
    now = now or datetime.now()
    settings = store.get_settings()
    mode = settings.get("backup_mode", "week")
    last = settings.get("last_backup")
    if mode == "off":
        return False
    if not last:
        return True
    elapsed = now - datetime.fromisoformat(last)
    return elapsed >= (timedelta(days=7) if mode == "week" else timedelta(hours=20))


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    # « ; » et BOM UTF-8 : Numbers et Excel en français l'ouvrent directement ; Anki reconnaît le séparateur.
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)


def make(store, dest: Path | None = None, now: datetime | None = None) -> Path:
    """Crée une sauvegarde et renvoie son dossier."""
    now = now or datetime.now()
    root = Path(store.root).resolve()
    base = Path(dest) if dest else backup_dir(store)
    base.mkdir(parents=True, exist_ok=True)
    folder = base / f"{PREFIX}{now:%Y-%m-%d %Hh%M}"
    if folder.exists():
        folder = base / f"{PREFIX}{now:%Y-%m-%d %Hh%M%S}"
    folder.mkdir()
    with zipfile.ZipFile(folder / "pirouette-donnees.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or base.resolve() in path.resolve().parents:
                continue
            if path == root / "settings.json":  # sans la clé API : une sauvegarde peut traîner dans Documents
                settings = {k: v for k, v in store.get_settings().items() if k != "anthropic_api_key"}
                archive.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=2))
            else:
                archive.write(path, path.relative_to(root))

    folders = {f["id"]: f["name"] for f in store.list_folders()}
    courses = store.list_courses()
    card_rows = []
    for course in courses:
        for card in (store.get_doc(course["id"], "cards") or {}).get("cards", []):
            card_rows.append([card.get("front", ""), card.get("back", ""), course["name"], folders.get(course.get("folder_id"), ""),
                              ", ".join(card.get("scope") or []), card.get("status", ""), card.get("due") or ""])
    _write_csv(folder / "flashcards.csv",
               ["Recto", "Verso", "Cours", "Semestre", "Chapitre", "Statut", "Prochain rappel"], card_rows)
    names = {c["id"]: c["name"] for c in courses}
    quiz_rows = []
    for summary in store.list_quizzes():
        quiz = store.get_quiz(summary["id"])
        for q in quiz.get("questions", []):
            quiz_rows.append([names.get(quiz.get("course_id"), ""), quiz.get("title", ""), q.get("question", ""), q.get("type", ""),
                              " | ".join(q.get("choices") or []), q.get("answer", ""), q.get("explanation", ""), q.get("source", "")])
    _write_csv(folder / "quiz.csv",
               ["Cours", "Quiz", "Question", "Type", "Propositions", "Réponse", "Explication", "Passage du cours"], quiz_rows)

    old = sorted((p for p in base.iterdir() if p.is_dir() and p.name.startswith(PREFIX)), key=lambda p: p.name)
    for stale in old[:-KEEP]:
        shutil.rmtree(stale, ignore_errors=True)
    store.save_settings(last_backup=now.isoformat(timespec="seconds"))
    return folder


def run_if_due(store) -> Path | None:
    """Au lancement de l'app : sauvegarde si c'est le moment. Une erreur (disque plein…) ne bloque pas l'app."""
    try:
        return make(store) if due(store) else None
    except OSError:
        return None
