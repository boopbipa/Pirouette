"""Créations en arrière-plan : les quiz se préparent pendant qu'on fait autre chose (un autre quiz, des cartes…).

Les demandes passent une par une (l'IA locale ne traite qu'une génération à la fois) ; la page suit leur avancée
via /api/jobs et affiche un petit suivi, visible partout dans l'app.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from .providers.base import NothingNew, ProviderError, QuotaError

KEEP = 30  # travaux gardés dans la liste (les plus récents)


class Jobs:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.queue: asyncio.Queue | None = None
        self.worker: asyncio.Task | None = None
        self.current: tuple[dict, asyncio.Task] | None = None  # (travail, tâche) en cours
        self.retries: dict[str, object] = {}

    def submit(self, info: dict, factory, retry=None) -> dict:
        """Ajoute un travail : `factory(on_progress)` renvoie le quiz créé (ou les cartes ajoutées). `retry(variant)`
        refabrique le travail, pour le relancer s'il se met en pause (« rien de nouveau »)."""
        job = {"id": uuid.uuid4().hex[:10], "status": "queued", "message": "En attente…",
               "created_at": datetime.now().isoformat(timespec="seconds")} | info
        job.pop("retried", None)
        self.jobs[job["id"]] = job
        if retry:
            self.retries[job["id"]] = retry
        if self.queue is None or self.worker is None or self.worker.done():
            self.queue = asyncio.Queue()
            self.worker = asyncio.get_running_loop().create_task(self._work())
        self.queue.put_nowait((job, factory))
        self._trim()
        return job

    async def _work(self) -> None:
        while True:
            job, factory = await self.queue.get()
            if job["status"] != "queued":  # annulé pendant l'attente
                continue
            job.update(status="running", message="C'est parti…")

            async def on_progress(message: str, job=job) -> None:
                job["message"] = message

            self.current = (job, asyncio.create_task(factory(on_progress)))
            try:
                made = await self.current[1]
                if job.get("kind") == "chapters":
                    job.update(status="done", message=f"{made['count']} chapitres repérés" if made["count"]
                               else "Pas de chapitres trouvés : le document reste en un seul bloc",
                               result={"title": made["title"], "count": made["count"]})
                elif job.get("kind") == "cards":
                    job.update(status="done", message=f"{made['count']} cartes ajoutées",
                               result={"title": made["title"], "count": made["count"]})
                else:
                    added = made.get("added")
                    job.update(status="done", message=f"{added} questions ajoutées au quiz du chapitre" if added
                               else f"{len(made['questions'])} questions",
                               result={"quiz_id": made["id"], "title": made["title"], "count": added or len(made["questions"])})
            except asyncio.CancelledError:
                if job["status"] != "cancelled":
                    raise  # c'est l'app qui s'arrête, pas une annulation
            except QuotaError as exc:
                # Crédit épuisé, limite atteinte : les créations suivantes avec ce moteur échoueraient aussi.
                job.update(status="error", message=str(exc))
                for other in self.jobs.values():
                    if other["status"] == "queued" and other.get("provider") == job.get("provider"):
                        other.update(status="cancelled", message=f"Annulée : {exc}")
            except NothingNew as exc:
                # En pause : l'étudiant choisit (plus facile, réessayer, laisser) ; les créations suivantes continuent.
                job.update(status="paused", message=str(exc))
            except ProviderError as exc:
                job.update(status="error", message=str(exc))
            except Exception as exc:  # une erreur ne doit pas arrêter les travaux suivants
                job.update(status="error", message=f"Erreur inattendue : {exc}")
            finally:
                self.current = None

    def cancel(self, job_id: str) -> None:
        """Annule une création en attente, ou arrête celle en cours (rien n'est enregistré)."""
        job = self.jobs.get(job_id)
        if not job or job["status"] not in {"queued", "running"}:
            return
        job.update(status="cancelled", message="Annulée")
        if self.current and self.current[0] is job:
            self.current[1].cancel()

    def retry(self, job_id: str, variant: str = "") -> dict | None:
        """Relance une création en pause (ou en erreur) : elle repart en fin de file, l'ancienne ligne disparaît."""
        job, make = self.jobs.get(job_id), self.retries.get(job_id)
        if not job or not make or job["status"] not in {"paused", "error"}:
            return None
        info = {k: v for k, v in job.items() if k not in {"id", "status", "message", "created_at", "result"}}
        if variant == "facile" and job.get("kind") == "quiz":
            info["label"] = f"{job['label']} (plus facile)" if "(plus facile)" not in job["label"] else job["label"]
        del self.jobs[job_id]
        self.retries.pop(job_id, None)
        return self.submit(info, make(variant), retry=make)

    def cancel_all(self) -> int:
        active = [j["id"] for j in self.jobs.values() if j["status"] in {"queued", "running"}]
        for job_id in active:
            self.cancel(job_id)
        return len(active)

    def list(self) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda j: j["created_at"])

    def dismiss(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job and job["status"] in {"done", "error", "cancelled", "paused"}:
            del self.jobs[job_id]
            self.retries.pop(job_id, None)

    def _trim(self) -> None:
        finished = [j for j in self.list() if j["status"] in {"done", "error", "cancelled"}]
        for job in finished[: max(0, len(self.jobs) - KEEP)]:
            del self.jobs[job["id"]]
            self.retries.pop(job["id"], None)
