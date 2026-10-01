from fastapi.testclient import TestClient

from tests.helpers import split_all
from app import main, news
from app.storage import Store

BASE = ("Chapitre 1 : Le neurone\n" + "Le neurone transmet l'influx nerveux le long de son axone. " * 12
        + "\n\nChapitre 2 : La synapse\n" + "La synapse relie deux neurones par des neurotransmetteurs. " * 12 + "\n")
ADDED = ("La myéline accélère la conduction de l'influx nerveux. Les nœuds de Ranvier permettent une conduction "
         "saltatoire, beaucoup plus rapide que la conduction continue des fibres sans myéline. ")


def test_new_text_keeps_only_new_sentences():
    assert news.new_text(BASE, BASE) == ""
    fresh = news.new_text(BASE, BASE.replace("\n\nChapitre 2", " " + ADDED + "\n\nChapitre 2"))
    assert "myéline" in fresh and "synapse" not in fresh and "transmet" not in fresh


def test_news_after_a_new_version(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    upload = lambda text: client.post(f"/api/courses/{cid}/files",  # noqa: E731
                                      files=[("files", ("neuro.txt", text.encode(), "text/plain"))])
    upload(BASE)
    split_all(client, cid)
    assert client.get(f"/api/courses/{cid}/news").json()["units"] == []  # premier dépôt : rien de « nouveau »
    upload(BASE.replace("\n\nChapitre 2", " " + ADDED + "\n\nChapitre 2"))
    units = client.get(f"/api/courses/{cid}/news").json()["units"]
    assert [u["title"] for u in units] == ["Chapitre 1 : Le neurone"]
    assert "Ranvier" in units[0]["text"] and units[0]["questions"] >= 3 and units[0]["cards"] >= 3
    # Troisième version avant d'avoir traité : la référence reste la première
    upload(BASE.replace("\n\nChapitre 2", " " + ADDED + "\n\nChapitre 2") + "\n")
    assert len(client.get(f"/api/courses/{cid}/news").json()["units"]) == 1

    submitted = []
    monkeypatch.setattr(main, "_submit_quiz", lambda *a, **k: submitted.append(("quiz", a[7], k["text"])) or {})
    monkeypatch.setattr(main, "_submit_cards", lambda *a, **k: submitted.append(("cards", a[5], k["text"])) or {})
    client.post(f"/api/courses/{cid}/news/create", data={"provider": "local"})
    assert [kind for kind, _, _ in submitted] == ["quiz", "cards"]
    assert all("Ranvier" in text and "synapse" not in text for _, _, text in submitted)
    assert client.get(f"/api/courses/{cid}/news").json()["units"] == []  # traité : on n'en parle plus
