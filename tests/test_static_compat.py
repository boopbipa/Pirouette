"""Les Mac restés sur un ancien macOS affichent l'app avec un vieux Safari : une seule écriture trop récente dans le
script, et toute l'interface reste vide. On vérifie les pièges connus."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [ROOT / "app" / "static" / "app.js", ROOT / "app" / "static" / "compat.js", ROOT / "web" / "app.js"]


def test_no_regex_lookbehind():
    # (?<= et (?<! : Safari 16.4 ; avant, le script entier est refusé (erreur de syntaxe)
    for path in SCRIPTS:
        assert not re.search(r"\(\?<[=!]", path.read_text(encoding="utf-8")), path.name


def test_compat_is_loaded_before_the_app():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/compat.js") < html.index("/static/app.js")
