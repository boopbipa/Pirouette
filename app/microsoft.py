"""« Se connecter avec Microsoft » : les cours sur OneDrive (Word en ligne, PowerPoint, PDF…) et dans OneNote, suivis
comme un dossier de l'ordinateur, sans installer l'app OneDrive (pour ceux qui travaillent dans le navigateur).

Connexion OAuth 2.0 (code + PKCE, application « publique » : aucun secret dans l'app). Microsoft renvoie vers
http://localhost:<port de Pirouette>/ ; Pirouette garde le jeton de renouvellement dans settings.json (lisible par
l'utilisateur seul) et ne demande que la lecture (Files.Read, Notes.Read).

Une matière peut suivre un dossier OneDrive ou une section OneNote : `course["cloud"]` = {kind: "drive" | "onenote",
id, name, known: {nom en minuscules: version}, ignored: [noms]}. Les mêmes règles que le dossier suivi (watch.py) : un
cours modifié est relu tout seul, un nouveau est proposé (« Ajouter » ou « Ignorer »)."""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import secrets
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

from .extract import SUPPORTED_EXTENSIONS

# L'identifiant de l'application Pirouette chez Microsoft (inscription gratuite, « Applications mobiles et de bureau »,
# adresse de retour http://localhost). Pas un secret : il est dans toutes les applications de bureau.
CLIENT_ID = "35441499-f3ec-42fd-b6dd-6376f027384a"
AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Files.Read Notes.Read"
TIMEOUT = 30

_pending: dict[str, dict] = {}  # connexions en cours : state → {verifier, redirect}


class MicrosoftError(Exception):
    """Message montré tel quel."""


def client_id(store) -> str:
    return (store.get_settings().get("ms_client_id") or os.getenv("PIROUETTE_MS_CLIENT_ID") or CLIENT_ID).strip()


def status(store) -> dict:
    saved = store.get_settings().get("microsoft") or {}
    return {"available": bool(client_id(store)), "connected": bool(saved.get("refresh_token")),
            "account": saved.get("account") or ""}


