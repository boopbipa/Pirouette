import pytest
from fastapi.testclient import TestClient

from app import main
from app.explain import course_excerpt
from app.providers import ollama_provider
from app.storage import Store

COURSE = "\n\n".join([f"Paragraphe {i} sur autre chose. " * 20 for i in range(10)]
                     + ["Le neurone est la cellule de base du système nerveux. Il transmet l'influx nerveux."]
                     + [f"Suite {i}. " * 30 for i in range(10)])


def test_excerpt_is_around_the_source():
    excerpt = course_excerpt(COURSE, "Le neurone est la cellule de base du système nerveux.", "Qu'est-ce qu'un neurone ?")
    assert "influx nerveux" in excerpt and len(excerpt) < len(COURSE)
    # Sans source : le passage le plus proche de la question
    assert "influx" in course_excerpt(COURSE, "", "Que transmet le neurone, cellule du système nerveux ?")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    return TestClient(main.app)


def test_explain_endpoint(client, monkeypatch):
    seen = []

    async def fake_ask(system, schema, prompt, model=None):
        seen.append(prompt)
        return {"explanation": "Le neurone est la cellule qui transmet l'influx nerveux."}

    monkeypatch.setattr(ollama_provider, "ask", fake_ask)
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", COURSE.encode(), "text/plain"))])
    body = {"course_id": cid, "question": "Qu'est-ce qu'un neurone ?", "expected": "La cellule de base du système nerveux.",
            "given": "Un muscle", "source": "Le neurone est la cellule de base du système nerveux."}
    assert client.post("/api/explain", json=body).json()["explanation"].startswith("Le neurone")
    assert "influx nerveux" in seen[0] and "Réponse de l'étudiant : Un muscle" in seen[0]


def test_items_that_no_longer_match_the_updated_course(client):
    store = main.store
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    old = "Le neurone est la cellule de base du système nerveux.\n\nLa synapse relie deux neurones entre eux."
    client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", old.encode(), "text/plain"))])
    qs = [{"type": "qcm", "question": "Neurone ?", "choices": ["a", "b"], "answer": "a", "source": "Le neurone est la cellule de base du système nerveux."},
          {"type": "qcm", "question": "Synapse ?", "choices": ["a", "b"], "answer": "a", "source": "La synapse relie deux neurones entre eux."},
          {"type": "qcm", "question": "Sans source ?", "choices": ["a", "b"], "answer": "a"}]
    quiz = store.save_quiz({"title": "Q", "questions": qs, "course_id": cid})
    store.add_card(cid, "Écrite à la main", "sans source")
    deck = store.get_doc(cid, "cards")
    deck["cards"].append({"id": "c2", "front": "Synapse ?", "back": "Relie deux neurones", "source": "La synapse relie deux neurones entre eux.",
                          "status": "new", "reviews": 0, "last_reviewed": None})
    store.save_doc(cid, "cards", deck)
    assert client.get(f"/api/courses/{cid}").json()["outdated"] == 0

    # Nouvelle version du fichier : la synapse n'y est plus
    new = "Le neurone est la cellule de base du système nerveux.\n\nLe cortex est la couche externe du cerveau."
    client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", new.encode(), "text/plain"))])
    assert client.get(f"/api/courses/{cid}").json()["outdated"] == 2
    found = client.get(f"/api/courses/{cid}/outdated").json()
    assert [q["question"] for q in found["questions"]] == ["Synapse ?"] and [c["id"] for c in found["cards"]] == ["c2"]

    # On garde la carte, on supprime la question
    left = client.post(f"/api/courses/{cid}/outdated", json={"keep": [found["cards"][0]["key"]],
                                                             "questions": [{"quiz_id": quiz["id"], "index": 1}]}).json()
    assert left == {"questions": [], "cards": []}
    assert [q["question"] for q in store.get_quiz(quiz["id"])["questions"]] == ["Neurone ?", "Sans source ?"]
