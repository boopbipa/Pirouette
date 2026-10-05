"""Pirouette en ligne : un compte, des amis et des défis (10 questions de quiz à faire entre amis).

Tout passe par Supabase (voir docs/supabase.sql). Pas d'e-mail ni de mot de passe : un compte « anonyme » est créé
avec un simple pseudo, et sa session (jetons de connexion) reste dans les réglages de Pirouette, sur cet ordinateur.
Aucune donnée personnelle n'est en ligne : seuls un pseudo, un code ami, les questions des défis et les scores.
Les cours eux-mêmes ne quittent jamais l'ordinateur.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import secrets
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import httpx

# Projet Supabase de Pirouette : l'adresse et la clé « publique » (anon) peuvent être dans l'app, ce sont les règles
# d'accès de la base qui protègent les données.
# Elles ne sont pas dans le code : GitHub les ajoute à l'app au moment de la fabrication (secrets du dépôt,
# voir .github/workflows), dans app/online_config.json. Pour développer : PIROUETTE_SUPABASE_URL / _KEY.
def _config() -> dict:
    try:
        return json.loads((Path(__file__).with_name("online_config.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


URL = (os.getenv("PIROUETTE_SUPABASE_URL") or _config().get("url", "")).rstrip("/")
KEY = os.getenv("PIROUETTE_SUPABASE_KEY") or _config().get("key", "")
CHALLENGE_SIZE = 10
_PENDING: dict = {}  # connexion Google en cours : le vérificateur PKCE (un seul Pirouette par ordinateur)


class OnlineError(Exception):
    """Erreur à montrer telle quelle (en français) à l'étudiant."""


def configured() -> bool:
    return bool(URL and KEY)


