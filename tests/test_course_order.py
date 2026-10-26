from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def test_courses_keep_the_order_chosen_by_dragging(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    s1 = client.post("/api/folders", json={"name": "S1"}).json()["id"]
    ids = {}
    for name in ("Philo", "Psycho", "Neuro"):
        ids[name] = client.post("/api/courses", json={"name": name}).json()["id"]
        client.put(f"/api/courses/{ids[name]}/folder", json={"folder_id": s1})

    def names(folder):
        return [c["name"] for c in client.get("/api/courses").json() if c.get("folder_id") == folder]

    order = ["Neuro", "Philo", "Psycho"]
    assert client.put("/api/courses-order", json={"folder_id": s1, "courses": [ids[n] for n in order]}).status_code == 200
    assert names(s1) == order
    client.patch(f"/api/courses/{ids['Psycho']}", json={"name": "Psycho"})  # une matière modifiée ne remonte plus
    assert names(s1) == order
    # Déposée dans un autre groupe (ici hors semestre) : elle y va, à sa place
    client.put("/api/courses-order", json={"folder_id": None, "courses": [ids["Philo"]]})
    assert names(s1) == ["Neuro", "Psycho"] and names(None) == ["Philo"]
