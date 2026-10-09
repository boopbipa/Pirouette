"""Mises à jour automatiques de l'app (Mac et Windows).

Windows : Pirouette télécharge l'installateur (Pirouette-Windows-Setup.exe) ; un petit script attend que la fenêtre
soit fermée, puis le lance sans rien demander (installation pour l'utilisateur, sans droits d'administrateur).


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
import time
import sys
import tempfile
from pathlib import Path

import httpx

from . import __version__

REPO = os.getenv("PIROUETTE_UPDATE_REPO", "boopbipa/Pirouette")
API = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3]) or (0,)


WINDOWS = sys.platform == "win32"


def asset_name() -> str:
    if WINDOWS:
        return "Pirouette-Windows-Setup.exe"
    return "Pirouette-Apple-Silicon.dmg" if platform.machine() == "arm64" else "Pirouette-Intel.dmg"


def windows_setup() -> Path:
    """Où attend l'installateur de la prochaine version (Windows)."""
    return Path(os.getenv("LOCALAPPDATA", Path.home())) / "Pirouette-maj" / "Pirouette-Setup.exe"


def app_bundle() -> Path | None:
    """Pirouette.app en cours d'exécution (…/Pirouette.app/Contents/MacOS/Pirouette), si c'est l'app empaquetée."""
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    bundle = Path(sys.executable).resolve().parents[2]
    return bundle if bundle.suffix == ".app" else None


def can_install() -> bool:
    if WINDOWS:
        return bool(getattr(sys, "frozen", False))
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
    ready = windows_setup().exists() if WINDOWS else pending_app() is not None
    result = {"current": __version__, "can_install": can_install(), "ready": ready}
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
    """Script lancé à part : attend que la fenêtre de Pirouette soit fermée (l'utilisateur la quitte quand il veut),
    puis remplace l'app. Il ne la relance pas : c'est l'utilisateur qui la rouvre.

    Seule l'app elle-même compte. Les Pirouette sans fenêtre — celle que l'app Claude garde ouverte pour ses outils
    (« --mcp »), le rappel quotidien (« --remind ») — n'empêchent pas la mise à jour : elles sont arrêtées au moment
    du remplacement (sinon la mise à jour attendait indéfiniment, tant que l'app Claude était ouverte)."""
    q = lambda p: "'" + str(p).replace("'", "'\\''") + "'"  # noqa: E731
    old = bundle.with_name(bundle.name + ".ancienne")
    program = q(str(bundle) + "/Contents/MacOS/")
    old_program = q(str(old) + "/Contents/MacOS/")
    # Rouverte très vite, Pirouette peut encore tourner depuis l'ancienne app déplacée : on ne la supprime alors
    # pas (elle le sera à la prochaine mise à jour) — la supprimer sous ses pieds faisait geler l'app.
    return f"""#!/bin/bash
# Programmes lancés depuis un dossier : « pid commande » (awk plutôt que pgrep : on lit toute la ligne de commande)
lancees() {{ ps -ww -e -o pid= -o args= | awk -v p="$1" '{{ pid = $1; $1 = ""; sub(/^ /, ""); if (index($0, p) && $0 !~ /^(awk|ps|grep) /) print pid, $0 }}'; }}
app_ouverte() {{ lancees "$1" | grep -v -e ' --mcp' -e ' --remind' | grep -q .; }}
while app_ouverte {program}; do sleep 1; done
[ -d {q(new_app)} ] || exit 0
# L'app est fermée : on arrête ses Pirouette sans fenêtre (outils de l'app Claude, rappel) avant de la remplacer
for i in 1 2 3 4 5; do
  pids=$(lancees {program} | awk '{{ print $1 }}')
  [ -z "$pids" ] && break
  kill $pids 2>/dev/null
  sleep 1
done
pids=$(lancees {program} | awk '{{ print $1 }}')
[ -n "$pids" ] && kill -9 $pids 2>/dev/null
lancees {old_program} | grep -q . || rm -rf {q(old)}
if [ ! -e {q(old)} ] && mv {q(bundle)} {q(old)} && mv {q(new_app)} {q(bundle)}; then
  lancees {old_program} | grep -q . || rm -rf {q(old)}
else
  [ -d {q(old)} ] && [ ! -d {q(bundle)} ] && mv {q(old)} {q(bundle)}
fi
xattr -dr com.apple.quarantine {q(bundle)} 2>/dev/null
exit 0
"""


