"""L'import d'un cours en tâche de fond : il ne dépend plus d'une connexion entre la page et le serveur (qu'une mise
en veille coupait, laissant le scan bloqué). On le suit, on le met en pause, on le reprend — à la page où il s'était
arrêté, même après avoir quitté Pirouette (les pages déjà reconnues d'un scan sont gardées)."""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path


class Paused(Exception):
    """La lecture s'arrête ici (pause demandée, ou tâche relancée ailleurs)."""


class ImportError_(Exception):
    """Le fichier ne peut pas être lu : le message est montré tel quel."""


STALLED_AFTER = 60  # secondes sans avancer : la tâche est sans doute figée (mise en veille), on propose de la relancer


class Imports:
    def __init__(self, root: Path, run):
        """`run(job, files, on_page, caches)` lit et enregistre les fichiers, et renvoie le résultat (comme l'import
        direct). Les fichiers et l'avancée sont gardés dans root/imports/<tâche>/."""
        self.dir = Path(root) / "imports"
        self.run = run
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._load()

    # ---------- Ce qu'on garde sur le disque ----------
    def _job_dir(self, job_id: str) -> Path:
        return self.dir / job_id

    def _save(self, job: dict) -> None:
        public = {k: v for k, v in job.items() if not k.startswith("_")}
        try:
            (self._job_dir(job["id"]) / "job.json").write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _load(self) -> None:
        """Au démarrage : une tâche qui tournait quand Pirouette s'est fermée est en pause (à reprendre)."""
        if not self.dir.exists():
            return
        for path in self.dir.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") == "done":
                shutil.rmtree(path.parent, ignore_errors=True)
                continue
            if job.get("status") == "running":
                job["status"] = "paused"
            job["gen"] = job.get("gen", 0)
            self.jobs[job["id"]] = job

    # ---------- Les tâches ----------
    def start(self, course_id: str, files: list[tuple[str, bytes]], similar: bool) -> dict:
        job_id = uuid.uuid4().hex[:12]
        folder = self._job_dir(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        for index, (name, data) in enumerate(files):
            (folder / f"{index}.bin").write_bytes(data)
        job = {"id": job_id, "course_id": course_id, "names": [n for n, _ in files], "similar": similar,
               "status": "running", "index": 0, "count": len(files), "file": files[0][0] if files else "",
               "page": 0, "pages": 0, "kind": "text", "message": "", "result": None, "updated": time.time(), "gen": 0}
        self.jobs[job_id] = job
        self._save(job)
        self._launch(job)
        return self.view(job)

    def _launch(self, job: dict) -> None:
        job["gen"] += 1
        job["status"], job["message"], job["updated"] = "running", "", time.time()
        self._save(job)
        threading.Thread(target=self._work, args=(job, job["gen"]), daemon=True).start()

    def _caches(self, job: dict) -> list[dict]:
        caches = []
        for index in range(job["count"]):
            path = self._job_dir(job["id"]) / f"{index}.ocr.json"
            try:
                caches.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                caches.append({})
        return caches

    def _keep_caches(self, job: dict, caches: list[dict]) -> None:
        for index, cache in enumerate(caches):
            if cache:
                try:
                    (self._job_dir(job["id"]) / f"{index}.ocr.json").write_text(json.dumps(cache), encoding="utf-8")
                except OSError:
                    pass

    def _work(self, job: dict, gen: int) -> None:
        folder = self._job_dir(job["id"])
        try:
            files = [(name, (folder / f"{i}.bin").read_bytes()) for i, name in enumerate(job["names"])]
        except OSError:
            job["status"], job["message"] = "error", "Les fichiers de cet import ont disparu : redépose-les."
            self._save(job)
            return
        caches = self._caches(job)
        seen = {"n": 0}

        def on_page(name: str, index: int, count: int, page: int, pages: int, kind: str = "text") -> None:
            if job["gen"] != gen or job.get("_pause"):
                self._keep_caches(job, caches)
                raise Paused()
            job.update(file=name, index=index, count=count, page=page, pages=pages, kind=kind, updated=time.time())
            seen["n"] += 1
            if kind == "ocr" and seen["n"] % 5 == 0:  # les pages reconnues, gardées au fur et à mesure
                self._keep_caches(job, caches)

        try:
            result = self.run(job, files, on_page, caches)
        except Paused:
            if job["gen"] == gen:
                job["status"] = "paused"
                job.pop("_pause", None)
                self._save(job)
            return
        except ImportError_ as exc:
            if job["gen"] == gen:
                job["status"], job["message"] = "error", str(exc)
                self._save(job)
            return
        except Exception as exc:  # erreur inattendue : on garde de quoi reprendre
            if job["gen"] == gen:
                self._keep_caches(job, caches)
                job["status"], job["message"] = "error", f"Erreur inattendue : {exc}"
                self._save(job)
            return
        if job["gen"] != gen:
            return
        job["status"], job["result"], job["updated"] = "done", result, time.time()
        shutil.rmtree(folder, ignore_errors=True)  # les fichiers sont enregistrés dans la matière

    def get(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    def view(self, job: dict) -> dict:
        view = {k: v for k, v in job.items() if not k.startswith("_")}
        view["stalled"] = job["status"] == "running" and time.time() - job["updated"] > STALLED_AFTER
        return view

    def active(self, course_id: str) -> dict | None:
        """La tâche pas encore finie de cette matière (la plus récente), pour la retrouver en revenant sur la page."""
        jobs = [j for j in self.jobs.values() if j["course_id"] == course_id and j["status"] != "done"]
        return self.view(max(jobs, key=lambda j: j["updated"])) if jobs else None

    def pause(self, job_id: str) -> dict:
        job = self.jobs[job_id]
        if job["status"] == "running":
            job["_pause"] = True
            if time.time() - job["updated"] > STALLED_AFTER:  # figée : personne ne lira la demande
                job["gen"] += 1
                job["status"] = "paused"
                job.pop("_pause", None)
                self._save(job)
        return self.view(job)

    def resume(self, job_id: str) -> dict:
        """Reprend (après une pause, une erreur, ou une tâche figée par une mise en veille) : une nouvelle lecture
        part, l'ancienne s'arrête d'elle-même si elle se réveille."""
        job = self.jobs[job_id]
        if job["status"] != "done":
            job.pop("_pause", None)
            self._launch(job)
        return self.view(job)

    def cancel(self, job_id: str) -> None:
        job = self.jobs.pop(job_id, None)
        if job:
            job["gen"] += 1
            shutil.rmtree(self._job_dir(job_id), ignore_errors=True)
