"""Assistant d'installation de l'IA locale : état d'Ollama, choix du modèle selon la mémoire, téléchargement."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from .providers import ollama_provider
from .providers.ollama_provider import list_models, ollama_url

# Modèles proposés, du plus léger au plus capable (taille du téléchargement, mémoire conseillée).
# Qwen 3.5 : bon en français, et Pirouette lui coupe la « réflexion » par défaut (Réglages → IA locale).
MODELS = [
    {"name": "qwen3.5:4b", "label": "Léger", "size_gb": 3.4, "min_ram_gb": 8,
     "note": "Pour les Mac avec 8 Go de mémoire : rapide, mais questions plus simples."},
    {"name": "qwen3.5:9b", "label": "Équilibré", "size_gb": 6.6, "min_ram_gb": 16,
     "note": "Le meilleur compromis qualité / vitesse, idéal avec 16 à 24 Go (MacBook Pro M3 Pro 18 Go…)."},
    {"name": "qwen3.5:27b", "label": "Qualité", "size_gb": 17.0, "min_ram_gb": 32,
     "note": "Les meilleures questions, nettement plus lent. Pour les Mac avec 32 Go ou plus."},
]
MIN_RAM_GB = 8

# Mémoire de lecture de l'IA (« contexte », en tokens) : la quantité de texte lue d'un coup.
# Plus elle est grande, moins le cours est découpé, mais plus il faut de mémoire vive.
CONTEXTS = [
    {"tokens": 8192, "min_ram_gb": 8},
    {"tokens": 16384, "min_ram_gb": 8},
    {"tokens": 32768, "min_ram_gb": 16},
    {"tokens": 65536, "min_ram_gb": 32},
]


def recommended_context(ram: float | None) -> int:
    """Le plus grand contexte conseillé pour cette mémoire (32 768 tokens avec 18 Go)."""
    if ram is None:
        return 16384
    fitting = [c for c in CONTEXTS if ram >= c["min_ram_gb"] - 1]
    return fitting[-1]["tokens"] if fitting else CONTEXTS[0]["tokens"]


def ram_gb() -> float | None:
    """Mémoire vive de l'ordinateur, en Go."""
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return round(int(out.stdout.strip()) / 1024**3, 1)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            memory = MemoryStatus(dwLength=ctypes.sizeof(MemoryStatus))
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                return round(memory.ullTotalPhys / 1024**3, 1)
        except (OSError, AttributeError, ValueError):
            pass
    return None


def recommended_model(ram: float | None) -> dict | None:
    """Le modèle le plus capable qui tient dans la mémoire ; None si la machine est trop juste."""
    if ram is None:
        return MODELS[1]
    fitting = [m for m in MODELS if ram >= m["min_ram_gb"] - 1]  # 18 Go « annoncés » valent ~17,9 Go réels
    return fitting[-1] if fitting else None


def ollama_app_path() -> Path | None:
    if sys.platform == "darwin":
        for base in (Path("/Applications"), Path.home() / "Applications"):
            if (base / "Ollama.app").exists():
                return base / "Ollama.app"
    if os.name == "nt":  # installé pour l'utilisateur : %LOCALAPPDATA%\Programs\Ollama
        app = Path(os.getenv("LOCALAPPDATA", Path.home())) / "Programs" / "Ollama" / "ollama app.exe"
        if app.exists():
            return app
    return None


def is_installed() -> bool:
    return ollama_app_path() is not None or shutil.which("ollama") is not None


def open_ollama() -> bool:
    """Lance l'app Ollama (macOS). Renvoie False si ce n'est pas possible."""
    app = ollama_app_path()
    if app is None:
        return False
    if os.name == "nt":
        subprocess.Popen([str(app)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        subprocess.Popen(["open", "-a", str(app)])
    return True


async def status() -> dict:
    models = await list_models()
    ram = ram_gb()
    recommended = recommended_model(ram)
    return {
        "running": models is not None,
        "installed": models is not None or is_installed(),
        "can_open": ollama_app_path() is not None,
        "models": models or [],
        "ram_gb": ram,
        "enough_ram": ram is None or ram >= MIN_RAM_GB - 1,
        "recommended": recommended and recommended["name"],
        "options": [m | {"installed": any(x.split(":latest")[0] == m["name"] for x in (models or []))}
                    for m in MODELS],
        "context": {
            "auto": recommended_context(ram),
            "chosen": ollama_provider.CONTEXT,  # None = automatique
            "used": ollama_provider.context_size(),
            "options": [c | {"too_big": ram is not None and ram < c["min_ram_gb"] - 1} for c in CONTEXTS],
        },
    }


async def pull(model: str):
    """Télécharge un modèle via Ollama et produit la progression globale (tous fichiers confondus)."""
    layers: dict[str, tuple[int, int]] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5)) as client:
            async with client.stream("POST", f"{ollama_url()}/api/pull", json={"model": model, "stream": True}) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    yield {"type": "error", "message": f"Ollama a refusé le téléchargement : {body[:200]}"}
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if "error" in event:
                        yield {"type": "error", "message": f"Téléchargement impossible : {event['error']}"}
                        return
                    if event.get("digest") and event.get("total"):
                        layers[event["digest"]] = (event.get("completed", 0), event["total"])
                    done = sum(c for c, _ in layers.values())
                    total = sum(t for _, t in layers.values())
                    if event.get("status") == "success":
                        yield {"type": "done", "model": model}
                        return
                    yield {"type": "progress", "status": event.get("status", ""),
                           "completed": done, "total": total,
                           "percent": round(100 * done / total, 1) if total else None}
    except httpx.ConnectError:
        yield {"type": "error", "message": "Ollama ne répond pas : ouvre l'app Ollama puis réessaie."}
        return
    yield {"type": "error", "message": "Le téléchargement s'est interrompu. Réessaie : il reprendra où il s'est arrêté."}
