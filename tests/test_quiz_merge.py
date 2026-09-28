import time

from fastapi.testclient import TestClient

from app import main
from app.providers import ollama_provider
from app.storage import Store

FILLER = "Le neurone transmet l'influx nerveux grâce à la synapse chimique. " * 12
COURSE = f"Chapitre 1 : Le neurone\n{FILLER}\nChapitre 2 : La synapse\n{FILLER}"


def q(text, answer, source="Le neurone transmet l'influx nerveux grâce à la synapse chimique."):
    return {"type": "qcm", "kind": "cours", "question": text, "choices": [answer, "Un os", "Un muscle", "Rien"],
            "answer": answer, "explanation": "", "source": source, "key_terms": []}


def test_merge_chapter_quizzes_keeps_stats_and_drops_similar(tmp_path):
    store = Store(tmp_path)
    base = {"course_id": "c1", "scope": ["Chapitre 1 — Le neurone"]}
    first = store.save_quiz({**base, "title": "A", "created_at": "2026-09-01T10:00:00",
                             "questions": [q("Que transmet le neurone ?", "L'influx nerveux")]})
    second = store.save_quiz({**base, "scope": ["Chapitre 1 : Le neurone"], "title": "B", "created_at": "2026-09-02T10:00:00",
                              "attempts": [{"score": 1, "total": 2, "date": "2026-09-02"}],
                              "stats": {"1": {"right": 0, "wrong": 2, "last": False, "date": "2026-09-02"}},
                              "questions": [q("Que transmet donc le neurone ?", "L'influx nerveux"),
                                            q("Grâce à quoi le neurone transmet-il ?", "La synapse chimique")]})
    other = store.save_quiz({**base, "scope": ["Chapitre 2 : La synapse"], "title": "C", "questions": [q("Autre ?", "X")]})
    themed = store.save_quiz({**base, "focus": "la synapse", "title": "D", "questions": [q("Thème ?", "Y")]})
    assert store.merge_chapter_quizzes() == 1
    ids = {s["id"] for s in store.list_quizzes()}
    assert ids == {first["id"], other["id"], themed["id"]}
    merged = store.get_quiz(first["id"])
    assert [x["question"] for x in merged["questions"]] == ["Que transmet le neurone ?", "Grâce à quoi le neurone transmet-il ?"]
    assert merged["stats"] == {"1": {"right": 0, "wrong": 2, "last": False, "date": "2026-09-02"}}  # suivi reporté
    assert merged["attempts"] and store.merge_chapter_quizzes() == 0
    # Ajout direct : la question semblable est écartée
    quiz, added = store.add_to_quiz(first["id"], [q("Que transmet le neurone donc ?", "L'influx nerveux"), q("Nouveau ?", "Z")])
    assert added == 1 and len(quiz["questions"]) == 3


def test_second_quiz_on_a_chapter_feeds_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    monkeypatch.setattr(main, "jobs", main.Jobs())
    calls = []

    async def fake_generate(course_text, options, model=None, on_progress=None):
        calls.append(options.avoid)
        series = [[("Quel signal le neurone fait-il circuler ?", "L'influx nerveux"),
                   ("Par quelle jonction passe le message ?", "La synapse chimique")],
                  [("Quelle cellule assure la transmission ?", "Le neurone"),
                   ("De quelle nature est la jonction décrite ?", "Chimique")]][len(calls) - 1]
        return [{"title": "x", "questions": [q(*series[i]) for i in range(options.num_questions)]}]

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    with TestClient(main.app) as client:
        cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
        client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", COURSE.encode(), "text/plain"))])
        fid = client.get(f"/api/courses/{cid}").json()["files"][0]["id"]

        def make():
            client.post(f"/api/courses/{cid}/quizzes/background", data={
                "provider": "local", "model": "m", "num_questions": "2", "types": "qcm", "chapters": f"{fid}-0"})
            for _ in range(100):
                listed = client.get("/api/jobs").json()
                if all(j["status"] in {"done", "error"} for j in listed):
                    return listed
                time.sleep(0.05)

        make()
        listed = make()
        quizzes = client.get(f"/api/courses/{cid}").json()["quizzes"]
        assert len(quizzes) == 1 and quizzes[0]["count"] == 4, (quizzes, listed)
        assert listed[-1]["message"] == "2 questions ajoutées au quiz du chapitre"
        assert "Quel signal le neurone fait-il circuler ?" in calls[1]  # l'IA a reçu les questions existantes à ne pas reposer
