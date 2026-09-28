"""Pirouette en application de bureau.

Le serveur tourne en arrière-plan (sur un port libre de cet ordinateur) et l'interface s'affiche dans une vraie
fenêtre, sans navigateur ni Terminal. C'est le point d'entrée de Pirouette.app.

    python desktop.py                 # ouvre la fenêtre
    python desktop.py --server-only   # serveur seul (vérifications, sans interface graphique)
    python desktop.py --remind        # rappel quotidien : notification s'il y a des cartes à réviser
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path


def data_dir() -> Path:
    """Dossier standard des données d'application, propre à chaque système."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home()))
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "Pirouette"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(port: int):
    import uvicorn

    from app.main import app

    # Boucle et protocole explicites : pas d'import dynamique, ce qui simplifie l'empaquetage.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                            loop="asyncio", http="h11", ws="none", lifespan="off")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("Le serveur de Pirouette n'a pas démarré.")
        time.sleep(0.05)
    return server


class DesktopApi:
    """Fonctions appelables depuis l'interface (window.pywebview.api.*).

    Les liens target="_blank" s'ouvrent déjà dans le navigateur par défaut (réglage par défaut de pywebview),
    et window.print() ouvre la fenêtre d'impression de macOS.
    """

    def quit(self) -> None:
        """Ferme Pirouette (après avoir préparé une mise à jour, qui relance l'app toute seule)."""
        import webview

        for window in list(webview.windows):
            window.destroy()

    def import_legacy_data(self) -> dict:
        """Récupère les cours d'une ancienne version (le dossier « data » à côté de run.sh)."""
        import webview

        folders = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        if not folders:
            return {"ok": False, "message": ""}
        source = Path(folders[0])
        if (source / "data").is_dir():  # l'utilisateur a choisi le dossier de l'app plutôt que « data »
            source = source / "data"
        if not ((source / "courses").is_dir() or (source / "quizzes").is_dir()):
            return {"ok": False, "message": "Ce dossier ne contient pas de données Pirouette "
                                            "(choisis le dossier « data » de l'ancienne version)."}
        target = Path(os.environ["QUIZZ_DATA_DIR"])
        copied = 0
        for sub in ("courses", "quizzes"):
            if (source / sub).is_dir():
                for item in (source / sub).iterdir():
                    dest = target / sub / item.name
                    if dest.exists():
                        continue  # on ne remplace jamais ce qui existe déjà
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
                    copied += 1
        profile = source / "profile.json"
        if profile.exists() and not (target / "profile.json").exists():
            shutil.copy2(profile, target / "profile.json")
        return {"ok": True, "message": f"{copied} élément(s) récupéré(s)."}


def remind() -> None:
    """Lancé chaque jour par macOS (voir app/reminder.py) : notification s'il y a des cartes du jour."""
    from app.reminder import message, notify
    from app.storage import Store

    store = Store(Path(os.environ["QUIZZ_DATA_DIR"]))
    if not store.get_settings().get("reminder_time"):
        return
    text = message(store.stats()["cards_today"])
    if text:
        notify(text)


def main() -> None:
    os.environ.setdefault("QUIZZ_DATA_DIR", str(data_dir()))
    os.environ["PIROUETTE_DESKTOP"] = "1"
    Path(os.environ["QUIZZ_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    if "--remind" in sys.argv:
        return remind()
    port = free_port()
    server = start_server(port)
    url = f"http://127.0.0.1:{port}/"

    if "--server-only" in sys.argv:
        print(json.dumps({"url": url, "data_dir": os.environ["QUIZZ_DATA_DIR"]}), flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    import webview

    webview.create_window(
        "Pirouette", url, js_api=DesktopApi(),
        width=1180, height=860, min_size=(420, 560),
        background_color="#FAF8F3",
        text_select=True,  # indispensable pour sélectionner un mot et demander sa définition
    )
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
