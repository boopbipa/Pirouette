from collections import Counter
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import main, spaced
from app.storage import Store

D = date(2026, 11, 2)


def day(n: int) -> date:
    return D + timedelta(days=n)


def test_a_course_comes_back_after_0_1_3_7_15_30_days_then_monthly():
    entry = {"id": "aaaa1111", "added_at": D.isoformat()}
    assert spaced.view(entry, D)["today"] and spaced.view(entry, D)["reason"] == "Ton cours d'aujourd'hui"
    seen = []
    for n in [0, 1, 3, 7, 15, 30, 60]:
        assert spaced.view(entry, day(n))["today"], n
        assert not spaced.view(entry, day(n - 1))["today"] or n == 0
        spaced.done(entry, day(n), 0.9)
        seen.append(n)
    assert entry["rev"]["due"] == day(90).isoformat()


def test_a_failed_revision_comes_back_tomorrow_at_the_same_step():
    entry = {"id": "aaaa1111", "added_at": D.isoformat()}
    spaced.done(entry, D, 0.9)          # J+0 réussie : J+1
    spaced.done(entry, day(1), 0.4)     # J+1 ratée : demain, même étape
    view = spaced.view(entry, day(2))
    assert view["today"] and view["reason"] == "À refaire" and view["step"] == 1
    spaced.done(entry, day(2), 0.8)     # réussie : la suivante est J+3 (l'écart est gardé : pas le jour même)
    assert entry["rev"]["due"] == day(4).isoformat()


def test_a_late_revision_keeps_its_gap():
    entry = {"id": "aaaa1111", "added_at": D.isoformat()}
    spaced.done(entry, D, 1)
    spaced.done(entry, day(1), 1)
    spaced.done(entry, day(3), 1)       # suivante : J+7
    spaced.done(entry, day(9), 1)       # faite 2 jours en retard : la J+15 passe à J+17 (8 jours d'écart)
    assert entry["rev"]["due"] == day(17).isoformat()


def test_old_courses_join_the_rhythm_without_a_backlog():
    entry = {"id": "aaaa1111", "added_at": D.isoformat()}
    assert spaced.view(entry, day(5))["due"] == day(7).isoformat()   # entre J+3 et J+7 : la J+7
    old = {"id": "bbbb2222", "added_at": (D - timedelta(days=200)).isoformat()}
    view = spaced.view(old, D)
    assert not view["today"] and 1 <= view["in_days"] <= 14


def test_kind_of_file():
    assert spaced.kind("CM1.pdf") == "PDF" and spaced.kind("notes.DOCX") == "Word" and spaced.kind("x") == ""


