"""« Ouvrir Pirouette au démarrage du Mac » : Pirouette dans les éléments d'ouverture de macOS (Réglages Système ›
Général › Ouverture). Seulement pour l'app installée (Pirouette.app), pas en développement."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _bundle() -> Path | None:
    """Le dossier Pirouette.app qui contient le programme en cours, s'il y en a un."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def available() -> bool:
    return _bundle() is not None


def _service():
    try:
        from ServiceManagement import SMAppService  # macOS 13 et plus
    except ImportError:
        return None
    return SMAppService.mainAppService()


def enabled() -> bool:
    if not available():
        return False
    service = _service()
    if service is not None:
        return service.status() == 1  # activé
    out = subprocess.run(["osascript", "-e", 'tell application "System Events" to get the name of every login item'],
                         capture_output=True, text=True, timeout=20)
    return "Pirouette" in out.stdout


def set_enabled(on: bool) -> bool:
    """Active ou retire l'ouverture au démarrage. Renvoie l'état obtenu."""
    if not available():
        raise RuntimeError("Seulement avec l'app Pirouette installée sur un Mac.")
    service = _service()
    if service is not None:
        ok, error = (service.registerAndReturnError_(None) if on else service.unregisterAndReturnError_(None))
        if not ok and on:
            raise RuntimeError(f"macOS a refusé : {error}. Tu peux l'ajouter toi-même dans Réglages Système › Général › Ouverture.")
        return enabled()
    # macOS 11 et 12 : par les éléments d'ouverture de System Events (le Mac demande l'autorisation une fois)
    app = str(_bundle())
    script = (f'tell application "System Events" to make login item at end with properties {{path:"{app}", hidden:false}}'
              if on else 'tell application "System Events" to delete (every login item whose name is "Pirouette")')
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if done.returncode != 0 and on:
        raise RuntimeError("macOS a refusé. Tu peux l'ajouter toi-même dans Réglages Système › Général › Ouverture.")
    return enabled()
