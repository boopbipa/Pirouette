"""Pirouette en application de bureau.

Le serveur tourne en arrière-plan (sur un port libre de cet ordinateur) et l'interface s'affiche dans une vraie
fenêtre, sans navigateur ni Terminal. C'est le point d'entrée de Pirouette.app.

    python desktop.py                 # ouvre la fenêtre
    python desktop.py --server-only   # serveur seul (vérifications, sans interface graphique)
    python desktop.py --remind        # rappel quotidien : notification s'il y a des cartes à réviser
    python desktop.py --mcp           # branché à l'app Claude : outils Pirouette pour Claude (voir app/mcp_server.py)
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


def std_stream(which: str):
    """Entrée / sortie standard en binaire. L'app Windows n'a pas de console : Python n'y branche alors rien
    (sys.stdout vaut None), mais l'app Claude, elle, lui passe des tuyaux ; on les récupère auprès de Windows."""
    stream = getattr(sys, which)
    if stream is not None:
        return stream.buffer
    import ctypes
    import msvcrt

    handle = ctypes.windll.kernel32.GetStdHandle({"stdin": -10, "stdout": -11, "stderr": -12}[which])
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if which == "stdin" else os.O_WRONLY)
    return os.fdopen(fd, "rb" if which == "stdin" else "wb", buffering=0 if which != "stdin" else -1)


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
        """Ferme Pirouette (après une mise à jour : la nouvelle version prend la place de l'ancienne, à rouvrir).

        Appelée depuis l'interface : fermer la fenêtre depuis ce fil d'exécution pouvait bloquer l'app (il fallait
        forcer à quitter). On ferme donc à part, et si rien ne s'est fermé au bout de quelques secondes, on arrête
        le programme net (les données sont déjà enregistrées sur le disque à chaque action)."""
        import webview

        def close() -> None:
            for window in list(webview.windows):
                try:
                    window.destroy()
                except Exception:
                    pass

        threading.Thread(target=close, daemon=True).start()
        threading.Timer(4, lambda: os._exit(0)).start()

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
    """Lancé chaque jour par macOS (voir app/reminder.py) : notification les jours de séance (ou de cartes du jour)."""
    from app.reminder import daily_text, notify
    from app.storage import Store

    store = Store(Path(os.environ["QUIZZ_DATA_DIR"]))
    if not store.get_settings().get("reminder_time"):
        return
    text = daily_text(store)
    if text:
        notify(text)


def refresh_claude_link() -> None:
    """Branchée à l'app Claude, Pirouette a pu être déplacée (autre dossier) : on remet le bon chemin."""
    from app import claude_desktop

    try:
        entry = claude_desktop._read(claude_desktop.config_path()).get("mcpServers", {}).get(claude_desktop.NAME)
        program = claude_desktop.command()
        if entry and [entry.get("command"), *entry.get("args", [])] != program:
            claude_desktop.install()
    except (OSError, ValueError):
        pass


def serve_claude() -> None:
    """Lancé par l'app Claude (voir app/mcp_server.py) : Pirouette répond à Claude par l'entrée / la sortie standard."""
    from app.mcp_server import serve
    from app.storage import Store

    serve(Store(Path(os.environ["QUIZZ_DATA_DIR"])), std_stream("stdin"), std_stream("stdout"))


def app_menu() -> list:
    """Barre des menus du Mac : « Rechercher une mise à jour… » dans le menu Pirouette, et un menu « Mise à jour »."""
    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator

    from app import __version__

    def call(action: str):
        def run() -> None:
            if webview.windows:
                webview.windows[0].evaluate_js(f"window.pirouetteMenu && window.pirouetteMenu({action!r})")
        return run

    return [
        Menu("__app__", [MenuAction("Rechercher une mise à jour…", call("check"))]),
        Menu("Mise à jour", [
            MenuAction(f"Version actuelle : {__version__}", call("version")),
            MenuSeparator(),
            MenuAction("Rechercher une mise à jour…", call("check")),
            MenuAction("Installer la mise à jour", call("install")),
        ]),
    ]


def main() -> None:
    os.environ.setdefault("QUIZZ_DATA_DIR", str(data_dir()))
    os.environ["PIROUETTE_DESKTOP"] = "1"
    Path(os.environ["QUIZZ_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    if "--remind" in sys.argv:
        return remind()
    if "--mcp" in sys.argv:
        return serve_claude()
    port = free_port()
    server = start_server(port)
    url = f"http://127.0.0.1:{port}/"

    if "--server-only" in sys.argv:
        out = std_stream("stdout")
        out.write((json.dumps({"url": url, "data_dir": os.environ["QUIZZ_DATA_DIR"]}) + "\n").encode())
        out.flush()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    from app import backup, updater
    from app.main import store

    updater.resume_pending()  # une mise à jour téléchargée attend encore : elle s'installera à la fermeture
    refresh_claude_link()
    try:
        store.merge_chapter_quizzes()  # des quiz sur les mêmes chapitres : un seul, sans questions en double
    except Exception:
        pass
    saving = threading.Thread(target=backup.run_if_due, args=(store,), daemon=True)  # sauvegarde automatique
    saving.start()

    import webview

    webview.create_window(
        "Pirouette", url, js_api=DesktopApi(),
        width=1180, height=860, min_size=(420, 560),
        background_color="#FAF8F3",
        text_select=True,  # indispensable pour sélectionner un mot et demander sa définition
    )
    # La barre des menus (« Rechercher une mise à jour… ») est celle du Mac ; sous Windows, tout passe par Réglages.
    webview.start(menu=app_menu() if sys.platform == "darwin" else [])
    server.should_exit = True
    # Fenêtre fermée : on s'arrête pour de bon. Sinon Python attend ses tâches de fond (une création de quiz, une
    # requête en cours…) et l'app restait ouverte sans fenêtre, bloquée, empêchant aussi la mise à jour de s'installer.
    saving.join(timeout=15)  # une sauvegarde en cours se termine d'abord
    os._exit(0)


if __name__ == "__main__":
    main()
