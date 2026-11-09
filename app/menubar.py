"""L'icône de Pirouette dans la barre des menus du Mac (en haut à droite, à côté du Wi-Fi) : le nombre de cours à
réviser aujourd'hui, et en un clic le quiz du jour, une révision précise ou Pirouette. Tant que Pirouette est
ouverte (même fenêtre cachée derrière les autres)."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from datetime import date

REFRESH = 60  # secondes entre deux mises à jour du compteur

def build(data: dict, today: date | None = None) -> tuple[str, list[dict]]:
    """Le chiffre affiché à côté de l'icône, et les lignes du menu ({title, action, enabled}, ou {separator}).
    `data` : la réponse de /api/revisions."""
    pending = data.get("today") or []
    done = data.get("done") or []
    quiz = [r for r in pending if r.get("cards", 0) + r.get("questions", 0) > 0]
    rows: list[dict] = []
    if pending:
        if quiz:
            rows.append({"title": "Faire le quiz du jour", "action": "quiz", "enabled": True})
    elif done:
        rows.append({"title": "Tout est révisé aujourd'hui ✓", "enabled": False})
    else:
        rows.append({"title": "Rien à réviser aujourd'hui", "enabled": False})
    if rows and (pending or done):
        rows.append({"separator": True})
    # Juste la matière (le nom du cours seulement si deux cours de la même matière sont à réviser)
    names = [r["course"] for r in pending]
    for r in pending:
        has_quiz = r.get("cards", 0) + r.get("questions", 0) > 0
        title = r["course"] if names.count(r["course"]) == 1 else f"{r['course']} · {r['title']}"
        rows.append({"title": title, "action": f"rev:{r['course_id']}:{r['file_id']}" if has_quiz else "home", "enabled": True})
    for r in done:
        rows.append({"title": f"✓ {r['course']}", "enabled": False})
    rows.append({"separator": True})
    rows.append({"title": "Ouvrir Pirouette", "action": "open", "enabled": True})
    rows.append({"title": "Quitter Pirouette", "action": "quit", "enabled": True})
    return (str(len(pending)) if pending else ""), rows


def route(action: str) -> str:
    """La page à ouvrir pour une ligne du menu."""
    if action == "quiz":
        return "#/accueil/quiz"
    if action.startswith("rev:"):
        return "#/accueil/r/" + action[4:]
    return "#/accueil"


# ---------- La partie Mac (AppKit) ----------
_keep: dict = {}


def start(window, base_url: str) -> None:
    """À appeler une fois l'app lancée (depuis n'importe quel fil) : l'icône est créée sur le fil principal."""
    if sys.platform != "darwin":
        return
    try:
        from PyObjCTools import AppHelper
    except ImportError:
        return
    _keep["window"], _keep["base"] = window, base_url.rstrip("/")
    AppHelper.callAfter(_create)


def _target_class():
    if "Target" in _keep:
        return _keep["Target"]
    from Foundation import NSObject

    class PirouetteMenuTarget(NSObject):
        def act_(self, sender):
            _act(str(sender.representedObject() or "open"))

        def menuWillOpen_(self, menu):  # le menu s'ouvre : on remet le compteur à jour juste après
            threading.Thread(target=_refresh, daemon=True).start()

    _keep["Target"] = PirouetteMenuTarget
    return PirouetteMenuTarget


def _create() -> None:
    try:
        from AppKit import NSImage, NSMenu, NSStatusBar, NSVariableStatusItemLength

        item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        target = _target_class().alloc().init()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("graduationcap", "Pirouette")
        button = item.button()
        if image is not None:
            image.setTemplate_(True)  # noir ou blanc selon la barre des menus, comme les icônes du système
            button.setImage_(image)
            button.setImagePosition_(2)  # l'icône à gauche du chiffre
        else:
            button.setTitle_("Pirouette")
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(target)
        item.setMenu_(menu)
        _keep.update(item=item, target=target, menu=menu)
        _fill("", [{"title": "Ouvrir Pirouette", "action": "open", "enabled": True}])
        threading.Thread(target=_loop, daemon=True).start()
    except Exception as exc:  # l'icône n'est qu'un plus : Pirouette marche sans
        print(f"Barre des menus : {exc!r}", file=sys.stderr)


