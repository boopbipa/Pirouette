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
    # Sobre : la matière seulement, sans raison ni prochaines révisions
    assert titles == ["Commencer : Neurosciences", None, "Psychométrie", "✓ Psychanalyse", None,
                      "Ouvrir Pirouette", "Quitter Pirouette"]
    rev = next(r for r in rows if r.get("title") == "Commencer : Neurosciences")
    assert menubar.route(rev["action"]) == f"#/accueil/r/{'a' * 12}:{'b' * 8}"
    assert next(r for r in rows if r.get("title") == "Psychométrie")["action"] == "home"  # pas de quiz : l'accueil
    assert menubar.route("quiz") == "#/accueil/quiz"
    _, rows = menubar.build({"today": [], "done": [{"course": "Neurosciences"}]})
    assert [r.get("title") for r in rows][:2] == ["Tout est révisé aujourd'hui ✓", "Quiz du jour (10 min)"]


def test_two_courses_of_the_same_subject_are_told_apart():
    item = {"course_id": "a" * 12, "course": "Psychanalyse", "cards": 1, "questions": 0, "minutes": 30}
    _, rows = menubar.build({"today": [item | {"file_id": "b" * 8, "title": "Le rêve"}, item | {"file_id": "c" * 8, "title": "Lapsus"}]})
    assert [r.get("title") for r in rows][:3] == ["Commencer : Psychanalyse", None, "Psychanalyse · Lapsus"]


def test_nothing_to_do():
    count, rows = menubar.build({"today": [], "done": [], "upcoming": []})
    assert count == "" and rows[0]["title"] == "Quiz du jour (10 min)"


def test_mac_settings(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.storage import Store
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    view = client.get("/api/mac").json()
    assert view["menubar_keep"] is True and view["login_available"] is False
    assert client.put("/api/mac", json={"menubar_keep": False}).json()["menubar_keep"] is False
    assert client.put("/api/mac", json={"login": True}).status_code == 400  # pas une app installée sur Mac
