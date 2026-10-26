"""Tes quiz et tes flashcards, aussi sur l'iPhone et l'iPad (voir docs/supabase-sync.sql et web/).

Le Mac reste la référence : il envoie en ligne une copie de ses quiz, de ses paquets de cartes et de la liste des
matières (jamais les cours eux-mêmes). L'iPhone ou l'iPad, jumelé avec un code affiché par le Mac, lit cette copie
et révise hors connexion ; chaque réponse devient un petit « évènement » envoyé en ligne. À la synchro suivante, le Mac
reprend ces évènements chez lui (la carte est replanifiée à l'heure où elle a été revue), les efface, et renvoie
ses paquets à jour. Seuls les documents qui ont changé repartent (une empreinte par document).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, timedelta

import segno

from .online import Online, OnlineError
from .storage import NotFound, _now, file_label, file_of

WEB_URL = "https://boopbipa.github.io/Pirouette/"
BATCH_BYTES = 500_000          # documents envoyés par paquets d'environ 500 Ko
ACTIVITY_DAYS = 60             # le journal des révisions envoyé (série de jours, révisions du jour)
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sans O/0 ni I/1 : faciles à recopier
CARD_SKIP = {"undo", "image"}


def _hash(data) -> str:
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def build_docs(store) -> dict[tuple[str, str], dict]:
    """Tout ce qui part en ligne, document par document : (« meta », « all »), (« deck », cours), (« quiz », quiz)."""
    docs: dict[tuple[str, str], dict] = {}
    courses = []
    quizzes = [json.loads(path.read_text(encoding="utf-8")) for path in store.quizzes_dir.glob("*.json")]
    for course in store.list_courses():
        exam = store.exam_info(course)
        files = course.get("files") or []
        courses.append({"id": course["id"], "name": course["name"], "folder_id": course.get("folder_id"),
                        "color": course.get("color"), "exam": exam["date"] if exam else None,
                        # Ses cours (fichiers), et le cours de chaque quiz : le téléphone range les quiz par cours
                        "files": [{"id": f["id"], "name": file_label(f)} for f in files],
                        "quiz_files": {q["id"]: file_of(course, q) for q in quizzes
                                       if q.get("course_id") == course["id"] and len(files) > 1}})
        cards = (store.get_doc(course["id"], "cards") or {}).get("cards", [])
        if cards:
            docs[("deck", course["id"])] = {"course_id": course["id"],
                                            "cards": [{k: v for k, v in c.items() if k not in CARD_SKIP} for c in cards]}
    since = (date.today() - timedelta(days=ACTIVITY_DAYS)).isoformat()
    docs[("meta", "all")] = {
        "name": store.get_profile().get("name") or "",
        "courses": courses,
        "folders": [{k: f.get(k) for k in ("id", "name", "archived", "exam_week")} for f in store.list_folders()],
        "new_per_day": store.new_per_day(),
        "daily_size": int(store.get_settings().get("daily_size", 30)),  # taille d'une série (téléphone) = révision du jour
        "activity": {day: v for day, v in store.activity().items() if day >= since},
    }
    for quiz in quizzes:
        if quiz.get("questions"):
            docs[("quiz", quiz["id"])] = quiz
    return docs


def data_size(store) -> dict:
    """Le poids de ce qui part en ligne (quiz et cartes), pour Réglages › Données."""
    docs = build_docs(store)
    size = lambda kind: sum(len(json.dumps(d, ensure_ascii=False).encode()) for (k, _), d in docs.items() if k == kind)  # noqa: E731
    return {"quizzes": sum(1 for k, _ in docs if k == "quiz"), "questions": sum(len(d["questions"]) for (k, _), d in docs.items() if k == "quiz"),
            "cards": sum(len(d["cards"]) for (k, _), d in docs.items() if k == "deck"),
            "bytes": size("quiz") + size("deck") + size("meta")}


class Sync:
    def __init__(self, online: Online):
        self.online = online
        self.store = online.store

    def status(self) -> dict:
        return self.store.get_settings().get("sync") or {}

    def _save(self, **changes) -> dict:
        state = self.status() | changes
        self.store.save_settings(sync=state)
        return state

    async def run(self) -> dict:
        """Une synchro : reprendre les révisions faites ailleurs, puis envoyer ce qui a changé."""
        try:
            applied = await self.pull()
            pushed, removed = await self.push()
        except OnlineError as exc:
            self._save(error=str(exc))
            raise
        return self._save(last=_now(), error="", applied=applied) | {"pushed": pushed, "removed": removed}

    async def pull(self) -> int:
        me = self.online._me()
        events = await self.online._rows("sync_events", owner=f"eq.{me}", order="created_at.asc",
                                         select="id,kind,payload,created_at")
        applied = 0
        for event in sorted(events, key=lambda e: (e["payload"] or {}).get("at") or e["created_at"]):
            applied += self._apply(event["kind"], event["payload"] or {})
        for start in range(0, len(events), 100):  # repris (ou impossibles à reprendre) : on les efface
            ids = ",".join(e["id"] for e in events[start:start + 100])
            await self.online._call("DELETE", "/rest/v1/sync_events", params={"id": f"in.({ids})", "owner": f"eq.{me}"})
        return applied

    def _apply(self, kind: str, p: dict) -> int:
        try:
            if kind == "card":
                return int(self.store.apply_remote_review(str(p["course_id"]), str(p["card_id"]), str(p["rating"]), str(p["at"])))
            if kind == "answers":
                answers = [{"index": int(a["index"]), "correct": bool(a["correct"])} for a in p.get("answers") or []]
                self.store.record_answers(str(p["quiz_id"]), answers, at=str(p["at"]))
                return len(answers)
            if kind == "attempt":
                self.store.record_attempt(str(p["quiz_id"]), int(p["score"]), int(p["total"]), at=str(p["at"]))
                return 1
        except (NotFound, KeyError, TypeError, ValueError):
            pass  # quiz ou carte effacés depuis sur le Mac, ou évènement abîmé : on l'oublie
        return 0

    async def push(self) -> tuple[int, int]:
        me = self.online._me()
        local = build_docs(self.store)
        remote = await self.online._rows("sync_docs", owner=f"eq.{me}", select="kind,id,hash")
        known = {(r["kind"], r["id"]): r["hash"] for r in remote}
        changed = []
        for (kind, doc_id), data in local.items():
            digest = _hash(data)
            if known.get((kind, doc_id)) != digest:
                changed.append({"owner": me, "kind": kind, "id": doc_id, "hash": digest, "data": data, "updated_at": _now()})
        batch, size = [], 0
        for row in changed:
            row_size = len(json.dumps(row, ensure_ascii=False).encode())
            if batch and size + row_size > BATCH_BYTES:
                await self._upsert(batch)
                batch, size = [], 0
            batch.append(row)
            size += row_size
        if batch:
            await self._upsert(batch)
        gone = [key for key in known if key not in local]
        for kind in {k for k, _ in gone}:
            ids = [i for k, i in gone if k == kind]
            for start in range(0, len(ids), 100):
                await self.online._call("DELETE", "/rest/v1/sync_docs", params={
                    "owner": f"eq.{me}", "kind": f"eq.{kind}", "id": f"in.({','.join(ids[start:start + 100])})"})
        return len(changed), len(gone)

    async def _upsert(self, rows: list[dict]) -> None:
        await self.online._call("POST", "/rest/v1/sync_docs", params={"on_conflict": "owner,kind,id"}, json=rows,
                                headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    # ---------- Jumeler un iPhone ou un iPad ----------
    async def pair(self) -> dict:
        """Un code à taper (ou à scanner) sur l'iPhone : valable 15 minutes, une seule fois."""
        self.online._me()
        code = "".join(secrets.choice(CODE_LETTERS) for _ in range(8))
        await self.online._call("POST", "/rest/v1/sync_pairs", json={"code": code},
                                headers={"Prefer": "return=minimal"})
        url = f"{WEB_URL}#jumeler={code}"
        # viewBox (sans taille fixe) : le code garde ses proportions quelle que soit sa taille à l'écran ; 4 modules de marge
        qr = segno.make(url, error="m").svg_inline(border=4, omitsize=True, dark="#1D1D1F", light="#FFFFFF")
        return {"code": f"{code[:4]}-{code[4:]}", "url": url, "web": WEB_URL, "qr": qr}

    async def devices(self) -> list[dict]:
        me = self.online._me()
        return await self.online._rows("sync_devices", owner=f"eq.{me}", select="device,name,created_at",
                                       order="created_at.asc")

    async def remove_device(self, device: str) -> None:
        me = self.online._me()
        await self.online._call("DELETE", "/rest/v1/sync_devices", params={"device": f"eq.{device}", "owner": f"eq.{me}"})
