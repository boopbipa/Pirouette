"""Garde-fous sur l'interface (app.js) : deux fonctions du même nom, et la seconde remplace la première sans
prévenir (c'est arrivé : cliquer une flashcard de la grille ne la retournait plus)."""

import re
from collections import Counter
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


def test_no_duplicate_top_level_names():
    source = APP_JS.read_text(encoding="utf-8")
    names = re.findall(r"^(?:async )?function (\w+)|^(?:const|let) (\w+)", source, re.MULTILINE)
    counts = Counter(a or b for a, b in names)
    assert [name for name, n in counts.items() if n > 1] == []


def test_every_id_used_in_app_js_exists_in_the_page():
    html = (APP_JS.parent / "index.html").read_text(encoding="utf-8")
    source = APP_JS.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([\w-]+)"', html + source))  # la page, et les éléments créés par app.js
    used = set(re.findall(r'\$\("#([\w-]+)"\)', source))
    assert sorted(used - ids) == []


def test_no_duplicate_ids_in_the_page():
    """Deux éléments avec le même id : $("#…") ne trouve que le premier (le nombre de cartes à créer était ignoré)."""
    html = (APP_JS.parent / "index.html").read_text(encoding="utf-8")
    counts = Counter(re.findall(r'\sid="([\w-]+)"', html))
    assert [name for name, n in counts.items() if n > 1] == []
