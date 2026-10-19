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
        entries = list(packages.iterdir())
    except OSError:
        entries = []
    for d in entries:
        # Un dossier protégé (autre app) ne doit pas empêcher de trouver celui de Claude : on les teste un par un.
        try:
            roaming = d / "LocalCache" / "Roaming" / "Claude"
            if ("claude" in d.name.lower() or "anthropic" in d.name.lower()) and (d / "LocalCache").is_dir() \
                    or roaming.is_dir():
                msix.append(roaming / FILE)
        except OSError:
            continue
    return [*sorted(set(msix)), Path(os.getenv("APPDATA", Path.home())) / "Claude" / FILE]


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


def _claude_processes() -> list[str]:
    """Sous Windows : le chemin des programmes « claude.exe » en cours (l'app Claude pour ordinateur, si elle tourne)."""
    if sys.platform != "win32":
        return []
    import subprocess
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-Process -Name claude -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def _log_dirs() -> list[Path]:
    dirs = [path.parent / "logs" for path in config_paths()]
    if sys.platform == "win32":
        local = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        try:
            for d in (local / "Packages").iterdir():
                if "claude" in d.name.lower() or "anthropic" in d.name.lower():
                    dirs += [d / "LocalCache" / "Local" / "Claude" / "logs", d / "LocalState" / "logs"]
        except OSError:
            pass
        dirs += [local / "AnthropicClaude" / "logs", local / "Claude" / "logs"]
    return list(dict.fromkeys(dirs))


def diagnose() -> dict:
    """Pour comprendre un branchement qui ne marche pas : quelle app Claude tourne, chaque fichier de configuration
    (existe-t-il, contient-il Pirouette, avec le bon chemin ? que contient son dossier ?) et les journaux de l'app
    Claude (les lignes qui parlent de Pirouette ou d'erreurs MCP)."""
    program = command()
    files = []
    for path in config_paths():
        info = {"path": str(path), "exists": path.exists(), "readable": True, "pirouette": False, "same_command": False}
        try:
            entry = _read(path).get("mcpServers", {}).get(NAME)
        except (ValueError, OSError) as exc:
            entry, info["readable"], info["error"] = None, False, str(exc)
        if entry:
            info["pirouette"] = True
            info["command"] = entry.get("command")
            info["same_command"] = [entry.get("command"), *entry.get("args", [])] == program
        try:  # ce que l'app Claude a mis dans ce dossier (vide à part notre fichier : elle ne l'utilise pas)
            info["neighbours"] = sorted(p.name for p in path.parent.iterdir())[:25]
        except OSError:
            info["neighbours"] = []
        files.append(info)
    logs = []
    for folder in _log_dirs():
        try:
            names = sorted(p for p in folder.iterdir() if p.suffix == ".log")
        except OSError:
            continue
        for log in names:
            try:
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            keep = [line for line in lines if any(w in line.lower() for w in ("pirouette", "mcp", "error"))]
            logs.append({"path": str(log), "lines": keep[-8:], "modified": int(log.stat().st_mtime)})
    return {"platform": sys.platform, "command": program, "command_exists": Path(program[0]).exists(),
            "claude_running": _claude_processes(), "files": files, "logs": logs[-8:]}
