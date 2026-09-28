"""Mises à jour automatiques de l'app Mac.

Au lancement, Pirouette regarde la dernière version publiée sur GitHub (page « Releases »). S'il y en a une plus
récente, un clic suffit : Pirouette télécharge le .dmg de ce Mac (puce Apple ou Intel) et en sort la nouvelle app, rangée
à côté (Pirouette.app.nouvelle). L'app ne se relance pas toute seule (c'était source de bugs) : on invite l'utilisateur
à la quitter puis la rouvrir. Dès que plus aucune Pirouette ne tourne, un petit script remplace l'ancienne app par la
nouvelle ; la prochaine ouverture est à jour. Les données (cours, cartes…) ne bougent pas : elles
sont rangées à part, dans ~/Library/Application Support/Pirouette.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from . import __version__

REPO = os.getenv("PIROUETTE_UPDATE_REPO", "boopbipa/Pirouette")
API = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3]) or (0,)


def asset_name() -> str:
    return "Pirouette-Apple-Silicon.dmg" if platform.machine() == "arm64" else "Pirouette-Intel.dmg"


def app_bundle() -> Path | None:
    """Pirouette.app en cours d'exécution (…/Pirouette.app/Contents/MacOS/Pirouette), si c'est l'app empaquetée."""
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    bundle = Path(sys.executable).resolve().parents[2]
    return bundle if bundle.suffix == ".app" else None


def can_install() -> bool:
    bundle = app_bundle()
    return bundle is not None and os.access(bundle.parent, os.W_OK)


def pending_app(bundle: Path | None = None) -> Path | None:
    """Nouvelle version déjà téléchargée, qui attend que Pirouette soit quittée pour prendre la place de l'ancienne."""
    bundle = bundle or app_bundle()
    if bundle is None:
        return None
    new_app = bundle.with_name(bundle.name + ".nouvelle")
    return new_app if (new_app / "Contents").is_dir() else None


async def check() -> dict:
    """Dernière version publiée : {current, latest, available, url, page, can_install, ready} ou {error}.
    `ready` : la nouvelle version est déjà téléchargée, il ne reste qu'à quitter et rouvrir Pirouette."""
    result = {"current": __version__, "can_install": can_install(), "ready": pending_app() is not None}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            response = await client.get(API, headers={"Accept": "application/vnd.github+json"})
    except httpx.HTTPError:
        return result | {"error": "offline"}
    if response.status_code == 404:
        return result | {"error": "private"}  # dépôt privé : les versions ne sont pas visibles sans compte
    if response.status_code != 200:
        return result | {"error": "unavailable"}
    release = response.json()
    latest = str(release.get("tag_name", "")).lstrip("v")
    asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name()), None)
    return result | {
        "latest": latest,
        "available": bool(latest) and parse_version(latest) > parse_version(__version__) and asset is not None,
        "url": asset and asset.get("browser_download_url"),
        "page": release.get("html_url"),
    }


def swap_script(bundle: Path, new_app: Path) -> str:
    """Script lancé à part : attend que plus aucune Pirouette ne tourne (l'utilisateur la quitte quand il veut),
    puis remplace l'app. Il ne la relance pas : c'est l'utilisateur qui la rouvre."""
    q = lambda p: "'" + str(p).replace("'", "'\\''") + "'"  # noqa: E731
    old = bundle.with_name(bundle.name + ".ancienne")
    running = q(str(bundle) + "/Contents/MacOS/")
    return f"""#!/bin/bash
while pgrep -f {running} >/dev/null 2>&1; do sleep 1; done
sleep 1
[ -d {q(new_app)} ] || exit 0
rm -rf {q(old)}
if mv {q(bundle)} {q(old)} && mv {q(new_app)} {q(bundle)}; then
  rm -rf {q(old)}
else
  [ -d {q(old)} ] && [ ! -d {q(bundle)} ] && mv {q(old)} {q(bundle)}
fi
xattr -dr com.apple.quarantine {q(bundle)} 2>/dev/null
"""


def start_swap(bundle: Path, new_app: Path) -> None:
    """Lance (à part, pour qu'il survive à la fermeture de Pirouette) le script qui remplacera l'app."""
    work = Path(tempfile.mkdtemp(prefix="pirouette-maj-"))
    script = work / "remplacer.sh"
    script.write_text(swap_script(bundle, new_app), encoding="utf-8")
    subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def resume_pending() -> None:
    """Au lancement : une nouvelle version attend encore (Mac redémarré entre-temps…) ? On relance l'attente,
    le remplacement se fera quand cette Pirouette sera quittée."""
    bundle = app_bundle()
    new_app = pending_app(bundle)
    if bundle is not None and new_app is not None:
        start_swap(bundle, new_app)


async def install(url: str, on_progress) -> None:
    """Télécharge le .dmg, prépare la nouvelle app et lance le remplacement (qui attend que Pirouette soit quittée)."""
    bundle = app_bundle()
    if bundle is None:
        raise RuntimeError("La mise à jour automatique ne marche que dans l'app Mac.")
    work = Path(tempfile.mkdtemp(prefix="pirouette-maj-"))
    dmg = work / "Pirouette.dmg"
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            done = 0
            with dmg.open("wb") as out:
                async for chunk in response.aiter_bytes(1 << 16):
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        await on_progress({"type": "progress", "percent": round(100 * done / total)})
    await on_progress({"type": "status", "message": "Préparation de la nouvelle version…"})
    mount = work / "volume"
    mount.mkdir()
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg)],
                   check=True, capture_output=True)
    try:
        new_app = bundle.with_name(bundle.name + ".nouvelle")
        shutil.rmtree(new_app, ignore_errors=True)
        subprocess.run(["ditto", str(mount / "Pirouette.app"), str(new_app)], check=True, capture_output=True)
    finally:
        subprocess.run(["hdiutil", "detach", "-force", str(mount)], capture_output=True)
    shutil.rmtree(work, ignore_errors=True)
    start_swap(bundle, new_app)
