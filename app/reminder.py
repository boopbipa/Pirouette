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
from datetime import date, timedelta
from pathlib import Path

from . import plan as plans

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


def daily_text(store, today: date | None = None) -> str | None:
    """Texte du rappel : avec un plan de révision, seulement les jours de séance (premier et dernier jour de la
    période, tant que la séance n'est pas faite) ; sans plan, s'il y a des cartes du jour."""
    today = today or date.today()
    found = store.plans()
    if not found:
        return message(store.stats()["cards_today"])
    due = []
    for kind, owner_id, name, plan in found:
        status = plans.status(plan, store.active_days(store.scope_course_ids(kind, owner_id)), today)
        deadline = date.fromisoformat(status["deadline"])
        if not status["done_today"] and today in {deadline - timedelta(days=plan["every"] - 1), deadline}:
            due.append(name)
    if not due:
        return None
    return f"Séance de révision prévue aujourd'hui : {', '.join(due)}. On s'y met ?"


def notify(text: str, title: str = "Pirouette") -> bool:
    """Une notification de l'ordinateur (Mac : centre de notifications ; Windows : notification de Windows 10/11)."""
    if sys.platform == "win32":
        return _notify_windows(text, title)
    script = f"display notification {_quote(text)} with title {_quote(title)} sound name \"Glass\""
    try:
        return subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _notify_windows(text: str, title: str) -> bool:
    """Notification Windows, sans module à installer : celle de PowerShell (seule appli toujours inscrite pour en
    montrer), avec le titre « Pirouette » en première ligne."""
    xml_escape = lambda t: t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;")  # noqa: E731
    toast = (f"<toast><visual><binding template='ToastGeneric'><text>{xml_escape(title)}</text>"
             f"<text>{xml_escape(text)}</text></binding></visual></toast>").replace("'", "''")
    script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
              "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
              f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{toast}');"
              "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
              "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
              "[Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                              timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
