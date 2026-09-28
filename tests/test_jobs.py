import time

from fastapi.testclient import TestClient

from app import main
from app.providers import ollama_provider
from app.storage import Store

FILLER = "Le neurone transmet l'influx nerveux grâce à la synapse chimique. " * 12
ANSWERS = [["La synapse chimique", "Le neurone"], ["L'influx nerveux", "Grâce à la synapse"], ["Transmet l'influx", "Chimique"]]
COURSE = f"Chapitre 1 : Le neurone\n{FILLER}\nChapitre 2 : La synapse\n{FILLER}\nChapitre 3 : Le cortex\n{FILLER}"


def test_one_quiz_per_chapter_in_the_background(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    monkeypatch.setattr(main, "jobs", main.Jobs())
    seen = []

    async def fake_generate(course_text, options, model=None, on_progress=None):
        seen.append(course_text)
        await on_progress("Partie 1/1 du cours…")
        return [{"title": "Titre de l'IA", "questions": [
            {"type": "qcm", "kind": "cours", "question": f"Sujetn{len(seen)}x{i} : que dit le cours sur la **synapse** ?",
             "choices": [ANSWERS[len(seen) % 3][i % 2], "Un os", "Un muscle", "Rien"], "answer": ANSWERS[len(seen) % 3][i % 2],
             "explanation": "", "source": "Le neurone transmet l'influx nerveux grâce à la synapse chimique."}
            for i in range(options.num_questions)]}]

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    with TestClient(main.app) as client:
        cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
        client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", COURSE.encode(), "text/plain"))])
        fid = client.get(f"/api/courses/{cid}").json()["files"][0]["id"]
        created = client.post(f"/api/courses/{cid}/quizzes/background", data={
            "provider": "local", "model": "m", "num_questions": "2", "types": "qcm", "per_chapter": "1",
            "chapters": f"{fid}-0,{fid}-2"}).json()["jobs"]
        assert [j["label"] for j in created] == ["Chapitre 1 : Le neurone", "Chapitre 3 : Le cortex"]
        for _ in range(100):
            listed = client.get("/api/jobs").json()
            if all(j["status"] in {"done", "error"} for j in listed):
                break
            time.sleep(0.05)
        assert [j["status"] for j in listed] == ["done", "done"], listed
        titles = [q["title"] for q in client.get(f"/api/courses/{cid}").json()["quizzes"]]
        assert sorted(titles) == ["Chapitre 1 : Le neurone", "Chapitre 3 : Le cortex"]
        assert "Le cortex" in seen[1] and "La synapse\n" not in seen[1]
        quiz = client.get(f"/api/quizzes/{listed[0]['result']['quiz_id']}").json()
        assert quiz["questions"][0]["question"].endswith("sur la synapse ?")  # sans les ** du cours
        client.delete(f"/api/jobs/{listed[0]['id']}")
        assert len(client.get("/api/jobs").json()) == 1
        # Un seul quiz (sans « per_chapter ») : un seul travail
        single = client.post(f"/api/courses/{cid}/quizzes/background", data={"provider": "local", "model": "m",
                                                                           "num_questions": "1", "types": "qcm"}).json()
        assert len(single["jobs"]) == 1 and single["jobs"][0]["label"] == "Nouveau quiz"
