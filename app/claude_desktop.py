"""Brancher Pirouette sur l'app Claude pour Mac (Claude Desktop).

L'app Claude lit la liste de ses outils locaux dans ~/Library/Application Support/Claude/claude_desktop_config.json
(clé « mcpServers »). Pirouette y ajoute (ou en retire) sa ligne, sans toucher aux autres outils. Il faut ensuite
quitter et rouvrir l'app Claude pour qu'elle en tienne compte.

Sous Windows, l'app Claude installée en « MSIX » (l'installateur actuel) ne lit pas %APPDATA%\\Claude : Windows lui
donne une copie à part, dans %LOCALAPPDATA%\\Packages\\Claude_…\\LocalCache\\Roaming\\Claude. Pirouette écrit donc
partout où l'app Claude peut lire (le Mac n'a qu'un seul fichier, inchangé).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NAME = "pirouette"


FILE = "claude_desktop_config.json"


def config_paths() -> list[Path]:
    """Les fichiers de configuration que l'app Claude peut lire. Le premier est celui qu'on montre."""
    if sys.platform != "win32":
        return [Path.home() / "Library" / "Application Support" / "Claude" / FILE]
    # Windows : les copies des installations MSIX (Packages\Claude_xxxx, Packages\Anthropic.ClaudeDesktop_xxxx)
    # d'abord (ce sont celles que l'app lit), puis l'emplacement classique %APPDATA%\Claude
    packages = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Packages"
    msix = []
    try:
        msix = sorted(d / "LocalCache" / "Roaming" / "Claude" / FILE for d in packages.iterdir()
                      if d.is_dir() and "claude" in d.name.lower() and (d / "LocalCache").is_dir())
    except OSError:
        pass
    return [*msix, Path(os.getenv("APPDATA", Path.home())) / "Claude" / FILE]


def config_path() -> Path:
    return config_paths()[0]


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


def _entry(path: Path) -> dict | None:
    try:
        return _read(path).get("mcpServers", {}).get(NAME)
    except ValueError:
        return None


def status(path: Path | None = None) -> dict:
    paths = [path] if path else config_paths()
    return {"installed": any(_entry(p) for p in paths), "claude_found": any(p.parent.exists() for p in paths),
            "config": str(paths[0])}


def _install_one(path: Path) -> None:
    data = _read(path)
    program = command()
    data.setdefault("mcpServers", {})[NAME] = {"command": program[0], "args": program[1:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def install(path: Path | None = None) -> dict:
    paths = [path] if path else config_paths()
    errors = []
    for p in paths:
        try:
            _install_one(p)
        except ValueError as exc:  # fichier illisible : on n'y touche pas
            errors.append(exc)
    if len(errors) == len(paths):
        raise errors[0]
    return status(path)


def snippet() -> str:
    """Le texte à coller soi-même dans le fichier de configuration de l'app Claude (branchement à la main)."""
    program = command()
    return json.dumps({"mcpServers": {NAME: {"command": program[0].replace("\\", "/"), "args": program[1:]}}},
                      ensure_ascii=False, indent=2)


def needs_refresh() -> bool:
    """Déjà branchée quelque part, mais un fichier n'a pas (ou plus) le bon chemin vers Pirouette : à remettre."""
    entries = [_entry(p) for p in config_paths()]
    program = command()
    return any(entries) and any(not e or [e.get("command"), *e.get("args", [])] != program for e in entries)


def uninstall(path: Path | None = None) -> dict:
    for p in [path] if path else config_paths():
        try:
            data = _read(p)
        except ValueError:
            continue
        if NAME in data.get("mcpServers", {}):
            del data["mcpServers"][NAME]
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status(path)
