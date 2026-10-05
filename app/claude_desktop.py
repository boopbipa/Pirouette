"""Brancher Pirouette sur l'app Claude pour Mac (Claude Desktop).

L'app Claude lit la liste de ses outils locaux dans ~/Library/Application Support/Claude/claude_desktop_config.json
(clé « mcpServers »). Pirouette y ajoute (ou en retire) sa ligne, sans toucher aux autres outils. Il faut ensuite
quitter et rouvrir l'app Claude pour qu'elle en tienne compte.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NAME = "pirouette"


def config_path() -> Path:
    if sys.platform == "win32":  # app Claude pour Windows : %APPDATA%\Claude
        return Path(os.getenv("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def command() -> list[str]:
    """Ce que l'app Claude lance : l'app Pirouette elle-même en mode « --mcp » (ou desktop.py hors de l'app)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mcp"]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "desktop.py"), "--mcp"]


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Le fichier de configuration de l'app Claude est illisible : Pirouette n'y touche pas.") from exc
    return data if isinstance(data, dict) else {}


def status(path: Path | None = None) -> dict:
    path = path or config_path()
    try:
        entry = _read(path).get("mcpServers", {}).get(NAME)
    except ValueError:
        entry = None
    return {"installed": bool(entry), "claude_found": path.parent.exists(), "config": str(path)}


def install(path: Path | None = None) -> dict:
    path = path or config_path()
    data = _read(path)
    program = command()
    data.setdefault("mcpServers", {})[NAME] = {"command": program[0], "args": program[1:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status(path)


def uninstall(path: Path | None = None) -> dict:
    path = path or config_path()
    data = _read(path)
    if NAME in data.get("mcpServers", {}):
        del data["mcpServers"][NAME]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status(path)