def test_revisions_api_and_session(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    course = store.create_course("Neurosciences")
    store.add_file(course["id"], "CM1.pdf", b"%PDF", "Le neurone transmet l'influx nerveux.")
    fid = store.get_course(course["id"])["files"][0]["id"]
    for i in range(3):
        store.add_card(course["id"], f"Question {i}", f"Réponse {i}")
    client = TestClient(main.app)
    data = client.get("/api/revisions").json()
    [item] = data["today"]
    assert item["title"] == "CM1" and item["kind"] == "PDF" and item["cards"] == 3 and item["reason"] == "Ton cours d'aujourd'hui"
    session = client.get(f"/api/session?mode=revision&pick={course['id']}:{fid}").json()
    assert session["cards"] == 3 and all(i["revision"] == f"{course['id']}:{fid}" for i in session["items"])
    after = client.post(f"/api/revisions/{course['id']}/{fid}/done", json={"score": 1}).json()
    assert after["done_today"] and after["in_days"] == 1
    data = client.get("/api/revisions").json()
    assert data["today"] == [] and data["done"][0]["file_id"] == fid
    assert "quiz_count" not in store.get_course(course["id"])  # la matière est enregistrée telle quelle


def test_postpone_to_tomorrow(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    course = store.create_course("Neurosciences")
    store.add_file(course["id"], "CM1.pdf", b"%PDF", "texte")
    fid = store.get_course(course["id"])["files"][0]["id"]
    client = TestClient(main.app)
    assert client.get("/api/revisions").json()["today"][0]["start"]  # à son heure
    client.post(f"/api/revisions/{course['id']}/{fid}/postpone", json={})
    data = client.get("/api/revisions").json()
    assert data["today"] == [] and data["upcoming"][0]["due"] == (date.today() + timedelta(days=1)).isoformat()


def test_reading_a_course_never_overwrites_your_edits(tmp_path, monkeypatch):
    import subprocess
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    # (sur le Mac des tests : ne rien ouvrir pour de vrai)
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(main.os, "startfile", lambda *a: None, raising=False)
    course = store.create_course("Neurosciences")
    store.add_file(course["id"], "CM1.txt", b"version 1", "version 1")
    fid = store.get_course(course["id"])["files"][0]["id"]
    client = TestClient(main.app)
    r = client.post(f"/api/courses/{course['id']}/files/{fid}/open").json()
    copy = tmp_path / "a-relire" / "CM1.txt"
    assert r["copy"] and copy.read_bytes() == b"version 1"
    copy.write_bytes(b"version 1 + mes notes")  # tu modifies la copie (Pages, Word…)
    client.post(f"/api/courses/{course['id']}/files/{fid}/open")
    assert copy.read_bytes() == b"version 1 + mes notes"  # jamais écrasée
    # Avec un dossier suivi : c'est ton vrai fichier qui s'ouvre
    folder = tmp_path / "Neuro"
    folder.mkdir()
    (folder / "CM1.txt").write_bytes(b"version 1")
    from app import watch
    c = store.get_course(course["id"])
    watch.link(c, str(folder))
    store._save_course(c)
    r = client.post(f"/api/courses/{course['id']}/files/{fid}/open").json()
    assert r["copy"] is False and r["path"].endswith("Neuro/CM1.txt")


def test_a_failed_card_says_why_it_comes_back(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    course = store.create_course("Neurosciences")
    store.add_card(course["id"], "Neurone ?", "Cellule excitable")
    store.add_card(course["id"], "Axone ?", "Conduit le potentiel d'action")
    deck = store.get_doc(course["id"], "cards")
    deck["cards"][0].update(reviews=2, last_rating="again", status="review", lapses=1, interval=0, due=date.today().isoformat())
    store.save_doc(course["id"], "cards", deck)
    client = TestClient(main.app)
    assert client.get(f"/api/progress?course={course['id']}").json()["weak"]["cards"] == 1
    items = client.get(f"/api/session?mode=weak&course={course['id']}").json()["items"]
    assert [(i["card"]["front"], i["retry"]) for i in items] == [("Neurone ?", "Ratée la dernière fois")]
    assert main._question_retry({"right": 1, "wrong": 3, "last": True}) == "À retravailler"
    assert main._question_retry({"right": 2, "wrong": 0, "last": True}) == ""


def test_many_courses_added_at_once_are_spread_over_the_next_days(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    today = date.today()
    for name, n in [("Neurosciences", 5), ("Psychométrie", 4), ("Méthode expérimentale", 2)]:
        course = store.create_course(name)
        for i in range(n):
            store.add_file(course["id"], f"CM{i + 1}.pdf", b"%PDF", f"texte {i}")
    client = TestClient(main.app)
    data = client.get("/api/revisions").json()
    assert len(data["today"]) == spaced.FIRST_PER_DAY                      # 4 aujourd'hui, pas 11
    assert len({r["course"] for r in data["today"]}) == 3                   # en alternant les matières
    days = Counter(r["due"] for r in data["upcoming"])
    assert days[(today + timedelta(days=1)).isoformat()] == 4 and days[(today + timedelta(days=2)).isoformat()] == 3
    assert all(r["late"] == 0 for r in data["today"] + data["upcoming"])    # reportées, pas « en retard »
    again = client.get("/api/revisions").json()                            # rien ne bouge au deuxième passage
    assert [r["file_id"] for r in again["today"]] == [r["file_id"] for r in data["today"]]
    # Une fois faites, elles gardent leur rythme (J+1 après le jour où elles sont révisées), sans se regrouper
    first = data["today"][0]
    client.post(f"/api/revisions/{first['course_id']}/{first['file_id']}/done", json={"score": 1})
    assert len(client.get("/api/revisions").json()["today"]) == 3


def test_a_few_courses_are_not_spread():
    today = D
    entries = [("a", {"id": f"f{i}", "name": f"CM{i}", "added_at": today.isoformat()}) for i in range(4)]
    assert spaced.spread_first(entries, today) == [] and all(e["rev"]["due"] == today.isoformat() for _, e in entries)
    old = ("a", {"id": "vieux", "name": "x", "added_at": today.isoformat(), "rev": {"start": "2026-10-01", "step": 3, "due": today.isoformat(), "last": "2026-10-25"}})
    more = [("b", {"id": f"g{i}", "name": f"CM{i}", "added_at": today.isoformat()}) for i in range(3)]
    moved = spaced.spread_first([old, *entries, *more], today)
    assert len(moved) == 3 and old[1]["rev"]["due"] == today.isoformat()  # une révision déjà dans son rythme n'est jamais déplacée
