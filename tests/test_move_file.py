"""Déplacer un fichier d'un cours (matière) vers un autre, avec ou sans ses quiz et ses flashcards."""
from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def _setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    psy = store.create_course("Psychanalyse")
    stats = store.create_course("Statistiques")
    folder = store.create_folder("L1 S1")
    store.move_course(psy["id"], folder["id"])
    store.add_file(psy["id"], "CM1.txt", b"a", "Freud", chapters=[{"title": "Chapitre 1 : Freud", "start": 0}])
    store.add_file(psy["id"], "Stats CM.txt", b"b", "Moyenne", chapters=[{"title": "La moyenne", "start": 0}])
    files = {f["name"]: f["id"] for f in store.get_course(psy["id"])["files"]}
    q = lambda title, scope: store.save_quiz({"course_id": psy["id"], "course_name": "Psychanalyse", "title": title,  # noqa: E731
                                              "scope": scope, "sources": [], "questions": [{"question": "?"}]})
    freud, moyenne = q("Freud", ["Chapitre 1 : Freud"]), q("Moyenne", ["La moyenne"])
    store.save_doc(psy["id"], "cards", {"course_id": psy["id"], "cards": [
        {"id": "c1", "front": "Freud ?", "back": "x", "scope": ["Chapitre 1 : Freud"]},
        {"id": "c2", "front": "Moyenne ?", "back": "y", "scope": ["La moyenne"]}]})
    return store, client, psy, stats, folder, files, freud, moyenne


def test_move_file_with_its_quizzes_and_cards(tmp_path, monkeypatch):
    store, client, psy, stats, _, files, freud, moyenne = _setup(tmp_path, monkeypatch)
    fid = files["Stats CM.txt"]
    assert client.get(f"/api/courses/{psy['id']}/files/{fid}/items").json() == {"quizzes": 1, "cards": 1}
    done = client.post(f"/api/courses/{psy['id']}/files/{fid}/move", json={"target_id": stats["id"]}).json()
    assert done == {"course_id": stats["id"], "course_name": "Statistiques", "quizzes": 1, "cards": 1}
    assert [f["name"] for f in store.get_course(psy["id"])["files"]] == ["CM1.txt"]
    assert [f["name"] for f in store.get_course(stats["id"])["files"]] == ["Stats CM.txt"]
    assert store.file_text(stats["id"], fid) == "Moyenne"
    assert store.get_quiz(moyenne["id"])["course_id"] == stats["id"] and store.get_quiz(freud["id"])["course_id"] == psy["id"]
    assert [c["id"] for c in store.get_doc(stats["id"], "cards")["cards"]] == ["c2"]
    assert [c["id"] for c in store.get_doc(psy["id"], "cards")["cards"]] == ["c1"]
    # Le même nom de fichier existe déjà là-bas : refusé, rien ne bouge
    store.add_file(psy["id"], "Stats CM.txt", b"c", "Autre")
    again = store.get_course(psy["id"])["files"][-1]["id"]
    assert client.post(f"/api/courses/{psy['id']}/files/{again}/move", json={"target_id": stats["id"]}).status_code == 400


def test_move_file_alone_into_a_new_course(tmp_path, monkeypatch):
    store, client, psy, _, folder, files, freud, _ = _setup(tmp_path, monkeypatch)
    done = client.post(f"/api/courses/{psy['id']}/files/{files['CM1.txt']}/move", json={"with_items": False}).json()
    new = store.get_course(done["course_id"])
    assert new["name"] == "CM1" and new["folder_id"] == folder["id"] and done["quizzes"] == done["cards"] == 0
    assert store.get_quiz(freud["id"])["course_id"] == psy["id"]  # quiz et cartes restent dans le cours de départ
