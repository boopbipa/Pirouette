"""Une matière, plusieurs cours (fichiers) : chacun garde ses quiz et ses cartes, même si deux fichiers ont un chapitre
du même nom (« Début du document »)."""
from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def _subject(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    cid = client.post("/api/courses", json={"name": "Psychanalyse"}).json()["id"]
    for name in ("CM1.txt", "Glossaire.txt"):
        client.post(f"/api/courses/{cid}/files", files=[("files", (name, ("Texte du cours. " * 40).encode(), "text/plain"))])
    course = store.get_course(cid)
    cm1, glossary = course["files"]
    cm1["chapters"] = [{"title": "Début du document"}, {"title": "Chapitre 1 — Le moi"}]
    glossary["chapters"] = [{"title": "Début du document"}, {"title": "20. Contenu latent"}]
    store._save_course(course)
    cards = [{"id": "a", "front": "Moi ?", "back": "r", "status": "new", "scope": ["Début du document"]},  # ancienne : sans origine
             {"id": "b", "front": "Latent ?", "back": "r", "status": "new", "scope": ["Début du document"],
              "origin": ["Glossaire.txt — Début du document"]},
             {"id": "c", "front": "Le moi ?", "back": "r", "status": "new", "scope": ["Chapitre 1 — Le moi"]}]
    store.save_doc(cid, "cards", {"cards": cards})
    store.save_quiz({"course_id": cid, "title": "Q", "scope": ["Début du document"], "sources": ["CM1.txt — Début du document"],
                     "questions": [{"type": "reponse_courte", "question": "?", "answer": "a", "choices": []}]})
    return client, cid, cm1["id"], glossary["id"]


def test_same_chapter_title_counts_per_file(tmp_path, monkeypatch):
    client, cid, cm1, glossary = _subject(tmp_path, monkeypatch)
    chapters = client.get(f"/api/mastery?course={cid}").json()[0]["chapters"]
    start = {c["key"].split("-")[0]: c for c in chapters if c["title"] == "Début du document"}
    assert start[cm1]["cards"] == 1 and start[cm1]["questions"] == 1  # la carte sans origine revient au premier fichier
    assert start[glossary]["cards"] == 1 and start[glossary]["questions"] == 0


def test_revise_one_course_of_the_subject(tmp_path, monkeypatch):
    client, cid, cm1, glossary = _subject(tmp_path, monkeypatch)
    every = client.get(f"/api/session?mode=all&course={cid}").json()
    assert every["cards"] == 3 and every["questions"] == 1
    mine = client.get(f"/api/session?mode=all&course={cid}&file={glossary}").json()
    assert [i["card"]["id"] for i in mine["items"]] == ["b"]
    first = client.get(f"/api/session?mode=all&course={cid}&file={cm1}").json()
    assert first["cards"] == 2 and first["questions"] == 1
    decks = client.get(f"/api/cards/decks?course={cid}&file={glossary}").json()
    assert decks["all"]["total"] == 1


def test_new_file_is_not_split_until_asked(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    cid = client.post("/api/courses", json={"name": "Psy"}).json()["id"]
    text = "Chapitre 1 : Le moi\n" + "Le moi. " * 40 + "\nChapitre 2 : Le ça\n" + "Le ça. " * 40
    send = lambda: client.post(f"/api/courses/{cid}/files", files=[("files", ("cm.txt", text.encode(), "text/plain"))])  # noqa: E731
    send()
    f = client.get(f"/api/courses/{cid}").json()["files"][0]
    assert f["chapters"] == [] and f["chapters_by"] == "none"  # un seul bloc : Pirouette propose de découper
    send()  # nouvelle version d'un fichier pas découpé : toujours pas découpé
    assert client.get(f"/api/courses/{cid}").json()["files"][0]["chapters"] == []
    split = client.post(f"/api/courses/{cid}/files/{f['id']}/chapters/auto").json()["course"]["files"][0]
    assert [c["title"] for c in split["chapters"]] == ["Chapitre 1 : Le moi", "Chapitre 2 : Le ça"]
    cleared = client.delete(f"/api/courses/{cid}/files/{f['id']}/chapters").json()["course"]["files"][0]
    assert cleared["chapters"] == [] and cleared["chapters_by"] == "none"
    kept = client.delete(f"/api/courses/{cid}/files/{f['id']}/chapters?keep=true").json()["course"]["files"][0]
    assert kept["chapters_by"] == "whole"