def start_swap(bundle: Path, new_app: Path) -> None:
    """Lance (à part, pour qu'il survive à la fermeture de Pirouette) le script qui remplacera l'app."""
    work = Path(tempfile.mkdtemp(prefix="pirouette-maj-"))
    script = work / "remplacer.sh"
    script.write_text(swap_script(bundle, new_app), encoding="utf-8")
    subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def windows_script(setup: Path, pid: int, app: Path) -> str:
    """PowerShell : attend que la fenêtre de Pirouette soit fermée (cette Pirouette, puis toute autre ouverte entre-temps),
    arrête les Pirouette sans fenêtre (outils de l'app Claude, rappel), installe sans rien demander, puis rouvre Pirouette.
    Si l'installation silencieuse échoue, l'installateur s'ouvre normalement (au lieu d'être effacé en silence, ce qui
    faisait reproposer la même mise à jour en boucle). Tout est noté dans installer.log, à côté de l'installateur."""
    q = lambda p: "'" + str(p).replace("'", "''") + "'"  # noqa: E731
    log, inno = setup.with_name("installer.log"), setup.with_name("setup.log")
    return f"""$ErrorActionPreference = 'Continue'
Start-Transcript -Path {q(log)} -Force | Out-Null
Write-Output "Attente de la fermeture de Pirouette ({pid})"
Wait-Process -Id {pid} -ErrorAction SilentlyContinue
function Fenetres {{ @(Get-CimInstance Win32_Process -Filter "Name='Pirouette.exe'" | Where-Object {{ "$($_.CommandLine)" -notmatch '--mcp|--remind|--server-only' }}) }}
while ((Fenetres).Count -gt 0) {{ Start-Sleep -Seconds 1 }}
if (-not (Test-Path {q(setup)})) {{ Write-Output "Plus d'installateur : rien à faire"; Stop-Transcript | Out-Null; exit }}
Write-Output "Pirouette fermée : arrêt des Pirouette sans fenêtre"
Get-CimInstance Win32_Process -Filter "Name='Pirouette.exe'" | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
Start-Sleep -Seconds 2
$p = Start-Process -FilePath {q(setup)} -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOCANCEL','/NORESTARTAPPLICATIONS',('/LOG="' + {q(inno)} + '"') -Wait -PassThru
Write-Output "Installateur terminé (code $($p.ExitCode))"
if ($p.ExitCode -eq 0) {{
  Remove-Item {q(setup)} -Force -ErrorAction SilentlyContinue
  Start-Process -FilePath {q(app)}
}} else {{
  Write-Output "Échec de l'installation silencieuse : ouverture de l'installateur"
  Start-Process -FilePath {q(setup)}
}}
Stop-Transcript | Out-Null
"""


def _note(setup: Path, line: str) -> None:
    """Journal côté Pirouette (update.log, à côté de l'installateur) : pour comprendre une mise à jour qui coince."""
    try:
        with setup.with_name("update.log").open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def start_windows_install(setup: Path) -> None:
    """Lance à part (il survit à la fermeture de Pirouette) le script qui installera la nouvelle version."""
    script = setup.with_name("installer.ps1")
    script.write_text(windows_script(setup, os.getpid(), Path(sys.executable)), encoding="utf-8-sig")
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script)]
    base = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    for flags in (base | breakaway, base):  # sortir du « job » de Pirouette si Windows le permet
        try:
            process = subprocess.Popen(command, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _note(setup, f"script lancé (pid {process.pid}, attend Pirouette {os.getpid()})")
            return
        except OSError as exc:
            _note(setup, f"lancement du script impossible ({flags:#x}) : {exc!r}")
    raise RuntimeError("Le programme d'installation n'a pas pu être lancé.")


def resume_pending() -> None:
    """Au lancement : une nouvelle version attend encore (Mac redémarré entre-temps…) ? On relance l'attente,
    le remplacement se fera quand cette Pirouette sera quittée."""
    if WINDOWS:
        if getattr(sys, "frozen", False) and windows_setup().exists():
            start_windows_install(windows_setup())
        return
    bundle = app_bundle()
    new_app = pending_app(bundle)
    if bundle is not None and new_app is not None:
        start_swap(bundle, new_app)


async def install(url: str, on_progress) -> None:
    """Télécharge le .dmg, prépare la nouvelle app et lance le remplacement (qui attend que Pirouette soit quittée).
    Windows : télécharge l'installateur, qui se lancera tout seul à la fermeture de Pirouette."""
    if WINDOWS:
        if not can_install():
            raise RuntimeError("La mise à jour automatique ne marche que dans l'app installée.")
        setup = windows_setup()
        setup.parent.mkdir(parents=True, exist_ok=True)
        await download(url, setup.with_suffix(".part"), on_progress)
        setup.with_suffix(".part").replace(setup)
        start_windows_install(setup)
        return
    bundle = app_bundle()
    if bundle is None:
        raise RuntimeError("La mise à jour automatique ne marche que dans l'app Mac.")
    work = Path(tempfile.mkdtemp(prefix="pirouette-maj-"))
    dmg = work / "Pirouette.dmg"
    await download(url, dmg, on_progress)
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


async def download(url: str, target: Path, on_progress) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            done = 0
            with target.open("wb") as out:
                async for chunk in response.aiter_bytes(1 << 16):
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        await on_progress({"type": "progress", "percent": round(100 * done / total)})
