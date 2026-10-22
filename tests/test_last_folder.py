from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def test_new_course_goes_to_the_last_used_semester(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    assert client.get("/api/settings").json()["last_folder"] == ""
    s1 = client.post("/api/folders", json={"name": "S1"}).json()["id"]
    s2 = client.post("/api/folders", json={"name": "S2"}).json()["id"]
    assert client.get("/api/settings").json()["last_folder"] == s2  # semestre tout juste créé
    course = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.put(f"/api/courses/{course}/folder", json={"folder_id": s1})
    assert client.get("/api/settings").json()["last_folder"] == s1  # cours rangé à la main dans S1
    client.put(f"/api/courses/{course}/folder", json={"folder_id": None})
    assert client.get("/api/settings").json()["last_folder"] == s1  # « Sans semestre » ne fait pas oublier S1
