from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def test_the_daily_size_is_for_the_whole_day(tmp_path, monkeypatch):
    """60 cartes à revoir, révision du jour de 30 : une fois les 30 faites, la révision du jour est finie (100 %),
    elle ne repart pas sur 30 autres."""
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    store.save_settings(daily_size=30, new_per_day=200)
    course = store.create_course("Neuro")
    cards = [store.add_card(course["id"], f"Question {i}", f"Réponse {i}") for i in range(60)]
    client = TestClient(main.app)
    assert client.get("/api/progress").json()["today"]["cards"] == 30
    for card in cards[:30]:
        client.post(f"/api/courses/{course['id']}/cards/{card['id']}/review", json={"rating": "good"})
    progress = client.get("/api/progress").json()
    assert progress["done_today"]["cards"] == 30
    assert progress["today"] == {"cards": 0, "questions": 0}
    assert client.get("/api/session?mode=today&questions=0").json()["items"] == []
