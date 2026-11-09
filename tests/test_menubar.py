from datetime import date

from app import menubar


def test_menu_lists_todays_revisions_and_the_next_ones():
    data = {
        "today": [{"course_id": "a" * 12, "file_id": "b" * 8, "course": "Neurosciences", "title": "CM3", "reason": "1ʳᵉ révision",
                   "cards": 5, "questions": 0, "minutes": 4},
                  {"course_id": "c" * 12, "file_id": "d" * 8, "course": "Psychométrie", "title": "CM2", "reason": "3 jours après le cours",
                   "cards": 0, "questions": 0, "minutes": 2, "before": "Avant ton cours de demain 8 h 30"}],
        "done": [{"course": "Psychanalyse", "title": "CM1"}],
        "upcoming": [{"course": "Neurosciences", "title": "CM2", "due": "2026-11-10"}, {"course": "X", "title": "Y", "due": "2026-11-13"}],
    }
    count, rows = menubar.build(data, date(2026, 11, 9))
    titles = [r.get("title") for r in rows]
    assert count == "2"
    assert "Faire le quiz du jour (environ 4 min)" in titles
    assert "Psychométrie · CM2  —  Avant ton cours de demain 8 h 30" in titles
    assert "✓ Psychanalyse · CM1" in titles and "demain · Neurosciences · CM2" in titles and "ven 13 · X · Y" in titles
    rev = next(r for r in rows if r.get("title", "").startswith("Neurosciences · CM3"))
    assert menubar.route(rev["action"]) == f"#/accueil/r/{'a' * 12}:{'b' * 8}"
    assert next(r for r in rows if r.get("title", "").startswith("Psychométrie"))["action"] == "home"  # pas de quiz : l'accueil
    assert menubar.route("quiz") == "#/accueil/quiz" and rows[-1]["action"] == "open"


def test_nothing_to_do():
    count, rows = menubar.build({"today": [], "done": [], "upcoming": []})
    assert count == "" and rows[0]["title"] == "Rien à réviser aujourd'hui"
