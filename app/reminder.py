"""Rappel quotidien (Mac) : une notification « 12 cartes t'attendent » à l'heure choisie, même app fermée.

Pirouette dépose un petit fichier de lancement dans ~/Library/LaunchAgents : chaque jour à l'heure dite, macOS
lance `Pirouette --remind`, qui compte les cartes du jour et affiche une notification s'il y en a (rien sinon).
Le désactiver supprime ce fichier.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "app.pirouette.rappel"


def supported() -> bool:
    return sys.platform == "darwin" and os.getenv("PIROUETTE_DESKTOP") == "1"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def command() -> list[str]:
    """Commande lancée chaque jour : l'app elle-même (ou desktop.py hors de l'app empaquetée)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--remind"]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "desktop.py"), "--remind"]


def plist(hour: int, minute: int, program: list[str]) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": program,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "ProcessType": "Background",
    })


def _launchctl(*args: str) -> None:
    try:
        subprocess.run(["launchctl", *args], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


def install(hour: int, minute: int) -> None:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(path))  # l'ancien rappel, s'il existe
    path.write_bytes(plist(hour, minute, command()))
    _launchctl("bootstrap", domain, str(path))


def uninstall() -> None:
    path = plist_path()
    if path.exists():
        _launchctl("bootout", f"gui/{os.getuid()}", str(path))
        path.unlink()


def message(count: int) -> str | None:
    if count <= 0:
        return None
    return f"{count} carte{'s' if count > 1 else ''} t'attend{'ent' if count > 1 else ''} aujourd'hui. On révise ?"


def notify(text: str, title: str = "Pirouette") -> bool:
    script = f"display notification {_quote(text)} with title {_quote(title)} sound name \"Glass\""
    try:
        return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
