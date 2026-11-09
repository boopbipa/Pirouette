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

DAYS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


def build(data: dict, today: date | None = None) -> tuple[str, list[dict]]:
    """Le chiffre affiché à côté de l'icône, et les lignes du menu ({title, action, enabled}, ou {separator}).
    `data` : la réponse de /api/revisions."""
    today = today or date.today()
    pending = data.get("today") or []
    done = data.get("done") or []
    quiz = [r for r in pending if r.get("cards", 0) + r.get("questions", 0) > 0]
    rows: list[dict] = []
    if pending:
        minutes = sum(r.get("minutes", 0) for r in quiz)
        rows.append({"title": f"Aujourd'hui : {len(pending)} cours à réviser", "enabled": False})
        if quiz:
            rows.append({"title": f"Faire le quiz du jour (environ {minutes} min)", "action": "quiz", "enabled": True})
    elif done:
        rows.append({"title": "Tes cours du jour sont révisés ✓", "enabled": False})
    else:
        rows.append({"title": "Rien à réviser aujourd'hui", "enabled": False})
    if pending or done:
        rows.append({"separator": True})
    for r in pending:
        why = r.get("before") or r.get("reason") or ""
        has_quiz = r.get("cards", 0) + r.get("questions", 0) > 0
        rows.append({"title": f"{r['course']} · {r['title']}  —  {why}",
                     "action": f"rev:{r['course_id']}:{r['file_id']}" if has_quiz else "home", "enabled": True})
    for r in done:
        rows.append({"title": f"✓ {r['course']} · {r['title']}", "enabled": False})
    later = [r for r in (data.get("upcoming") or []) if r.get("due", "") > today.isoformat()][:4]
    if later:
        rows.append({"separator": True})
        rows.append({"title": "Prochaines révisions", "enabled": False})
        for r in later:
            day = date.fromisoformat(r["due"])
            label = "demain" if (day - today).days == 1 else f"{DAYS[day.weekday()]} {day.day}"
            rows.append({"title": f"{label} · {r['course']} · {r['title']}", "enabled": False})
    rows.append({"separator": True})
    rows.append({"title": "Ouvrir Pirouette", "action": "open", "enabled": True})
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


def _act(action: str) -> None:
    """Un clic dans le menu (sur le fil principal) : la fenêtre passe devant, sur la bonne page."""
    window = _keep.get("window")
    if window is None:
        return
    try:
        from AppKit import NSApplication

        window.show()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass
    target = route(action)
    # evaluate_js attend le fil principal : on l'appelle d'ailleurs, sinon l'app se bloquerait
    threading.Thread(target=lambda: window.evaluate_js(f"location.hash = {json.dumps(target)}"), daemon=True).start()
    threading.Timer(3, _refresh).start()
