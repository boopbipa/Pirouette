import io
import zipfile

from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def _question(i):
    return {"type": "qcm", "kind": "cours", "question": f"Quelle est la notion numéro {i} du cours ?",
            "choices": [f"Réponse {i}", "Autre A", "Autre B", "Autre C"], "answer": f"Réponse {i}",
            "explanation": "", "source": ""}


def _fill(store):
    folder = store.create_folder("L1 S1")
    neuro = store.create_course("Neurosciences")
    psy = store.create_course("Psychologie du travail")
    for c in (neuro, psy):
        store.move_course(c["id"], folder["id"])
    store.save_quiz({"course_id": neuro["id"], "course_name": "Neurosciences", "title": "Le neurone",
                     "questions": [_question(i) for i in range(3)]})
    store.save_quiz({"course_id": psy["id"], "course_name": "Psychologie du travail", "title": "La justice",
                     "questions": [_question(i) for i in range(10, 12)]})
    store.save_doc(neuro["id"], "cards", {"cards": [{"id": "c1", "front": "Myéline ?", "back": "Gaine de l'axone"}]})
    return folder, neuro, psy


def test_export_folder_and_import_elsewhere(tmp_path, monkeypatch):
    source = Store(tmp_path / "a")
    folder, neuro, psy = _fill(source)
    monkeypatch.setattr(main, "store", source)
    client = TestClient(main.app)
    response = client.get(f"/api/export/folder/{folder['id']}")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "pirouette.json" in names and "Neurosciences/Quiz - Le neurone.txt" in names
    assert "Neurosciences/Flashcards.txt" in names
    pack = response.content

    # Autre Mac : « Neuroscience » existe (nom presque identique), pas la psychologie
    target = Store(tmp_path / "b")
    mine = target.create_course("Neuroscience")
    monkeypatch.setattr(main, "store", target)
    client = TestClient(main.app)
    preview = client.post("/api/pack/preview", files={"file": ("p.zip", pack, "application/zip")}).json()
    rows = {r["name"]: r for r in preview["courses"]}
    assert rows["Neurosciences"]["match"]["id"] == mine["id"] and rows["Neurosciences"]["quizzes"] == 1
    assert rows["Psychologie du travail"]["match"] is None and rows["Neurosciences"]["cards"] == 1
    targets = [mine["id"] if r["name"] == "Neurosciences" else "new" for r in preview["courses"]]
    done = client.post("/api/pack/import", json={"token": preview["token"], "targets": targets}).json()
    assert done == {"courses": 2, "created": 1, "quizzes": 2, "questions": 5, "cards": 1}
    created = next(c for c in target.list_courses() if c["name"] == "Psychologie du travail")
    assert target.get_folder(created["folder_id"])["name"] == "L1 S1"  # rangé dans le semestre du même nom

    # Réimporter le même paquet ne double rien
    again = client.post("/api/pack/preview", files={"file": ("p.zip", pack, "application/zip")}).json()
    targets = [r["match"]["id"] for r in again["courses"]]
    assert all(targets)
    done = client.post("/api/pack/import", json={"token": again["token"], "targets": targets}).json()
    assert done["questions"] == 0 and done["cards"] == 0 and len(target.list_quizzes(mine["id"])) == 1


def test_ignored_course_and_bad_files(tmp_path, monkeypatch):
    source = Store(tmp_path / "a")
    _, neuro, _ = _fill(source)
    monkeypatch.setattr(main, "store", source)
    client = TestClient(main.app)
    pack = client.get(f"/api/export/course/{neuro['id']}").content
    target = Store(tmp_path / "b")
    monkeypatch.setattr(main, "store", target)
    client = TestClient(main.app)
    preview = client.post("/api/pack/preview", files={"file": ("p.zip", pack)}).json()
    done = client.post("/api/pack/import", json={"token": preview["token"], "targets": [""]}).json()
    assert done["courses"] == 0 and not target.list_courses()
    assert client.post("/api/pack/preview", files={"file": ("x.zip", b"pas un zip")}).status_code == 400
    empty = target.create_course("Vide")
    assert client.get(f"/api/export/course/{empty['id']}").status_code == 400