def _fill(count: str, rows: list[dict]) -> None:
    from AppKit import NSMenuItem

    item, menu, target = _keep["item"], _keep["menu"], _keep["target"]
    item.button().setTitle_(f" {count}" if count else "")
    menu.removeAllItems()
    for row in rows:
        if row.get("separator"):
            menu.addItem_(NSMenuItem.separatorItem())
            continue
        entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(row["title"], "act:" if row.get("action") else None, "")
        if row.get("action"):
            entry.setTarget_(target)
            entry.setRepresentedObject_(row["action"])
        entry.setEnabled_(bool(row.get("enabled")))
        menu.addItem_(entry)


def _refresh() -> None:
    from PyObjCTools import AppHelper

    try:
        with urllib.request.urlopen(_keep["base"] + "/api/revisions", timeout=20) as response:  # noqa: S310 — l'app elle-même
            data = json.loads(response.read())
    except Exception:
        return
    count, rows = build(data)
    AppHelper.callAfter(_fill, count, rows)


def _loop() -> None:
    while True:
        _refresh()
        time.sleep(REFRESH)


QUITTING = threading.Event()  # on quitte pour de bon (⌘Q, « Quitter », une mise à jour) : la fenêtre se ferme vraiment


def reveal() -> None:
    """La fenêtre revient (sur le fil principal) : l'icône du Dock aussi."""
    window = _keep.get("window")
    if window is None:
        return
    try:
        from AppKit import NSApplication

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)  # comme une app normale : dans le Dock et le ⌘Tab
        window.show()
        app.activateIgnoringOtherApps_(True)
    except Exception:
        pass


def quit_app() -> None:
    QUITTING.set()
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().terminate_(None)
    except Exception:
        pass


def _terminating() -> bool:
    """La fermeture vient-elle de ⌘Q / Quitter (macOS demande à toute l'app de s'arrêter) plutôt que du bouton rouge ?"""
    frame = sys._getframe()
    while frame is not None:
        if frame.f_code.co_name == "applicationShouldTerminate_":
            return True
        frame = frame.f_back
    return False


def keep_running(window, wanted) -> None:
    """Fermer la fenêtre (bouton rouge, ⌘W) la cache : Pirouette reste dans la barre des menus et continue de tenir
    l'agenda à jour. ⌘Q, « Quitter » et les mises à jour ferment pour de bon. `wanted()` : le réglage (activé ou non)."""
    if sys.platform != "darwin":
        return
    _keep["window"] = window

    def closing():
        if QUITTING.is_set() or _terminating() or "item" not in _keep or not wanted():
            return True
        try:
            from AppKit import NSApplication

            window.hide()
            NSApplication.sharedApplication().setActivationPolicy_(1)  # plus dans le Dock : seulement en haut de l'écran
        except Exception:
            return True
        return False  # la fenêtre n'est pas fermée, juste cachée

    window.events.closing += closing
    # Rouvrir Pirouette (Dock, Spotlight, Launchpad) pendant qu'elle tourne cachée : la fenêtre revient
    try:
        import objc
        from webview.platforms.cocoa import BrowserView

        def reopen(self, app, has_windows):
            reveal()
            return True

        objc.classAddMethods(BrowserView.AppDelegate, [objc.selector(
            reopen, selector=b"applicationShouldHandleReopen:hasVisibleWindows:", signature=b"Z@:@Z")])
    except Exception as exc:
        print(f"Rouvrir depuis le Dock : {exc!r}", file=sys.stderr)


def _act(action: str) -> None:
    """Un clic dans le menu (sur le fil principal) : la fenêtre passe devant, sur la bonne page."""
    if action == "quit":
        return quit_app()
    window = _keep.get("window")
    if window is None:
        return
    reveal()
    target = route(action)
    # evaluate_js attend le fil principal : on l'appelle d'ailleurs, sinon l'app se bloquerait
    threading.Thread(target=lambda: window.evaluate_js(f"location.hash = {json.dumps(target)}"), daemon=True).start()
    threading.Timer(3, _refresh).start()
