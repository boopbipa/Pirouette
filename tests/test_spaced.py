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
