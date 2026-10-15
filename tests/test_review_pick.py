"""Réviser une sélection de cours et de fichiers (de matières différentes), et les sélections enregistrées."""
from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def _setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    psy, neuro = store.create_course("Psychanalyse"), store.create_course("Neurosciences")
    store.add_file(psy["id"], "CM1.pages", b"a", "Freud", chapters=[{"title": "Freud", "start": 0}])
    store.add_file(psy["id"], "Glossaire_psy.pdf", b"b", "Mots", chapters=[{"title": "Glossaire", "start": 0}])
    store.add_file(neuro["id"], "Neuro.pdf", b"c", "Neurone", chapters=[{"title": "Le neurone", "start": 0}])
    f = {fl["name"]: fl["id"] for c in (psy, neuro) for fl in store.get_course(c["id"])["files"]}
    card = lambda i, scope: {"id": i, "front": i, "back": "x", "scope": [scope], "status": "new", "reviews": 0}  # noqa: E731
    store.save_doc(psy["id"], "cards", {"cards": [card("freud1", "Freud"), card("freud2", "Freud"), card("glos", "Glossaire")]})
    store.save_doc(neuro["id"], "cards", {"cards": [card("neur", "Le neurone")]})
    store.save_quiz({"course_id": psy["id"], "course_name": "Psychanalyse", "title": "Freud", "scope": ["Freud"], "sources": [],
                     "questions": [{"type": "qcm", "question": "Q?", "choices": ["a", "b"], "answer": "a"}]})
    return store, client, psy, neuro, f


def test_session_on_a_pick_of_files(tmp_path, monkeypatch):
    store, client, psy, neuro, f = _setup(tmp_path, monkeypatch)
    pick = f"{psy['id']}:{f['CM1.pages']};{neuro['id']}:*"
    data = client.get("/api/session", params={"mode": "all", "pick": pick}).json()
    assert sorted(i["card"]["id"] for i in data["items"] if i["kind"] == "card") == ["freud1", "freud2", "neur"]
    assert data["questions"] == 1
    cards = client.get("/api/session", params={"mode": "cards", "filter": "all", "pick": f"{psy['id']}:{f['Glossaire_psy.pdf']}"}).json()
    assert [i["card"]["id"] for i in cards["items"]] == ["glos"]
    assert client.get("/api/session", params={"mode": "today", "pick": "inconnu:*"}).status_code == 400

    catalog = client.get("/api/review/catalog").json()["courses"]
    psy_files = next(c for c in catalog if c["id"] == psy["id"])["files"]
    assert [(x["name"], x["cards"], x["questions"]) for x in psy_files] == [("CM1", 2, 1), ("Glossaire psy", 1, 0)]


def test_saved_selections(tmp_path, monkeypatch):
    store, client, psy, neuro, f = _setup(tmp_path, monkeypatch)
    sets = client.post("/api/review/sets", json={"name": "Partiel Psycha", "pick": f"{psy['id']}:{f['CM1.pages']}"}).json()
    assert [(s["name"], s["due"]) for s in sets] == [("Partiel Psycha", 2)]
    assert client.post("/api/review/sets", json={"name": " ", "pick": f"{psy['id']}:*"}).status_code == 400
    sets = client.post("/api/review/sets", json={"name": "partiel psycha", "pick": f"{neuro['id']}:*"}).json()
    assert len(sets) == 1 and sets[0]["due"] == 1  # même nom : remplacée
    assert client.delete(f"/api/review/sets/{sets[0]['id']}").json() == []
