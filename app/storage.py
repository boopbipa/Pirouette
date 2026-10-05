"""Stockage sur disque des cours, de leurs fichiers, des quiz et des flashcards.

data/
  courses/<id>/course.json        nom, liste des fichiers, version
  courses/<id>/files/<fid>.<ext>  fichier original tel que déposé
  courses/<id>/files/<fid>.txt    texte extrait
  courses/<id>/cards.json         flashcards et leur suivi (apprises / à revoir)
  quizzes/<id>.json               quiz (avec course_id)
  folders.json                    dossiers de cours (un semestre, une UE…), archivés ou non
  activity.json                   suivi des révisions : par jour et par cours, cartes et questions (réussies ou non)
  profile.json                    prénom affiché sur l'accueil
  settings.json                   réglages (clé API Claude), lisible par l'utilisateur seul
"""

from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import srs


class NotFound(KeyError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _day(iso: str) -> date:
    return datetime.fromisoformat(iso).date()


def _new_id(length: int = 12) -> str:
    return uuid.uuid4().hex[:length]


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _check_day(value: str) -> str | None:
    """Date « AAAA-MM-JJ », ou "" pour l'effacer."""
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("Date invalide.") from exc


def _check_id(value: str, length: int = 12) -> str:
    if not re.fullmatch(rf"[0-9a-f]{{{length}}}", value or ""):
        raise NotFound(value)
    return value


# Ce qui fait le contenu d'une carte (le reste est son suivi : statut, intervalle, dates…)
CARD_CONTENT = {"id", "front", "back", "source", "scope", "origin", "created_at", "figure", "image"}

class Store:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def courses_dir(self) -> Path:
        return self.root / "courses"

    @property
    def quizzes_dir(self) -> Path:
        return self.root / "quizzes"

    # ---------- Profil et statistiques ----------
    def get_profile(self) -> dict:
        path = self.root / "profile.json"
        return _read(path) if path.exists() else {"name": "", "asked": False}

    def save_profile(self, name: str) -> dict:
        profile = {"name": name.strip()[:40], "asked": True}
        _write(self.root / "profile.json", profile)
        return profile

    def get_settings(self) -> dict:
        path = self.root / "settings.json"
        return _read(path) if path.exists() else {}

    def save_settings(self, **changes) -> dict:
        settings = self.get_settings() | changes
        settings = {k: v for k, v in settings.items() if v not in (None, "")}
        path = self.root / "settings.json"
        _write(path, settings)
        path.chmod(0o600)
        return settings

    def stats(self, today: date | None = None) -> dict:
        """Compteurs de l'accueil : cours, quiz, cartes, jours de révision d'affilée, réussite des 7 derniers jours."""
        today = today or date.today()
        courses = self.list_courses()
        quizzes = self.list_quizzes()
        days: set[date] = {date.fromisoformat(d) for d in self.activity()}
        decks = cards_known = cards_total = 0
        for course in courses:
            deck = self.get_doc(course["id"], "cards")
            if deck:
                decks += 1
                cards_total += len(deck["cards"])
                cards_known += sum(c["status"] == "known" for c in deck["cards"])
                days |= {_day(c["last_reviewed"]) for c in deck["cards"] if c.get("last_reviewed")}
        week_score = week_total = 0
        for path in self.quizzes_dir.glob("*.json"):
            for attempt in _read(path).get("attempts", []):
                day = _day(attempt["date"])
                days.add(day)
                if (today - day).days < 7:
                    week_score, week_total = week_score + attempt["score"], week_total + attempt["total"]
        # Jours d'affilée : jusqu'à aujourd'hui, ou jusqu'à hier si on n'a pas encore révisé aujourd'hui.
        streak, day = 0, today if today in days else today - timedelta(days=1)
        while day in days:
            streak, day = streak + 1, day - timedelta(days=1)
        return {"courses": len(courses), "quizzes": len(quizzes),
                "quizzes_done": sum(q["attempts"] for q in quizzes),
                "decks": decks, "cards_known": cards_known, "cards_review": cards_total - cards_known,
                "streak": streak, "week_success": round(100 * week_score / week_total) if week_total else None,
                "cards_today": len(self.today_cards(self.active_course_ids(), today))}

    # ---------- Suivi des révisions ----------
    def activity(self) -> dict:
        path = self.root / "activity.json"
        return _read(path) if path.exists() else {}

    def log_activity(self, course_id: str | None, day: date | None = None, **counts: int) -> None:
        """Ajoute au journal du jour : cartes / questions révisées et réussies, nouvelles cartes vues."""
        activity = self.activity()
        entry = activity.setdefault((day or date.today()).isoformat(), {}).setdefault(course_id or "-", {})
        for key, value in counts.items():
            entry[key] = entry.get(key, 0) + value
        _write(self.root / "activity.json", activity)

    def _new_seen_today(self, today: date) -> int:
        return sum(c.get("new", 0) for c in self.activity().get(today.isoformat(), {}).values())

    def _decks(self, course_ids: list[str] | None = None):
        """(cours, cartes) de chaque cours choisi (tous par défaut)."""
        for course in self.list_courses():
            if course_ids is not None and course["id"] not in course_ids:
                continue
            cards = (self.get_doc(course["id"], "cards") or {}).get("cards", [])
            if cards:
                yield course, cards

    def active_course_ids(self) -> list[str]:
        """Cours révisés par défaut : tous, sauf ceux des dossiers archivés (les semestres passés)."""
        archived = {f["id"] for f in self.list_folders() if f.get("archived")}
        return [c["id"] for c in self.list_courses() if c.get("folder_id") not in archived]

    def review_questions(self, course_ids: list[str] | None = None, today: date | None = None) -> list[dict]:
        """Questions de quiz à reposer : jamais répondues, ou réussies il y a plus d'une semaine (les plus anciennes
        d'abord). Les questions ratées sont dans `weak_questions`."""
        today = today or date.today()
        items = []
        for path in self.quizzes_dir.glob("*.json"):
            quiz = _read(path)
            if course_ids is not None and quiz.get("course_id") not in course_ids:
                continue
            stats = quiz.get("stats") or {}
            for index in range(len(quiz["questions"])):
                stat = stats.get(str(index))
                if stat is None or (stat.get("last") and (today - _day(stat["date"])).days >= 7):
                    items.append({"quiz": quiz, "index": index, "stat": stat})
        return sorted(items, key=lambda i: (i["stat"] or {}).get("date") or "")

    def today_cards(self, course_ids: list[str] | None = None, today: date | None = None) -> list[dict]:
        """Cartes du jour : celles dont la date de rappel est arrivée (les plus en retard d'abord), puis des
        nouvelles cartes, au plus `srs.NEW_PER_DAY` par jour. Chaque élément : {course, card}."""
        today = today or date.today()
        due, new = [], []
        needed = 0  # nouvelles cartes à voir chaque jour pour tout avoir vu avant les partiels
        for course, cards in self._decks(course_ids):
            exam = self.exam_info(course, today)
            fresh = []
            for card in cards:
                item = {"course": course, "card": card, "exam": exam["days"] if exam and exam["days"] > 0 else 9999}
                if srs.is_new(card) and card.get("status") != "known":
                    fresh.append(item)
                elif srs.is_due(card, today):
                    due.append(item)
            new += fresh
            if exam and 0 < exam["days"] <= 90 and fresh:
                needed += math.ceil(len(fresh) / max(1, exam["days"] - 2))
        due.sort(key=lambda i: srs.due_date(i["card"], today))
        new.sort(key=lambda i: i["exam"])  # d'abord les cours dont les partiels approchent
        quota = max(0, max(self.new_per_day(), needed) - self._new_seen_today(today))
        return due + new[:quota]

    def new_per_day(self) -> int:
        return int(self.get_settings().get("new_per_day", srs.NEW_PER_DAY))

    def weak_cards(self, course_ids: list[str] | None = None) -> list[dict]:
        items = [{"course": course, "card": card} for course, cards in self._decks(course_ids)
                 for card in cards if srs.is_weak(card)]
        return sorted(items, key=lambda i: srs.weakness(i["card"]))

    def weak_questions(self, course_ids: list[str] | None = None) -> list[dict]:
        """Questions de quiz ratées la dernière fois, ou plus souvent ratées que réussies."""
        items = []
        for path in self.quizzes_dir.glob("*.json"):
            quiz = _read(path)
            if course_ids is not None and quiz.get("course_id") not in course_ids:
                continue
            for key, stat in (quiz.get("stats") or {}).items():
                index = int(key)
                if index < len(quiz["questions"]) and (stat.get("last") is False or stat["wrong"] > stat["right"]):
                    items.append({"quiz": quiz, "index": index, "stat": stat})
        return sorted(items, key=lambda i: (i["stat"]["right"] - i["stat"]["wrong"], i["stat"].get("date") or ""))

    def progress(self, today: date | None = None) -> dict:
        """Page Suivi : révisions des 12 dernières semaines, prévisions des 7 prochains jours, bilan par cours."""
        today = today or date.today()
        activity = self.activity()
        days = []
        for offset in range(83, -1, -1):
            day = today - timedelta(days=offset)
            entries = activity.get(day.isoformat(), {}).values()
            total = {k: sum(e.get(k, 0) for e in entries) for k in ("cards", "cards_ok", "questions", "questions_ok")}
            days.append({"date": day.isoformat(), **total})
        forecast = [0] * 7
        courses = []
        week = days[-7:]
        for course in self.list_courses():
            cards = (self.get_doc(course["id"], "cards") or {}).get("cards", [])
            for card in cards:
                if not srs.is_new(card) or card.get("status") == "known":
                    ahead = (srs.due_date(card, today) - today).days
                    if ahead < 7:
                        forecast[max(0, ahead)] += 1
            recent = [e for d, per_course in activity.items() if (today - date.fromisoformat(d)).days < 30
                      for cid, e in per_course.items() if cid == course["id"]]
            answered = sum(e.get("questions", 0) for e in recent)
            reviewed = sum(e.get("cards", 0) for e in recent)
            courses.append({
                "id": course["id"], "name": course["name"], "folder_id": course.get("folder_id"),
                "exam": self.exam_info(course, today),
                "cards": len(cards), "known": sum(c["status"] == "known" for c in cards),
                "today": len(self.today_cards([course["id"]], today)),
                "weak": len(self.weak_cards([course["id"]])) + len(self.weak_questions([course["id"]])),
                "cards_success": round(100 * sum(e.get("cards_ok", 0) for e in recent) / reviewed) if reviewed else None,
                "questions_success": round(100 * sum(e.get("questions_ok", 0) for e in recent) / answered) if answered else None,
                "last": max((d for d, per_course in activity.items() if course["id"] in per_course), default=None),
            })
        week_cards = sum(d["cards"] for d in week)
        week_questions = sum(d["questions"] for d in week)
        return {
            "days": days, "forecast": forecast, "courses": courses,
            "week": {"cards": week_cards, "questions": week_questions,
                     "cards_success": round(100 * sum(d["cards_ok"] for d in week) / week_cards) if week_cards else None,
                     "questions_success": round(100 * sum(d["questions_ok"] for d in week) / week_questions)
                     if week_questions else None},
            "active_days": sum(1 for d in days if d["cards"] or d["questions"]),
        }

    def review_cards(self) -> list[dict]:
        """Cartes à revoir de tous les cours, regroupées par cours."""
        groups = []
        for course in self.list_courses():
            cards = [c for c in (self.get_doc(course["id"], "cards") or {}).get("cards", []) if c["status"] != "known"]
            if cards:
                groups.append({"course_id": course["id"], "course_name": course["name"], "cards": cards})
        return groups

    # ---------- Cours ----------
    def _course_dir(self, course_id: str) -> Path:
        path = self.courses_dir / _check_id(course_id)
        if not (path / "course.json").exists():
            raise NotFound(course_id)
        return path

    def create_course(self, name: str) -> dict:
        course = {"id": _new_id(), "name": name.strip() or "Nouveau cours", "created_at": _now(),
                  "updated_at": _now(), "version": 0, "files": []}
        _write(self.courses_dir / course["id"] / "course.json", course)
        return course

    def get_course(self, course_id: str) -> dict:
        return _read(self._course_dir(course_id) / "course.json")

    def _save_course(self, course: dict) -> None:
        _write(self.courses_dir / course["id"] / "course.json", course)

    def list_courses(self) -> list[dict]:
        quizzes = self.list_quizzes()
        courses = []
        for path in self.courses_dir.glob("*/course.json"):
            course = _read(path)
            own = [q for q in quizzes if q.get("course_id") == course["id"]]
            deck = self.get_doc(course["id"], "cards") or {}
            courses.append(course | {"quiz_count": len(own), "card_count": len(deck.get("cards", []))})
        return sorted(courses, key=lambda c: c["updated_at"], reverse=True)

    def move_course(self, course_id: str, folder_id: str | None) -> dict:
        """Range le cours dans un dossier (None : le sortir de son dossier)."""
        course = self.get_course(course_id)
        if folder_id is not None:
            self.get_folder(folder_id)
        course["folder_id"] = folder_id
        self._save_course(course)
        return course

    # ---------- Dossiers de cours ----------
    def list_folders(self) -> list[dict]:
        path = self.root / "folders.json"
        return _read(path) if path.exists() else []

    def _save_folders(self, folders: list[dict]) -> None:
        _write(self.root / "folders.json", folders)

    def get_folder(self, folder_id: str) -> dict:
        folder = next((f for f in self.list_folders() if f["id"] == folder_id), None)
        if folder is None:
            raise NotFound(folder_id)
        return folder

    def create_folder(self, name: str) -> dict:
        folders = self.list_folders()
        folder = {"id": _new_id(8), "name": name.strip()[:80] or "Nouveau dossier", "archived": False,
                  "created_at": _now()}
        self._save_folders([*folders, folder])
        return folder

    def update_folder(self, folder_id: str, name: str | None = None, archived: bool | None = None,
                      exam_week: str | None = None) -> dict:
        folders = self.list_folders()
        folder = next((f for f in folders if f["id"] == folder_id), None)
        if folder is None:
            raise NotFound(folder_id)
        if name is not None and name.strip():
            folder["name"] = name.strip()[:80]
        if archived is not None:
            folder["archived"] = archived
        if exam_week is not None:
            folder["exam_week"] = _check_day(exam_week)
        self._save_folders(folders)
        return folder

    def delete_folder(self, folder_id: str) -> None:
        """Supprime le dossier seulement : ses cours reviennent dans « Mes cours »."""
        self.get_folder(folder_id)
        for path in self.courses_dir.glob("*/course.json"):
            course = _read(path)
            if course.get("folder_id") == folder_id:
                course["folder_id"] = None
                _write(path, course)
        self._save_folders([f for f in self.list_folders() if f["id"] != folder_id])

    # ---------- Plan de révision et rétroplanning (sur un semestre, ou un cours sans semestre) ----------
    def _owner(self, kind: str, owner_id: str) -> dict:
        return self.get_folder(owner_id) if kind == "dossier" else self.get_course(owner_id)

    def _update_owner(self, kind: str, owner_id: str, **fields) -> dict:
        if kind == "dossier":
            folders = self.list_folders()
            folder = next((f for f in folders if f["id"] == owner_id), None)
            if folder is None:
                raise NotFound(owner_id)
            folder.update(fields)
            self._save_folders(folders)
            return folder
        course = self.get_course(owner_id)
        course.update(fields)
        self._save_course(course)
        return course

    def get_plan(self, kind: str, owner_id: str) -> dict | None:
        return self._owner(kind, owner_id).get("plan")

    def set_plan(self, kind: str, owner_id: str, plan: dict | None) -> dict:
        return self._update_owner(kind, owner_id, plan=plan)

    def get_retro(self, kind: str, owner_id: str) -> dict | None:
        return self._owner(kind, owner_id).get("retro")

    def set_retro(self, kind: str, owner_id: str, retro: dict | None) -> dict:
        return self._update_owner(kind, owner_id, retro=retro)

    def scope_course_ids(self, kind: str, owner_id: str) -> list[str]:
        if kind == "dossier":
            return [c["id"] for c in self.list_courses() if c.get("folder_id") == owner_id]
        return [owner_id]

    def active_days(self, course_ids: list[str]) -> set[date]:
        """Jours où l'on a révisé (cartes ou questions) au moins un de ces cours."""
        wanted = set(course_ids)
        return {date.fromisoformat(day) for day, per_course in self.activity().items()
                if any(cid in wanted and (e.get("cards") or e.get("questions")) for cid, e in per_course.items())}

    def plans(self) -> list[tuple[str, str, str, dict]]:
        """Plans en cours : (type, id, nom, plan) — semestres non archivés et cours sans semestre."""
        found = [("dossier", f["id"], f["name"], f["plan"]) for f in self.list_folders()
                 if f.get("plan") and not f.get("archived")]
        known = {f["id"] for f in self.list_folders()}
        found += [("cours", c["id"], c["name"], c["plan"]) for c in self.list_courses()
                  if c.get("plan") and c.get("folder_id") not in known]
        return found

    # ---------- Semaine des partiels ----------
    def set_exam_week(self, course_id: str, day: str) -> dict:
        course = self.get_course(course_id)
        course["exam_week"] = _check_day(day)
        self._save_course(course)
        return course

    def exam_info(self, course: dict, today: date | None = None) -> dict | None:
        """Semaine des partiels d'un cours (la sienne, sinon celle de son dossier) : {date, days, from}."""
        today = today or date.today()
        source, day = "cours", course.get("exam_week")
        if not day and course.get("folder_id"):
            folder = next((f for f in self.list_folders() if f["id"] == course["folder_id"]), None)
            source, day = "dossier", (folder or {}).get("exam_week")
        if not day:
            return None
        start = date.fromisoformat(day)
        return {"date": day, "days": (start - today).days, "from": source}

    def exam_date(self, course_id: str) -> date | None:
        info = self.exam_info(self.get_course(course_id))
        return date.fromisoformat(info["date"]) if info else None

    def next_exam(self, today: date | None = None) -> dict | None:
        """Prochaine semaine de partiels (cours hors archives), pour l'accueil. En cours : jours négatifs (> -7)."""
        today = today or date.today()
        best = None
        active = set(self.active_course_ids())
        for course in self.list_courses():
            info = self.exam_info(course, today) if course["id"] in active else None
            if info and info["days"] > -7 and (best is None or info["days"] < best["days"]):
                best = info | {"course": course["name"]}
        return best

    def rename_course(self, course_id: str, name: str) -> dict:
        course = self.get_course(course_id)
        course["name"] = name.strip() or course["name"]
        self._save_course(course)
        return course

    def delete_course(self, course_id: str) -> None:
        path = self._course_dir(course_id)
        for quiz in self.list_quizzes(course_id):
            (self.quizzes_dir / f"{quiz['id']}.json").unlink(missing_ok=True)
        shutil.rmtree(path)

    def add_file(self, course_id: str, filename: str, data: bytes, text: str, chapters: list[dict] | None = None,
                 extract_version: int = 1, chapters_by: str = "auto") -> str:
        """Ajoute un fichier au cours, ou remplace celui qui porte le même nom. Renvoie "added" ou "updated"."""
        course = self.get_course(course_id)
        files_dir = self._course_dir(course_id) / "files"
        files_dir.mkdir(exist_ok=True)
        name = Path(filename).name or "fichier"
        existing = next((f for f in course["files"] if f["name"].lower() == name.lower()), None)
        entry = existing or {"id": _new_id(8), "name": name, "added_at": _now(), "revisions": 0}
        # Nouvelle version : le texte de l'ancienne est gardé pour repérer les passages nouveaux (voir news.py).
        # S'il en reste un plus ancien (nouveautés pas encore traitées), c'est lui la référence.
        previous_path = files_dir / f"{entry['id']}.prev.txt"
        previous = previous_path.read_text(encoding="utf-8") if previous_path.exists() else None
        if existing and previous is None and (files_dir / f"{entry['id']}.txt").exists():
            previous = (files_dir / f"{entry['id']}.txt").read_text(encoding="utf-8")
        for old in files_dir.glob(f"{entry['id']}.*"):
            old.unlink()
        if previous is not None and previous != text:
            previous_path.write_text(previous, encoding="utf-8")
        ext = re.sub(r"[^a-z0-9.]", "", Path(name).suffix.lower()) or ".bin"
        (files_dir / f"{entry['id']}{ext}.orig").write_bytes(data)
        (files_dir / f"{entry['id']}.txt").write_text(text, encoding="utf-8")
        entry.update(name=name, size=len(data), chars=len(text), updated_at=_now(), revisions=entry["revisions"] + 1,
                     chapters=chapters or [], chapters_by=chapters_by, extract_version=extract_version)
        if existing is None:
            course["files"].append(entry)
        course["version"] += 1
        course["updated_at"] = _now()
        self._save_course(course)
        return "updated" if existing else "added"

    def remove_file(self, course_id: str, file_id: str) -> dict:
        course = self.get_course(course_id)
        _check_id(file_id, 8)
        if not any(f["id"] == file_id for f in course["files"]):
            raise NotFound(file_id)
        course["files"] = [f for f in course["files"] if f["id"] != file_id]
        for path in (self._course_dir(course_id) / "files").glob(f"{file_id}.*"):
            path.unlink()
        course["version"] += 1
        course["updated_at"] = _now()
        self._save_course(course)
        return course

    def original_file(self, course_id: str, file_id: str) -> tuple[str, bytes] | None:
        """Le fichier tel que déposé (nom, contenu), pour le relire avec une version plus récente de l'extraction."""
        course = self.get_course(course_id)
        entry = next((f for f in course["files"] if f["id"] == file_id), None)
        path = next((self._course_dir(course_id) / "files").glob(f"{_check_id(file_id, 8)}.*.orig"), None)
        return (entry["name"], path.read_bytes()) if entry and path else None

    def replace_text(self, course_id: str, file_id: str, text: str, extract_version: int,
                     chapters: list[dict] | None = None) -> None:
        """Remplace le texte extrait d'un fichier (même contenu, mieux lu) sans changer de version du cours."""
        course = self.get_course(course_id)
        entry = next(f for f in course["files"] if f["id"] == file_id)
        (self._course_dir(course_id) / "files" / f"{file_id}.txt").write_text(text, encoding="utf-8")
        entry.update(chars=len(text), extract_version=extract_version)
        if chapters is not None:
            entry.update(chapters=chapters)
        self._save_course(course)

    def figure_index(self, course_id: str, file_id: str) -> dict:
        """Les figures repérées dans un fichier (voir figures.py), calculées une fois puis gardées à côté du fichier."""
        from . import figures

        cache = self._course_dir(course_id) / "files" / f"{_check_id(file_id, 8)}.figures.json"
        if cache.exists():
            try:
                index = json.loads(cache.read_text(encoding="utf-8"))
                if index.get("version") == figures.INDEX_VERSION:
                    return index
            except (json.JSONDecodeError, OSError):
                pass
        original = self.original_file(course_id, file_id)
        if original is None or not figures.supported(original[0]):
            return {"version": figures.INDEX_VERSION, "pages": {}}
        try:
            index = figures.build_index(*original)
        except Exception:  # fichier abîmé : pas de figures, le texte reste lisible
            index = {"version": figures.INDEX_VERSION, "pages": {}}
        cache.write_text(json.dumps(index), encoding="utf-8")
        return index

    def previous_text(self, course_id: str, file_id: str) -> str | None:
        """Le texte de la version précédente d'un fichier, tant que ses nouveautés n'ont pas été traitées."""
        path = self._course_dir(course_id) / "files" / f"{_check_id(file_id, 8)}.prev.txt"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def forget_previous(self, course_id: str) -> None:
        """Nouveautés traitées (ou écartées) : on oublie les anciennes versions."""
        for path in (self._course_dir(course_id) / "files").glob("*.prev.txt"):
            path.unlink()

    def file_text(self, course_id: str, file_id: str) -> str:
        return (self._course_dir(course_id) / "files" / f"{_check_id(file_id, 8)}.txt").read_text(encoding="utf-8")

    def set_chapters(self, course_id: str, file_id: str, chapters: list[dict], by: str,
                     revision: int | None = None) -> dict:
        """Enregistre les chapitres d'un fichier (sauf s'il a été remplacé entre-temps : `revision` ne correspond plus)."""
        course = self.get_course(course_id)
        entry = next((f for f in course["files"] if f["id"] == file_id), None)
        if entry is None:
            raise NotFound(file_id)
        if revision is None or entry["revisions"] == revision:
            entry.update(chapters=chapters, chapters_by=by)
            self._save_course(course)
        return course

    def course_sources(self, course_id: str) -> list[tuple[str, str]]:
        course = self.get_course(course_id)
        files_dir = self._course_dir(course_id) / "files"
        return [(f["name"], (files_dir / f"{f['id']}.txt").read_text(encoding="utf-8")) for f in course["files"]]

    # ---------- Flashcards ----------
    def _doc_path(self, course_id: str, kind: str) -> Path:
        return self._course_dir(course_id) / f"{kind}.json"

    def save_doc(self, course_id: str, kind: str, doc: dict) -> dict:
        """Enregistre le paquet de cartes (`cards`) du cours, en remplaçant le précédent."""
        doc.update(created_at=_now())
        _write(self._doc_path(course_id, kind), doc)
        return doc

    def get_doc(self, course_id: str, kind: str) -> dict | None:
        path = self._doc_path(course_id, kind)
        return _read(path) if path.exists() else None

    def delete_doc(self, course_id: str, kind: str) -> None:
        self._doc_path(course_id, kind).unlink(missing_ok=True)

    def review_card(self, course_id: str, card_id: str, known: bool | None = None, rating: str | None = None) -> dict:
        """Réponse à une carte : `rating` (again / hard / good / easy), ou simplement sue / pas sue (`known`)."""
        deck, card = self._deck_card(course_id, card_id)
        rating = rating or ("good" if known else "again")
        was_new = srs.is_new(card)
        # L'état d'avant, pour revenir en arrière pendant la séance (« Précédent ») et changer d'avis
        before = {k: v for k, v in card.items() if k not in CARD_CONTENT and k != "undo"}
        srs.schedule(card, rating, exam=self.exam_date(course_id))
        card["undo"] = {"before": before, "ok": int(rating != "again"), "new": int(was_new), "day": date.today().isoformat()}
        _write(self._doc_path(course_id, "cards"), deck)
        self.log_activity(course_id, cards=1, cards_ok=int(rating != "again"), new=int(was_new))
        return card

    def undo_review(self, course_id: str, card_id: str) -> dict:
        """Annule la dernière réponse à une carte : elle retrouve son état d'avant (le journal du jour aussi)."""
        deck, card = self._deck_card(course_id, card_id)
        undo = card.pop("undo", None)
        if undo:
            content = {k: v for k, v in card.items() if k in CARD_CONTENT}
            card.clear()
            card.update(undo["before"] | content)
            self.log_activity(course_id, day=date.fromisoformat(undo["day"]), cards=-1, cards_ok=-undo["ok"], new=-undo["new"])
        _write(self._doc_path(course_id, "cards"), deck)
        return card

    def _deck_card(self, course_id: str, card_id: str) -> tuple[dict, dict]:
        deck = self.get_doc(course_id, "cards")
        card = next((c for c in (deck or {}).get("cards", []) if c["id"] == card_id), None)
        if card is None:
            raise NotFound(card_id)
        return deck, card

    def update_card(self, course_id: str, card_id: str, front: str, back: str) -> dict:
        """Corrige le recto / verso d'une carte, en gardant son suivi."""
        deck, card = self._deck_card(course_id, card_id)
        if (front, back) != (card["front"], card["back"]):
            card.update(front=front, back=back, edited_at=_now())
            _write(self._doc_path(course_id, "cards"), deck)
        return card

    def add_card(self, course_id: str, front: str, back: str) -> dict:
        """Carte écrite à la main, ajoutée au paquet du cours (créé s'il n'existe pas encore)."""
        course = self.get_course(course_id)
        deck = self.get_doc(course_id, "cards") or {"course_id": course_id, "course_name": course["name"],
                                                    "cards": [], "created_at": _now()}
        card = {"id": uuid.uuid4().hex[:8], "front": front, "back": back, "source": "", "status": "new",
                "reviews": 0, "last_reviewed": None, "scope": [], "created_at": _now(), "manual": True}
        deck["cards"].append(card)
        _write(self._doc_path(course_id, "cards"), deck)
        return card

    def remove_card(self, course_id: str, card_id: str) -> dict:
        deck = self.get_doc(course_id, "cards")
        cards = (deck or {}).get("cards", [])
        if not any(c["id"] == card_id for c in cards):
            raise NotFound(card_id)
        deck["cards"] = [c for c in cards if c["id"] != card_id]
        _write(self._doc_path(course_id, "cards"), deck)
        return deck

    # ---------- Quiz ----------
    def _quiz_path(self, quiz_id: str) -> Path:
        path = self.quizzes_dir / f"{_check_id(quiz_id)}.json"
        if not path.exists():
            raise NotFound(quiz_id)
        return path

    def save_quiz(self, quiz: dict) -> dict:
        quiz.setdefault("id", _new_id())
        quiz.setdefault("created_at", _now())
        quiz.setdefault("attempts", [])
        _write(self.quizzes_dir / f"{quiz['id']}.json", quiz)
        return quiz

    def get_quiz(self, quiz_id: str) -> dict:
        return _read(self._quiz_path(quiz_id))

    # ---------- Un quiz par chapitre ----------
    @staticmethod
    def chapter_key(quiz: dict) -> tuple | None:
        """Les quiz d'un même cours sur les mêmes chapitres se regroupent. Pas les quiz sur tout le cours ni les
        quiz sur un thème précis. « Chapitre 1 — X » et « Chapitre 1 : X » désignent le même chapitre."""
        scope = quiz.get("scope") or []
        if not scope or not quiz.get("course_id") or quiz.get("focus"):
            return None
        return quiz["course_id"], tuple(sorted(" ".join(re.findall(r"\w+", str(t).lower())) for t in scope))

    def chapter_quiz(self, key: tuple) -> dict | None:
        """Le quiz déjà créé sur ces chapitres (le plus ancien), s'il y en a un."""
        same = [self.get_quiz(q["id"]) for q in self.list_quizzes(key[0]) if self.chapter_key(q) == key]
        return min(same, key=lambda q: q.get("created_at") or "") if same else None

    def add_to_quiz(self, quiz_id: str, questions: list[dict]) -> tuple[dict, int]:
        """Ajoute des questions à un quiz, sauf celles qui ressemblent à une question qu'il contient déjà."""
        from .revision import is_duplicate

        quiz = self.get_quiz(quiz_id)
        notion = lambda q: {"front": q["question"], "back": "" if q["type"] == "vrai_faux" else q["answer"]}  # noqa: E731
        known = [notion(q) for q in quiz["questions"]]
        added = 0
        for question in questions:
            if not is_duplicate(**notion(question), others=known):
                quiz["questions"].append(question)
                known.append(notion(question))
                added += 1
        quiz["updated_at"] = _now()
        _write(self._quiz_path(quiz_id), quiz)
        return quiz, added

    def merge_chapter_quizzes(self) -> int:
        """Regroupe les quiz qui portent sur les mêmes chapitres : les questions des plus récents rejoignent le plus
        ancien (sans les questions semblables), avec leur suivi (réussites, ratés). Renvoie le nombre de quiz fondus."""
        from .revision import is_duplicate

        groups: dict[tuple, list[dict]] = {}
        for summary in self.list_quizzes():
            key = self.chapter_key(summary)
            if key:
                groups.setdefault(key, []).append(summary)
        merged = 0
        for key, members in groups.items():
            if len(members) < 2:
                continue
            members.sort(key=lambda q: q.get("created_at") or "")
            target = self.get_quiz(members[0]["id"])
            notion = lambda q: {"front": q["question"], "back": "" if q["type"] == "vrai_faux" else q["answer"]}  # noqa: E731
            known = [notion(q) for q in target["questions"]]
            stats = target.setdefault("stats", {})
            renamed: dict[str, str] = {}
            for summary in members[1:]:
                other = self.get_quiz(summary["id"])
                for index, question in enumerate(other["questions"]):
                    if is_duplicate(**notion(question), others=known):
                        continue
                    target["questions"].append(question)
                    known.append(notion(question))
                    if str(index) in (other.get("stats") or {}):
                        stats[str(len(target["questions"]) - 1)] = other["stats"][str(index)]
                    renamed[f"{other['id']}:{question['question']}"] = f"{target['id']}:{question['question']}"
                if not target.get("attempts") and other.get("attempts"):
                    target["attempts"] = other["attempts"]  # garde au moins un score à afficher
                self._quiz_path(other["id"]).unlink()
                merged += 1
            _write(self._quiz_path(target["id"]), target)
            if renamed:  # questions « gardées malgré le cours mis à jour » : elles changent de quiz
                try:
                    course = self.get_course(key[0])
                except NotFound:
                    continue
                if course.get("kept_outdated"):
                    course["kept_outdated"] = [renamed.get(k, k) for k in course["kept_outdated"]]
                    self._save_course(course)
        return merged

    def rename_quiz(self, quiz_id: str, title: str) -> dict:
        """Nouveau nom, affiché tel quel (sans le « Quiz 3 · » ajouté aux quiz jamais renommés)."""
        quiz = self.get_quiz(quiz_id)
        quiz["title"] = title.strip()[:160] or quiz["title"]
        quiz["custom_title"] = True
        _write(self._quiz_path(quiz_id), quiz)
        return quiz

    def remove_question(self, quiz_id: str, index: int) -> dict:
        """Retire une question du quiz (hors sujet…). Le suivi des questions suivantes est décalé d'un rang."""
        quiz = self.get_quiz(quiz_id)
        if not 0 <= index < len(quiz["questions"]):
            raise NotFound(str(index))
        quiz["questions"].pop(index)
        stats = quiz.get("stats") or {}
        quiz["stats"] = {str(int(k) - (int(k) > index)): v for k, v in stats.items() if int(k) != index}
        _write(self._quiz_path(quiz_id), quiz)
        return quiz

    # ---------- Cours mis à jour ----------
    def outdated_items(self, course_id: str) -> dict:
        """Questions et cartes dont la phrase du cours (« source ») n'est plus dans le cours : un fichier a été
        remplacé par une nouvelle version, ou retiré. Celles que l'étudiant a décidé de garder ne sont plus signalées."""
        from .grounding import Grounding

        course = self.get_course(course_id)
        kept = set(course.get("kept_outdated", []))
        grounding = Grounding("\n\n".join(text for _, text in self.course_sources(course_id)))
        questions, cards = [], []
        for summary in self.list_quizzes(course_id):
            quiz = self.get_quiz(summary["id"])
            for index, q in enumerate(quiz["questions"]):
                key = f"{quiz['id']}:{q['question']}"
                if q.get("source") and key not in kept and not grounding.quote_found(q["source"]):
                    questions.append({"quiz_id": quiz["id"], "quiz_title": quiz["title"], "index": index,
                                      "question": q["question"], "source": q["source"], "key": key})
        for card in (self.get_doc(course_id, "cards") or {}).get("cards", []):
            key = f"card:{card['id']}"
            if card.get("source") and key not in kept and not grounding.quote_found(card["source"]):
                cards.append({"id": card["id"], "front": card["front"], "source": card["source"], "key": key})
        return {"questions": questions, "cards": cards}

    def keep_outdated(self, course_id: str, keys: list[str]) -> None:
        course = self.get_course(course_id)
        course["kept_outdated"] = sorted(set(course.get("kept_outdated", [])) | set(keys))
        self._save_course(course)

    def remove_outdated(self, course_id: str, questions: list[dict], card_ids: list[str]) -> None:
        """Supprime des questions ({quiz_id, index}) et des cartes ; les index d'un même quiz, du plus grand au plus petit."""
        for item in sorted(questions, key=lambda q: -int(q["index"])):
            quiz = self.remove_question(item["quiz_id"], int(item["index"]))
            if not quiz["questions"]:
                self.delete_quiz(quiz["id"])
        for card_id in card_ids:
            try:
                self.remove_card(course_id, card_id)
            except NotFound:
                pass

    # ---------- Partiels ----------
    def partiel_items(self, course_id: str, n_questions: int, n_cards: int, today: date | None = None) -> dict:
        """Sujet de partiel : des questions de tous les quiz du cours, réparties équitablement entre les quiz
        (d'abord jamais faites ou ratées, puis les plus anciennes), et des flashcards (d'abord à revoir)."""
        import random

        today = today or date.today()
        pools = []
        for summary in self.list_quizzes(course_id):
            quiz = self.get_quiz(summary["id"])
            stats = quiz.get("stats") or {}

            def priority(i, stats=stats):
                stat = stats.get(str(i))
                if stat is None:
                    return (1, random.random())
                if stat.get("last") is False:
                    return (0, random.random())
                return (2, stat.get("date") or "")
            order = sorted(range(len(quiz["questions"])), key=priority)
            pools.append([{"quiz": quiz, "index": i} for i in order])
        chosen = []
        while len(chosen) < n_questions and any(pools):
            for pool in pools:  # une question de chaque quiz à tour de rôle
                if pool and len(chosen) < n_questions:
                    chosen.append(pool.pop(0))
        cards = (self.get_doc(course_id, "cards") or {}).get("cards", [])
        ranked = sorted(cards, key=lambda c: (0 if c["status"] != "known" else 1 if srs.is_due(c, today) else 2,
                                              random.random()))
        return {"questions": chosen, "cards": ranked[:n_cards],
                "available": {"questions": sum(len(self.get_quiz(q["id"])["questions"]) for q in self.list_quizzes(course_id)),
                              "cards": len(cards)}}

    def reset_stats(self, course_id: str) -> dict:
        """Remet à zéro le suivi d'un cours (scores des quiz, réussites par question, progression des flashcards,
        journal des révisions, notes des partiels) ; les quiz et les cartes eux-mêmes sont gardés."""
        self.get_course(course_id)
        quizzes = 0
        for summary in self.list_quizzes(course_id):
            quiz = self.get_quiz(summary["id"])
            quiz["attempts"], quiz["stats"] = [], {}
            _write(self._quiz_path(quiz["id"]), quiz)
            quizzes += 1
        deck = self.get_doc(course_id, "cards")
        cards = 0
        if deck:
            for card in deck.get("cards", []):
                for key in ("interval", "ease", "due", "lapses", "last_rating"):
                    card.pop(key, None)
                card.update(status="new", reviews=0, last_reviewed=None)
                cards += 1
            self.save_doc(course_id, "cards", deck)
        activity = self.activity()
        for day in list(activity):
            activity[day].pop(course_id, None)
            if not activity[day]:
                del activity[day]
        _write(self.root / "activity.json", activity)
        path = self._partiels_path(course_id)
        if path.exists():
            path.unlink()
        return {"quizzes": quizzes, "cards": cards}

    def _partiels_path(self, course_id: str) -> Path:
        return self._course_dir(course_id) / "partiels.json"

    def list_partiels(self, course_id: str) -> list[dict]:
        path = self._partiels_path(course_id)
        return _read(path) if path.exists() else []

    def save_partiel(self, course_id: str, result: dict) -> dict:
        """Enregistre (ou met à jour, même `id`) la note d'un partiel."""
        partiels = self.list_partiels(course_id)
        result = {k: result[k] for k in ("id", "score", "points", "total", "duration") if k in result}
        existing = next((p for p in partiels if p["id"] == result.get("id")), None)
        if existing:
            existing.update(result)
            result = existing
        else:
            result = result | {"id": _new_id(8), "date": _now()}
            partiels.append(result)
        _write(self._partiels_path(course_id), partiels)
        return result

    def add_feedback(self, report: dict) -> dict:
        """Garde une copie de chaque signalement (data/feedback.json), même si le mail n'est pas envoyé."""
        path = self.root / "feedback.json"
        reports = _read(path) if path.exists() else []
        report = report | {"id": _new_id(), "date": _now()}
        reports.append(report)
        _write(path, reports)
        return report

    def delete_quiz(self, quiz_id: str) -> None:
        self._quiz_path(quiz_id).unlink()

    def list_quizzes(self, course_id: str | None = None, orphans: bool = False) -> list[dict]:
        summaries = []
        for path in self.quizzes_dir.glob("*.json"):
            quiz = _read(path)
            if course_id is not None and quiz.get("course_id") != course_id:
                continue
            if orphans and quiz.get("course_id"):
                continue
            attempts = quiz.get("attempts", [])
            summaries.append({
                **{k: quiz.get(k) for k in ("id", "title", "created_at", "provider", "model", "difficulty",
                                            "course_id", "course_version", "sources", "scope", "custom_title", "focus")},
                "count": len(quiz.get("questions", [])),
                "attempts": len(attempts),
                "last_score": attempts[-1] if attempts else None,
                "best_score": max(attempts, key=lambda a: a["score"] / max(a["total"], 1)) if attempts else None,
                # Questions réussies la dernière fois qu'elles ont été posées (une banque se voit par morceaux)
                "known": sum(1 for stat in (quiz.get("stats") or {}).values() if stat.get("last")),
            })
        return sorted(summaries, key=lambda q: q["created_at"] or "", reverse=True)

    def record_answers(self, quiz_id: str, answers: list[dict]) -> dict:
        """Réponses données aux questions d'un quiz (dans le quiz ou pendant une révision) : pour repérer
        les points faibles et suivre la réussite."""
        quiz = self.get_quiz(quiz_id)
        stats = quiz.setdefault("stats", {})
        good = 0
        for answer in answers:
            index = int(answer["index"])
            if not 0 <= index < len(quiz["questions"]):
                continue
            stat = stats.setdefault(str(index), {"right": 0, "wrong": 0})
            if answer.get("replaces") is not None:
                # Réponse changée (retour en arrière) : la précédente ne compte plus
                was = bool(answer["replaces"])
                stat["right" if was else "wrong"] = max(0, stat["right" if was else "wrong"] - 1)
                self.log_activity(quiz.get("course_id"), questions=-1, questions_ok=-int(was))
            correct = bool(answer["correct"])
            stat["right" if correct else "wrong"] += 1
            stat.update(last=correct, date=_now())
            good += correct
        _write(self._quiz_path(quiz_id), quiz)
        if answers:
            self.log_activity(quiz.get("course_id"), questions=len(answers), questions_ok=good)
        return stats

    def record_attempt(self, quiz_id: str, score: int, total: int) -> dict:
        quiz = self.get_quiz(quiz_id)
        quiz.setdefault("attempts", []).append({"date": _now(), "score": score, "total": total})
        _write(self._quiz_path(quiz_id), quiz)
        return quiz
