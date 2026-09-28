import json

from fastapi.testclient import TestClient

from app import main
from app.grounding import Grounding
from app.providers import ollama_provider
from app.quiz import QuizOptions, assemble_quiz
from app.storage import Store

COURSE = """Chapitre 2 : Stress et cognition
Le stress aigu augmente la vigilance et mobilise l'énergie de l'organisme.
Les glandes surrénales libèrent alors du cortisol, qui agit sur la mémoire.
Un stress chronique altère la concentration et le sommeil."""


def question(**fields):
    base = {"type": "qcm", "question": "Quelle hormone est libérée ?", "choices": ["Le cortisol", "La mélatonine"],
            "answer": "Le cortisol", "explanation": "",
            "source": "Les glandes surrénales libèrent alors du cortisol, qui agit sur la mémoire.", "key_terms": []}
    return base | fields


def test_quote_must_come_from_the_course():
    g = Grounding(COURSE)
    assert g.quote_found("Les glandes surrénales libèrent alors du cortisol, qui agit sur la mémoire.")
    # Ponctuation, majuscules, retour à la ligne ou petit oubli : toujours reconnue
    assert g.quote_found("les glandes surrenales liberent alors du cortisol qui agit sur la memoire")
    assert g.quote_found("Le stress aigu augmente la vigilance et mobilise l'énergie")
    assert not g.quote_found("L'axe hypothalamo-hypophysaire est activé par le stress aigu.")
    assert not g.quote_found("stress aigu")  # trop court pour prouver quelque chose


def test_answer_outside_the_course_is_rejected_even_with_a_real_quote():
    """Le cas vu en vrai : une phrase du cours sur le stress, mais une réponse que le cours ne contient pas."""
    g = Grounding(COURSE)
    assert g.check_question(question())
    assert not g.check_question(question(answer="L'activation de l'axe hypothalamo-hypophysaire (HPA)",
                                         source="Le stress aigu augmente la vigilance et mobilise l'énergie de l'organisme."))
    assert not g.check_question(question(source="Le cortisol est produit par le cortex surrénalien sous l'effet de l'ACTH."))
    assert g.check_question(question(type="vrai_faux", answer="Vrai", choices=["Vrai", "Faux"]))
    assert g.check_card({"front": "Effet du stress chronique ?", "back": "Il altère la concentration et le sommeil.",
                         "source": "Un stress chronique altère la concentration et le sommeil."})
    assert not g.check_card({"front": "Rôle de l'ACTH ?", "back": "L'ACTH stimule le cortex surrénalien.",
                             "source": "L'ACTH stimule le cortex surrénalien."})


def test_assemble_quiz_reports_rejected_questions():
    parts = [{"title": "Stress", "questions": [question(), question(question="Quel axe ?", answer="L'axe hypothalamo-hypophysaire",
                                                                     choices=["L'axe hypothalamo-hypophysaire", "Autre"])]}]
    quiz = assemble_quiz(parts, QuizOptions(types=["qcm"]), "Cours", Grounding(COURSE))
    assert [q["question"] for q in quiz["questions"]] == ["Quelle hormone est libérée ?"]
    assert quiz["questions"][0]["source"].startswith("Les glandes surrénales")
    assert quiz["rejected"] == ["Quel axe ?"]


def test_generation_replaces_questions_outside_the_course(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    calls = []

    async def fake_generate(course_text, options, model=None, on_progress=None):
        calls.append(list(options.avoid))
        if len(calls) == 1:  # 1re réponse : une question hors du cours
            return [{"title": "Stress", "questions": [question(question="Quel axe ?", answer="L'axe hypothalamo-hypophysaire",
                                                               choices=["L'axe hypothalamo-hypophysaire", "Autre"])]}]
        return [{"title": "Stress", "questions": [question()]}]

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.post(f"/api/courses/{cid}/files", files=[("files", ("cours.txt", COURSE.encode(), "text/plain"))])
    events = [json.loads(line) for line in client.post(
        f"/api/courses/{cid}/quizzes", data={"provider": "local", "num_questions": "1", "types": "qcm"}).text.splitlines()]
    quiz = events[-1]["result"]
    assert [q["question"] for q in quiz["questions"]] == ["Quelle hormone est libérée ?"]
    assert "rejected" not in quiz and calls[1] == ["Quel axe ?"]
    assert any("1 écartée : hors du cours, trop facile ou déjà posée" in e.get("message", "") for e in events)