class Online:
    def __init__(self, store, transport: httpx.AsyncBaseTransport | None = None):
        self.store = store
        self.transport = transport  # pour les tests : un faux Supabase

    # ---------- Session ----------
    def _session(self) -> dict:
        return self.store.get_settings().get("online") or {}

    def _save_session(self, session: dict | None) -> None:
        self.store.save_settings(online=session or None)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=URL, timeout=15, transport=self.transport,
                                 headers={"apikey": KEY, "Content-Type": "application/json"})

    async def _call(self, method: str, path: str, *, auth: bool = True, **kwargs) -> httpx.Response:
        if not configured():
            raise OnlineError("Les défis en ligne ne sont pas encore ouverts dans cette version.")
        headers = kwargs.pop("headers", {})
        if auth:
            headers["Authorization"] = f"Bearer {await self._token()}"
        try:
            async with self._client() as client:
                response = await client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise OnlineError("Pas de connexion à internet (ou le service ne répond pas). Réessaie dans un moment.") from exc
        if response.status_code == 401 and auth:
            self._save_session(None)
            raise OnlineError("Ta connexion a expiré : reconnecte-toi.")
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = body.get("msg") or body.get("message") or body.get("error_description") or ""
            except ValueError:
                pass
            raise OnlineError(_french(detail) or f"Le service en ligne a refusé la demande ({response.status_code}).")
        return response

    async def _token(self) -> str:
        session = self._session()
        if not session.get("access_token"):
            raise OnlineError("Connecte-toi d'abord.")
        if session.get("expires_at", 0) - 60 > time.time():
            return session["access_token"]
        response = await self._call("POST", "/auth/v1/token?grant_type=refresh_token", auth=False,
                                    json={"refresh_token": session.get("refresh_token", "")})
        self._keep(response.json())
        return self._session()["access_token"]

    def _keep(self, data: dict) -> None:
        user = data.get("user") or {}
        before = self._session()
        meta = user.get("user_metadata") or {}
        self._save_session({"access_token": data["access_token"], "refresh_token": data["refresh_token"],
                            "expires_at": int(time.time()) + int(data.get("expires_in", 3600)),
                            "user_id": user.get("id") or before.get("user_id"),
                            "email": user.get("email") or before.get("email"),
                            "anonymous": user.get("is_anonymous", before.get("anonymous", True)),
                            "name": meta.get("full_name") or meta.get("name") or before.get("name")})

    # ---------- Compte : un pseudo suffit (compte anonyme, gardé sur cet ordinateur) ----------
    async def start(self, pseudo: str) -> dict:
        if not self._session().get("access_token"):
            _check_pseudo(pseudo)
            response = await self._call("POST", "/auth/v1/signup", auth=False, json={"data": {}})
            self._keep(response.json())
        return await self.set_pseudo(pseudo)

    def logout(self) -> None:
        self._save_session(None)

    # ---------- Connexion avec Google ----------
    # Le navigateur s'ouvre sur la page de Google ; après le choix du compte, Supabase renvoie vers Pirouette
    # (http://127.0.0.1:<port>/api/online/google/callback?code=…), qui échange ce code contre une session (PKCE :
    # seul ce Pirouette, qui a gardé le « vérificateur », peut faire l'échange). Un compte « pseudo » déjà créé
    # est relié à Google (mêmes amis, mêmes scores) au lieu d'en créer un nouveau.
    async def google_url(self, redirect_to: str) -> str:
        if not configured():
            raise OnlineError("Les défis en ligne ne sont pas encore ouverts dans cette version.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        _PENDING.clear()
        _PENDING["verifier"] = verifier
        params = {"provider": "google", "redirect_to": redirect_to, "code_challenge": challenge,
                  "code_challenge_method": "s256"}
        session = self._session()
        if session.get("access_token") and session.get("anonymous", True):
            response = await self._call("GET", "/auth/v1/user/identities/authorize",
                                        params=params | {"skip_http_redirect": "true"})
            return response.json()["url"]
        return f"{URL}/auth/v1/authorize?{urlencode(params)}"

    async def google_finish(self, code: str) -> dict:
        verifier = _PENDING.pop("verifier", None)
        if not verifier:
            raise OnlineError("Cette connexion a expiré : recommence depuis Pirouette.")
        response = await self._call("POST", "/auth/v1/token?grant_type=pkce", auth=False,
                                    json={"auth_code": code, "code_verifier": verifier})
        self._keep(response.json())
        return await self.me()

    # ---------- Profil ----------
    async def me(self) -> dict:
        session = self._session()
        if not session.get("user_id"):
            return {"connected": False, "configured": configured()}
        rows = (await self._call("GET", "/rest/v1/profiles", params={"id": f"eq.{session['user_id']}",
                                                                      "select": "id,pseudo,friend_code"})).json()
        return {"connected": True, "configured": True, "email": session.get("email"),
                "google": not session.get("anonymous", True), "name": session.get("name"),
                "profile": rows[0] if rows else None}

    async def set_pseudo(self, pseudo: str) -> dict:
        pseudo = _check_pseudo(pseudo)
        user_id = self._session().get("user_id")
        existing = (await self.me()).get("profile")
        if existing:
            await self._call("PATCH", "/rest/v1/profiles", params={"id": f"eq.{user_id}"}, json={"pseudo": pseudo})
        else:
            await self._call("POST", "/rest/v1/profiles", json={"id": user_id, "pseudo": pseudo})
        return await self.me()

    # ---------- Amis ----------
    async def friends(self) -> dict:
        me = self._session().get("user_id")
        rows = (await self._call("GET", "/rest/v1/friendships", params={
            "select": "user_id,friend_id,status,a:profiles!friendships_user_id_fkey(pseudo),"
                      "b:profiles!friendships_friend_id_fkey(pseudo)"})).json()
        friends, received, sent = [], [], []
        for row in rows:
            mine = row["user_id"] == me
            profile = (row.get("b") if mine else row.get("a")) or {}
            other = {"id": row["friend_id"] if mine else row["user_id"], "pseudo": profile.get("pseudo", "?")}
            if row["status"] == "accepted":
                friends.append(other)
            elif mine:
                sent.append(other)
            else:
                received.append(other)
        return {"friends": friends, "received": received, "sent": sent}

    async def add_friend(self, code: str) -> dict:
        code = code.strip().upper()
        rows = (await self._call("GET", "/rest/v1/profiles", params={"friend_code": f"eq.{code}",
                                                                      "select": "id,pseudo"})).json()
        if not rows:
            raise OnlineError("Aucun compte avec ce code ami : vérifie-le avec ton ami.")
        me = self._session().get("user_id")
        other = rows[0]
        if other["id"] == me:
            raise OnlineError("C'est ton propre code : envoie-le à tes amis.")
        # Il m'a déjà invité : on accepte directement son invitation
        theirs = (await self._call("GET", "/rest/v1/friendships", params={
            "user_id": f"eq.{other['id']}", "friend_id": f"eq.{me}", "select": "status"})).json()
        if theirs:
            await self.accept(other["id"])
            return {"pseudo": other["pseudo"], "status": "accepted"}
        await self._call("POST", "/rest/v1/friendships", json={"user_id": me, "friend_id": other["id"]},
                         headers={"Prefer": "resolution=ignore-duplicates"})
        return {"pseudo": other["pseudo"], "status": "pending"}

    async def accept(self, friend_id: str) -> None:
        await self._call("PATCH", "/rest/v1/friendships", json={"status": "accepted"},
                         params={"user_id": f"eq.{friend_id}", "friend_id": f"eq.{self._session().get('user_id')}"})

    async def remove_friend(self, friend_id: str) -> None:
        me = self._session().get("user_id")
        for a, b in ((me, friend_id), (friend_id, me)):
            await self._call("DELETE", "/rest/v1/friendships", params={"user_id": f"eq.{a}", "friend_id": f"eq.{b}"})

    # ---------- Défis ----------
    async def create_challenge(self, quiz: dict, size: int = CHALLENGE_SIZE) -> dict:
        """Un défi : quelques questions d'un de mes quiz (sans le cours), que mes amis pourront faire."""
        questions = [{k: q.get(k) for k in ("type", "question", "choices", "answer", "explanation")}
                     for q in quiz.get("questions", []) if q.get("question") and q.get("answer") is not None]
        if not questions:
            raise OnlineError("Ce quiz n'a pas de question à partager.")
        picked = random.sample(questions, min(size, len(questions)))
        body = {"owner": self._session().get("user_id"), "title": str(quiz.get("title") or "Défi")[:120],
                "subject": str(quiz.get("course_name") or "")[:120] or None, "questions": picked}
        rows = (await self._call("POST", "/rest/v1/challenges", json=body,
                                 headers={"Prefer": "return=representation"})).json()
        return rows[0] if rows else body

    async def challenges(self, days: int = 7) -> list[dict]:
        """Les défis récents (les miens et ceux de mes amis), avec les scores et mon essai."""
        since = date.fromordinal(date.today().toordinal() - days).isoformat()
        rows = (await self._call("GET", "/rest/v1/challenges", params={
            "select": "id,title,subject,day,created_at,owner,questions,author:profiles!challenges_owner_fkey(pseudo),"
                      "attempts(user_id,score,total,seconds,player:profiles!attempts_user_id_fkey(pseudo))",
            "day": f"gte.{since}", "order": "created_at.desc"})).json()
        me = self._session().get("user_id")
        for row in rows:
            row["count"] = len(row.get("questions") or [])
            row["mine"] = row.get("owner") == me
            attempts = sorted(row.get("attempts") or [], key=lambda a: (-a["score"] / a["total"], a.get("seconds") or 0))
            row["scores"] = [{"pseudo": (a.get("player") or {}).get("pseudo", "?"), "score": a["score"],
                              "total": a["total"], "seconds": a.get("seconds"), "me": a["user_id"] == me}
                             for a in attempts]
            row["done"] = any(a["user_id"] == me for a in attempts)
            row["author"] = (row.get("author") or {}).get("pseudo", "?")
            if not row["done"]:
                row.pop("scores")  # pas de scores avant d'avoir joué (on ne se gâche pas la surprise)
            row.pop("attempts", None)
        return rows

    async def challenge(self, challenge_id: str) -> dict:
        rows = (await self._call("GET", "/rest/v1/challenges", params={
            "id": f"eq.{challenge_id}",
            # Liens explicites : défis et profils se rejoignent aussi par les scores (sinon Supabase refuse, ambigu)
            "select": "id,title,subject,questions,author:profiles!challenges_owner_fkey(pseudo)"})).json()
        if not rows:
            raise OnlineError("Ce défi n'existe plus.")
        return rows[0]

    async def submit(self, challenge_id: str, score: int, total: int, seconds: int | None) -> None:
        await self._call("POST", "/rest/v1/attempts", json={
            "challenge_id": challenge_id, "user_id": self._session().get("user_id"),
            "score": score, "total": total, "seconds": seconds},
            headers={"Prefer": "resolution=ignore-duplicates"})


def _check_pseudo(pseudo: str) -> str:
    pseudo = " ".join(str(pseudo).split())[:24]
    if len(pseudo) < 2:
        raise OnlineError("Choisis un pseudo d'au moins 2 lettres.")
    return pseudo


def _french(message: str) -> str:
    """Les messages de Supabase les plus courants, en français."""
    lowered = message.lower()
    if "anonymous sign-ins are disabled" in lowered:
        return "Les comptes ne sont pas encore ouverts sur le service en ligne (réglage Supabase à activer)."
    if "rate limit" in lowered or "security purposes" in lowered:
        return "Trop de demandes d'un coup : patiente quelques minutes avant de réessayer."
    if "duplicate key" in lowered and "pseudo" in lowered:
        return "Ce pseudo est déjà pris."
    return message