# ---------- Connexion ----------
def begin(store, redirect: str) -> str:
    """L'adresse de la page de connexion Microsoft (à ouvrir dans le navigateur)."""
    cid = client_id(store)
    if not cid:
        raise MicrosoftError("La connexion Microsoft n'est pas encore disponible dans cette version.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _pending.clear()
    _pending[state] = {"verifier": verifier, "redirect": redirect, "at": time.time()}
    return f"{AUTH}/authorize?" + urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": redirect, "response_mode": "query",
        "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "prompt": "select_account"})


def is_callback(state: str | None) -> bool:
    return bool(state) and state in _pending


def finish(store, code: str, state: str) -> str:
    """Retour de Microsoft : le code devient un jeton ; renvoie le compte connecté."""
    import httpx

    pending = _pending.pop(state, None)
    if pending is None:
        raise MicrosoftError("Cette connexion a expiré : recommence depuis Pirouette.")
    response = httpx.post(f"{AUTH}/token", timeout=TIMEOUT, data={
        "client_id": client_id(store), "grant_type": "authorization_code", "code": code,
        "redirect_uri": pending["redirect"], "code_verifier": pending["verifier"], "scope": SCOPES})
    tokens = _tokens(response)
    saved = {"refresh_token": tokens["refresh_token"], "access_token": tokens["access_token"],
             "expires_at": time.time() + int(tokens.get("expires_in", 3600)) - 120, "account": ""}
    store.save_settings(microsoft=saved)
    try:
        me = get(store, "/me", params={"$select": "displayName,mail,userPrincipalName"})
        saved["account"] = me.get("mail") or me.get("userPrincipalName") or me.get("displayName") or ""
        store.save_settings(microsoft=saved)
    except MicrosoftError:
        pass
    return saved["account"]


def disconnect(store) -> None:
    store.save_settings(microsoft={})


def _tokens(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or "access_token" not in data:
        code = data.get("error") or response.status_code
        detail = data.get("error_description", "").split("\r\n")[0]
        if code in ("consent_required", "interaction_required") or "AADSTS65001" in detail or "AADSTS90094" in detail:
            raise MicrosoftError("Ton école doit d'abord autoriser Pirouette (un administrateur de ton compte Microsoft "
                                 "de l'école). En attendant : l'app OneDrive sur ton ordinateur, et le dossier suivi.")
        raise MicrosoftError(f"Microsoft a refusé la connexion ({code}). {detail}".strip())
    return data


def _access_token(store) -> str:
    import httpx

    saved = store.get_settings().get("microsoft") or {}
    if not saved.get("refresh_token"):
        raise MicrosoftError("Connecte-toi d'abord avec ton compte Microsoft.")
    if saved.get("access_token") and saved.get("expires_at", 0) > time.time():
        return saved["access_token"]
    response = httpx.post(f"{AUTH}/token", timeout=TIMEOUT, data={
        "client_id": client_id(store), "grant_type": "refresh_token", "refresh_token": saved["refresh_token"],
        "scope": SCOPES})
    try:
        tokens = _tokens(response)
    except MicrosoftError as exc:
        if response.status_code in (400, 401):  # jeton révoqué ou expiré (90 jours sans usage) : se reconnecter
            store.save_settings(microsoft={})
            raise MicrosoftError("Ta connexion Microsoft a expiré : reconnecte-toi.") from exc
        raise
    saved.update(access_token=tokens["access_token"], refresh_token=tokens.get("refresh_token", saved["refresh_token"]),
                 expires_at=time.time() + int(tokens.get("expires_in", 3600)) - 120)
    store.save_settings(microsoft=saved)
    return saved["access_token"]


def get(store, path: str, params: dict | None = None, raw: bool = False):
    import httpx

    url = path if path.startswith("https://") else f"{GRAPH}{path}"
    try:
        response = httpx.get(url, params=params, timeout=TIMEOUT, follow_redirects=True,
                             headers={"Authorization": f"Bearer {_access_token(store)}"})
    except httpx.HTTPError as exc:
        raise MicrosoftError(f"Impossible de joindre Microsoft (connexion Internet ?) : {exc}") from exc
    if response.status_code == 403:
        raise MicrosoftError("Microsoft refuse l'accès à ce contenu (ton école l'a peut-être bloqué pour les applications).")
    if response.status_code == 404:
        raise MicrosoftError("Introuvable sur ton compte Microsoft (supprimé ou déplacé ?).")
    if response.status_code >= 400:
        raise MicrosoftError(f"Microsoft a répondu par une erreur ({response.status_code}).")
    return response.content if raw else response.json()


def _all(store, path: str, params: dict | None = None) -> list[dict]:
    items, url, query = [], path, params
    while url:
        data = get(store, url, params=query)
        items += data.get("value", [])
        url, query = data.get("@odata.nextLink"), None
    return items


# ---------- Parcourir ----------
def browse_drive(store, item_id: str = "") -> dict:
    """Un dossier de OneDrive : ses sous-dossiers et ses cours (les formats que Pirouette sait lire)."""
    base = f"/me/drive/items/{item_id}" if item_id else "/me/drive/root"
    folder = get(store, base, params={"$select": "id,name,parentReference"})
    children = _all(store, f"{base}/children", params={"$select": "id,name,file,folder,size,lastModifiedDateTime", "$top": "200"})
    items = []
    for c in children:
        if "folder" in c:
            items.append({"id": c["id"], "name": c["name"], "folder": True})
        elif Path(c["name"]).suffix.lower() in SUPPORTED_EXTENSIONS:
            items.append({"id": c["id"], "name": c["name"], "folder": False})
    items.sort(key=lambda i: (not i["folder"], i["name"].lower()))
    parent = (folder.get("parentReference") or {}).get("id") if item_id else None
    return {"id": folder.get("id"), "name": "OneDrive" if not item_id else folder.get("name"), "parent": parent, "items": items}


def notebooks(store) -> list[dict]:
    """Les carnets OneNote et leurs sections."""
    books = _all(store, "/me/onenote/notebooks", params={"$expand": "sections($select=id,displayName)", "$select": "id,displayName"})
    return [{"id": b["id"], "name": b.get("displayName", ""),
             "sections": [{"id": s["id"], "name": s.get("displayName", "")} for s in b.get("sections") or []]} for b in books]


# ---------- Les cours d'une source (dossier OneDrive ou section OneNote) ----------
def _sources(store, cloud: dict) -> dict[str, dict]:
    """nom du cours (en minuscules) → {name, id, version}."""
    out = {}
    if cloud["kind"] == "drive":
        for c in _all(store, f"/me/drive/items/{cloud['id']}/children",
                      params={"$select": "id,name,file,eTag,lastModifiedDateTime", "$top": "200"}):
            if "file" in c and Path(c["name"]).suffix.lower() in SUPPORTED_EXTENSIONS:
                out[c["name"].lower()] = {"name": c["name"], "id": c["id"], "version": c.get("lastModifiedDateTime") or c.get("eTag")}
    else:
        for p in _all(store, f"/me/onenote/sections/{cloud['id']}/pages",
                      params={"$select": "id,title,lastModifiedDateTime", "$top": "100"}):
            name = _safe_name(p.get("title") or "Page sans titre") + ".md"
            out[name.lower()] = {"name": name, "id": p["id"], "version": p.get("lastModifiedDateTime")}
    return out


def _safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", title).strip()[:120] or "Page"


def link(store, course: dict, kind: str, item_id: str, name: str) -> dict:
    """Suivre ce dossier OneDrive ou cette section OneNote : les cours déjà dans la matière qui portent le même nom y
    sont reliés (sans les relire)."""
    if kind not in ("drive", "onenote"):
        raise MicrosoftError("Source inconnue.")
    cloud = {"kind": kind, "id": item_id, "name": name, "known": {}, "ignored": []}
    found = _sources(store, cloud)
    for f in course["files"]:
        src = found.get(f["name"].lower())
        if src:
            cloud["known"][f["name"].lower()] = src["version"]
    course["cloud"] = cloud
    return cloud


def scan(store, course: dict) -> dict:
    """Comme watch.scan : `updated` (cours de la matière modifiés), `new` (à proposer)."""
    cloud = course["cloud"]
    try:
        found = _sources(store, cloud)
    except MicrosoftError as exc:
        return {"updated": [], "new": [], "cloud": [], "missing": False, "google": [], "error": str(exc)}
    in_course = {f["name"].lower() for f in course["files"]}
    ignored = {n.lower() for n in cloud.get("ignored", [])}
    known = cloud.setdefault("known", {})
    updated, new = [], []
    for key, src in sorted(found.items()):
        if key in ignored:
            continue
        if key in in_course:
            if key not in known:
                known[key] = src["version"]
            elif src["version"] != known[key]:
                updated.append(src["name"])
        else:
            new.append(src["name"])
    return {"updated": updated, "new": new, "cloud": [], "missing": False, "google": []}


def take(store, course: dict, names: list[str]) -> list[tuple[str, bytes]]:
    """Les cours à importer, téléchargés ; leur version est retenue."""
    cloud = course["cloud"]
    found = _sources(store, cloud)
    files = []
    for name in names:
        src = found.get(name.lower())
        if src is None:
            continue
        if cloud["kind"] == "drive":
            data = get(store, f"/me/drive/items/{src['id']}/content", raw=True)
        else:
            page = get(store, f"/me/onenote/pages/{src['id']}/content", raw=True)
            title = src["name"][:-3]
            data = f"# {title}\n\n{html_to_text(page.decode('utf-8', errors='replace'))}".encode()
        files.append((src["name"], data))
        cloud["known"][name.lower()] = src["version"]
    return files


def ignore(course: dict, names: list[str]) -> None:
    ignored = course["cloud"].setdefault("ignored", [])
    for name in names:
        if name.lower() not in {n.lower() for n in ignored}:
            ignored.append(name)


# ---------- Une page OneNote (HTML) en texte ----------
class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "table", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(page: str) -> str:
    parser = _Text()
    parser.feed(page)
    text = html.unescape("".join(parser.out))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
