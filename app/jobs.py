"""Créations en arrière-plan : les quiz se préparent pendant qu'on fait autre chose (un autre quiz, des cartes…).

Les demandes passent une par une (l'IA locale ne traite qu'une génération à la fois) ; la page suit leur avancée
via /api/jobs et affiche un petit suivi, visible partout dans l'app.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from .providers.base import ProviderError

KEEP = 30  # travaux gardés dans la liste (les plus récents)


class Jobs:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.queue: asyncio.Queue | None = None
        self.worker: asyncio.Task | None = None

    def submit(self, info: dict, factory) -> dict:
        """Ajoute un travail : `factory(on_progress)` renvoie le quiz créé."""
        job = {"id": uuid.uuid4().hex[:10], "status": "queued", "message": "En attente…",
               "created_at": datetime.now().isoformat(timespec="seconds")} | info
        self.jobs[job["id"]] = job
        if self.queue is None or self.worker is None or self.worker.done():
            self.queue = asyncio.Queue()
            self.worker = asyncio.get_running_loop().create_task(self._work())
        self.queue.put_nowait((job, factory))
        self._trim()
        return job

    async def _work(self) -> None:
        while True:
            job, factory = await self.queue.get()
            job.update(status="running", message="C'est parti…")

            async def on_progress(message: str, job=job) -> None:
                job["message"] = message

            try:
                quiz = await factory(on_progress)
                job.update(status="done", message=f"{len(quiz['questions'])} questions",
                           result={"quiz_id": quiz["id"], "title": quiz["title"], "count": len(quiz["questions"])})
            except ProviderError as exc:
                job.update(status="error", message=str(exc))
            except Exception as exc:  # une erreur ne doit pas arrêter les travaux suivants
                job.update(status="error", message=f"Erreur inattendue : {exc}")

    def list(self) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda j: j["created_at"])

    def dismiss(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job and job["status"] in {"done", "error"}:
            del self.jobs[job_id]

    def _trim(self) -> None:
        finished = [j for j in self.list() if j["status"] in {"done", "error"}]
        for job in finished[: max(0, len(self.jobs) - KEEP)]:
            del self.jobs[job["id"]]
