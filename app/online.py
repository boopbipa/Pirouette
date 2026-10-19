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
    # Une clé mal recopiée (« … », espace) casserait chaque requête : on la considère absente
    return bool(URL and KEY and KEY.isascii() and URL.isascii() and " " not in KEY)


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
            _log(self.store.root, f"{method} {path} -> {response.status_code} {response.text[:500]}")
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
        if not data.get("access_token"):
            _log(self.store.root, f"session sans jeton : {str(data)[:300]}")
            raise OnlineError("Le service en ligne n'a pas renvoyé de session : réessaie.")
        self._save_session({"access_token": data["access_token"], "refresh_token": data.get("refresh_token", ""),
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
    async def create_challenge(self, quiz: dict, size: int = CHALLENGE_SIZE, time_limit: int | None = None) -> dict:
        """Un défi : quelques questions d'un quiz (ou de tous les quiz d'un cours), sans le cours lui-même, que mes
        amis pourront faire ; `time_limit` (secondes) : un chrono pour tout le défi."""
        questions = [{k: q.get(k) for k in ("type", "question", "choices", "answer", "explanation")}
                     for q in quiz.get("questions", []) if q.get("question") and q.get("answer") is not None]
        if not questions:
            raise OnlineError("Pas de question à partager : crée d'abord un quiz.")
        picked = random.sample(questions, min(max(3, min(int(size), 20)), len(questions)))
        body = {"owner": self._session().get("user_id"), "title": str(quiz.get("title") or "Défi")[:120],
                "subject": str(quiz.get("course_name") or "")[:120] or None, "questions": picked}
        if time_limit:
            body["time_limit"] = max(30, min(int(time_limit), 3600))
        try:
            rows = (await self._call("POST", "/rest/v1/challenges", json=body,
                                     headers={"Prefer": "return=representation"})).json()
        except OnlineError as exc:
            if "time_limit" not in str(exc):
                raise
            # La base n'a pas encore la colonne du chrono (mise à jour SQL à faire) : défi sans chrono
            body.pop("time_limit", None)
            rows = (await self._call("POST", "/rest/v1/challenges", json=body,
                                     headers={"Prefer": "return=representation"})).json()
        return rows[0] if rows else body

    async def challenges(self, days: int = 7) -> list[dict]:
        """Les défis récents (les miens et ceux de mes amis), avec les scores et mon essai."""
        since = date.fromordinal(date.today().toordinal() - days).isoformat()
        select = ("id,title,subject,day,created_at,owner,questions,time_limit,"
                  "author:profiles!challenges_owner_fkey(pseudo),"
                  "attempts(user_id,score,total,seconds,player:profiles!attempts_user_id_fkey(pseudo))")
        rows = (await self._get_challenges({"select": select, "day": f"gte.{since}", "order": "created_at.desc"})).json()
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
        rows = (await self._get_challenges({
            "id": f"eq.{challenge_id}",
            # Liens explicites : défis et profils se rejoignent aussi par les scores (sinon Supabase refuse, ambigu)
            "select": "id,title,subject,questions,time_limit,author:profiles!challenges_owner_fkey(pseudo)"})).json()
        if not rows:
            raise OnlineError("Ce défi n'existe plus.")
        return rows[0]

    async def _get_challenges(self, params: dict) -> httpx.Response:
        try:
            return await self._call("GET", "/rest/v1/challenges", params=params)
        except OnlineError as exc:
            if "time_limit" not in str(exc):
                raise
            # Base pas encore mise à jour (pas de chrono) : on lit sans
            return await self._call("GET", "/rest/v1/challenges",
                                    params=params | {"select": params["select"].replace("time_limit,", "")})

    async def submit(self, challenge_id: str, score: int, total: int, seconds: int | None) -> None:
        await self._call("POST", "/rest/v1/attempts", json={
            "challenge_id": challenge_id, "user_id": self._session().get("user_id"),
            "score": score, "total": total, "seconds": seconds},
            headers={"Prefer": "resolution=ignore-duplicates"})

    # ---------- Messages : conversations à deux et groupes de révision ----------
    # Une conversation (« room ») a des membres ; une conversation à deux est une room « direct ». Les messages
    # peuvent porter un quiz, des flashcards, un cours entier, un défi ou un résultat (`kind`) : `meta` (petit) sert
    # à l'aperçu, `payload` (le contenu) n'est lu qu'au moment de faire le quiz ou de l'ajouter à ses cours.
    def _me(self) -> str:
        me = self._session().get("user_id")
        if not me:
            raise OnlineError("Connecte-toi d'abord (onglet Défis).")
        return me

    async def _rows(self, table: str, **params) -> list[dict]:
        return (await self._call("GET", f"/rest/v1/{table}", params=params)).json()

    async def rooms(self) -> list[dict]:
        """Mes conversations, la plus récente d'abord : membres, dernier message, messages non lus."""
        me = self._me()
        mine = await self._rows("room_members", user_id=f"eq.{me}", select="room_id,last_read")
        if not mine:
            return []
        ids = ",".join(m["room_id"] for m in mine)
        rooms = await self._rows("rooms", id=f"in.({ids})", select="id,kind,name,pinned,created_by,created_at")
        members = await self._rows("room_members", room_id=f"in.({ids})", select="room_id,user_id")
        pseudos = await self._pseudos({m["user_id"] for m in members})
        recent = await self._rows("messages", room_id=f"in.({ids})", select="id,room_id,sender,body,kind,title,created_at",
                                  order="created_at.desc", limit="400")
        read = {m["room_id"]: m["last_read"] for m in mine}
        out = []
        for room in rooms:
            rid = room["id"]
            people = [{"id": m["user_id"], "pseudo": pseudos.get(m["user_id"], "?")}
                      for m in members if m["room_id"] == rid]
            msgs = [m for m in recent if m["room_id"] == rid]
            last = msgs[0] if msgs else None
            others = [p for p in people if p["id"] != me]
            out.append({
                "id": rid, "kind": room["kind"], "pinned": room.get("pinned"), "created_by": room.get("created_by"),
                "name": room.get("name") or ", ".join(p["pseudo"] for p in others) or "Moi",
                "members": people, "others": others,
                "unread": sum(1 for m in msgs if m["sender"] != me and _after(m["created_at"], read.get(rid))),
                "last": last and {"text": _preview(last), "mine": last["sender"] == me,
                                  "pseudo": pseudos.get(last["sender"], "?"), "at": last["created_at"]},
                "at": (last or room)["created_at"]})
        return sorted(out, key=lambda r: r["at"] or "", reverse=True)

    async def _pseudos(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        rows = await self._rows("profiles", id=f"in.({','.join(sorted(ids))})", select="id,pseudo")
        return {r["id"]: r["pseudo"] for r in rows}

    async def direct(self, friend_id: str) -> dict:
        """La conversation à deux avec un ami (créée la première fois)."""
        me = self._me()
        for room in await self.rooms():
            if room["kind"] == "direct" and [p["id"] for p in room["others"]] == [friend_id]:
                return room
        friends = {f["id"] for f in (await self.friends())["friends"]}
        if friend_id not in friends:
            raise OnlineError("Tu ne peux écrire qu'à tes amis : ajoute-le d'abord avec son code ami.")
        return await self._new_room("direct", None, [friend_id])

    async def group(self, name: str, member_ids: list[str]) -> dict:
        """Un groupe de révision, avec certains de mes amis."""
        name = " ".join(str(name).split())[:60]
        if len(name) < 2:
            raise OnlineError("Donne un nom au groupe.")
        return await self._new_room("group", name, member_ids)

    async def _new_room(self, kind: str, name: str | None, member_ids: list[str]) -> dict:
        me = self._me()
        room = (await self._call("POST", "/rest/v1/rooms", json={"kind": kind, "name": name, "created_by": me},
                                 headers={"Prefer": "return=representation"})).json()[0]
        # D'abord moi (je crée la conversation), puis mes amis (seul un membre peut ajouter ses amis)
        await self._call("POST", "/rest/v1/room_members", json={"room_id": room["id"], "user_id": me})
        await self.add_members(room["id"], member_ids)
        return {"id": room["id"], "kind": kind, "name": name}

    async def add_members(self, room_id: str, member_ids: list[str]) -> None:
        me = self._me()
        rows = [{"room_id": room_id, "user_id": uid} for uid in dict.fromkeys(member_ids) if uid and uid != me]
        if rows:
            await self._call("POST", "/rest/v1/room_members", json=rows,
                             headers={"Prefer": "resolution=ignore-duplicates"})

    async def leave(self, room_id: str) -> None:
        await self._call("DELETE", "/rest/v1/room_members", params={"room_id": f"eq.{room_id}", "user_id": f"eq.{self._me()}"})

    async def update_room(self, room_id: str, name: str | None = None, pinned: str | None = None,
                          unpin: bool = False) -> None:
        body = {}
        if name is not None:
            body["name"] = " ".join(name.split())[:60] or None
        if pinned or unpin:
            body["pinned"] = pinned or None
        if body:
            await self._call("PATCH", "/rest/v1/rooms", json=body, params={"id": f"eq.{room_id}"})

    async def messages(self, room_id: str, after: str = "") -> dict:
        """Les messages d'une conversation (les nouveaux seulement avec `after`), et on la marque comme lue."""
        me = self._me()
        params = {"room_id": f"eq.{room_id}", "select": "id,sender,body,kind,title,meta,created_at,edited_at",
                  "order": "created_at.asc", "limit": "500"}
        if after:
            params["created_at"] = f"gt.{after}"
        try:
            rows = await self._rows("messages", **params)
        except OnlineError as exc:
            if "messages-edit" not in str(exc):
                raise
            # Base sans la mise à jour « modifier un message » : on lit sans la date de modification
            rows = await self._rows("messages", **params | {"select": params["select"].replace(",edited_at", "")})
        pseudos = await self._pseudos({r["sender"] for r in rows})
        for row in rows:
            row["mine"] = row["sender"] == me
            row["pseudo"] = pseudos.get(row["sender"], "?")
        if rows:
            await self._call("PATCH", "/rest/v1/room_members", json={"last_read": rows[-1]["created_at"]},
                             params={"room_id": f"eq.{room_id}", "user_id": f"eq.{me}"})
        return {"messages": rows}

    async def send(self, room_id: str, body: str = "", kind: str = "text", title: str = "",
                   meta: dict | None = None, payload: dict | None = None) -> dict:
        body = str(body or "").strip()[:2000]
        if kind == "text" and not body:
            raise OnlineError("Message vide.")
        row = {"room_id": room_id, "sender": self._me(), "body": body or None, "kind": kind,
               "title": (title or "")[:160] or None, "meta": meta, "payload": payload}
        if payload is not None and len(json.dumps(payload)) > 1_500_000:
            raise OnlineError("C'est trop gros à envoyer d'un coup : envoie plutôt un chapitre ou un quiz.")
        rows = (await self._call("POST", "/rest/v1/messages", json=row, headers={"Prefer": "return=representation"})).json()
        return rows[0] if rows else row

    async def edit_message(self, message_id: str, body: str) -> None:
        """Modifier le texte d'un de mes messages (Supabase n'accepte que les miens)."""
        body = str(body or "").strip()[:2000]
        if not body:
            raise OnlineError("Message vide : supprime-le plutôt.")
        from datetime import datetime, timezone
        rows = (await self._call("PATCH", "/rest/v1/messages", json={"body": body, "edited_at": datetime.now(timezone.utc).isoformat()},
                                 params={"id": f"eq.{message_id}", "sender": f"eq.{self._me()}"},
                                 headers={"Prefer": "return=representation"})).json()
        if not rows:  # refusé sans bruit par les règles d'accès : la mise à jour SQL manque
            raise OnlineError(_french("row-level security messages"))

    async def delete_message(self, message_id: str) -> None:
        rows = (await self._call("DELETE", "/rest/v1/messages", params={"id": f"eq.{message_id}", "sender": f"eq.{self._me()}"},
                                 headers={"Prefer": "return=representation"})).json()
        if not rows:
            raise OnlineError(_french("row-level security messages"))

    async def payload(self, message_id: str) -> dict:
        rows = await self._rows("messages", id=f"eq.{message_id}", select="id,kind,title,meta,payload,room_id")
        if not rows:
            raise OnlineError("Ce message n'existe plus.")
        return rows[0]

    async def unread(self) -> int:
        try:
            return sum(r["unread"] for r in await self.rooms())
        except OnlineError:
            return 0


def _after(stamp: str | None, reference: str | None) -> bool:
    """`stamp` est-il plus récent que `reference` (dates ISO de Supabase) ?"""
    if not stamp:
        return False
    if not reference:
        return True
    try:
        from datetime import datetime
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) > datetime.fromisoformat(reference.replace("Z", "+00:00"))
    except ValueError:
        return stamp > reference


def _preview(message: dict) -> str:
    labels = {"quiz": "📝 Quiz", "cards": "🃏 Flashcards", "course": "📚 Cours", "challenge": "⚔️ Défi", "result": "🏁 Résultat"}
    if message.get("kind") in labels:
        return f"{labels[message['kind']]} : {message.get('title') or ''}".strip(" :")
    return (message.get("body") or "")[:120]


def _log(folder: Path, line: str) -> None:
    """Journal des refus du service en ligne (pour comprendre un problème), dans le dossier des données."""
    try:
        with (folder / "online.log").open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


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
    if "edited_at" in lowered or ("row-level security" in lowered and "messages" in lowered):
        return ("Modifier ou supprimer un message n'est pas encore activé sur le service en ligne : la mise à jour "
                "docs/supabase-messages-edit.sql doit être collée une fois dans Supabase → SQL Editor.")
    if "could not find the table" in lowered and any(t in lowered for t in ("room", "messages")):
        return ("La messagerie n'est pas encore activée sur le service en ligne : la mise à jour « Messages » "
                "(docs/supabase-messages.sql) doit être collée une fois dans Supabase → SQL Editor.")
    if "duplicate key" in lowered and "pseudo" in lowered:
        return "Ce pseudo est déjà pris."
    return message
