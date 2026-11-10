from fastapi.testclient import TestClient

from app import main
from app.mcp_server import Pirouette
from app.storage import Store


def test_a_flagged_card_or_question_is_set_aside_until_corrected(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    course = store.create_course("Neurosciences")
    store.add_file(course["id"], "CM1.txt", b"x", "Le neurone est une cellule excitable.")
    good = store.add_card(course["id"], "Neurone ?", "Cellule excitable")
    bad = store.add_card(course["id"], "Axone ?", "Il reçoit le message")
    client = TestClient(main.app)
    fronts = lambda: sorted(i["card"]["front"] for i in client.get(f"/api/session?mode=cards&course={course['id']}").json()["items"])  # noqa: E731
    assert fronts() == ["Axone ?", "Neurone ?"]
    client.post(f"/api/courses/{course['id']}/flag", json={"kind": "card", "card_id": bad["id"], "note": "c'est la dendrite qui reçoit"})
    assert fronts() == ["Neurone ?"]
    info = client.get(f"/api/courses/{course['id']}").json()
    assert info["flagged"] == 1 and info["outdated"] == 1
    # Claude la voit, avec la remarque
    news = Pirouette(store).pirouette_mise_a_jour(course["id"])
    assert f"c:{bad['id']}" in news and "SIGNALÉE FAUSSE" in news and "dendrite" in news
    # Corrigée (par Claude ou à la main) : de nouveau posée, plus signalée
    store.update_card(course["id"], bad["id"], "Axone ?", "Il conduit le message")
    assert fronts() == ["Axone ?", "Neurone ?"]
    assert client.get(f"/api/courses/{course['id']}").json()["flagged"] == 0
    assert good["id"]
