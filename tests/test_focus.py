import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.focus import focus_text, parse_manual_lines
from app.grounding import Grounding
from app.providers import ollama_provider
from app.quiz import BLANK, QuizOptions, assemble_quiz, build_user_prompt, giveaway, normalize_question
from app.storage import Store

FILLER = "La mémoire de travail garde les informations quelques secondes pour raisonner. " * 3
COURSE = "\n\n".join([
    "=== neuro.pages ===\nIntroduction à la neuropsychologie.",
    FILLER,
    "Le système nerveux central comprend l'encéphale et la moelle épinière.",
    "Le système nerveux périphérique relie les organes au système nerveux central.",
    FILLER.replace("mémoire", "attention"),
    FILLER.replace("mémoire", "perception"),
    FILLER.replace("mémoire", "motricité"),
    FILLER.replace("mémoire", "langage"),
])


def test_focus_keeps_only_passages_on_the_theme():
    text, hits, total = focus_text(COURSE, "les systèmes nerveux et leurs fonctions")
    assert hits == 2 and total == 8
    assert "encéphale" in text and "périphérique" in text and "langage" not in text
    assert text.startswith("=== neuro.pages ===")
    # Rien sur le thème : on garde tout le cours (l'IA reçoit quand même la consigne).
    assert focus_text(COURSE, "la photosynthèse")[0] == COURSE


def test_manual_lines():
    assert parse_manual_lines("1. Qu'est-ce que le SNC ?\n\n- Rôle de la moelle ?\nok\n• Et le SNP ?") == [
        "Qu'est-ce que le SNC ?", "Rôle de la moelle ?", "Et le SNP ?"]


def test_cloze_questions():
    raw = {"type": "texte_a_trous", "question": "Le système nerveux central comprend l'encéphale et la ________.",
           "choices": ["x"], "answer": "moelle épinière", "explanation": "",
           "source": "Le système nerveux central comprend l'encéphale et la moelle épinière."}
    q = normalize_question(raw, ["texte_a_trous"])
    assert q["question"] == f"Le système nerveux central comprend l'encéphale et la {BLANK}." and q["choices"] == []
    assert not giveaway(q)  # un trou juste après « la » : normal pour un texte à trous
    assert Grounding(COURSE).check_question(q)
    assert giveaway(normalize_question(raw | {"answer": "encéphale"}, ["texte_a_trous"]))  # déjà dans la phrase
    assert normalize_question(raw | {"question": "Sans trou."}, ["texte_a_trous"]) is None
    assert normalize_question(raw | {"question": "___ et ___"}, ["texte_a_trous"]) is None
    assert assemble_quiz([{"questions": [raw]}], QuizOptions(num_questions=1, types=["texte_a_trous"]), "T")["questions"]


def test_focus_in_prompt():
    prompt = build_user_prompt("cours", 5, QuizOptions(focus="le système nerveux"))
    assert "Thème imposé" in prompt and "« le système nerveux »" in prompt
    assert "Thème imposé" not in build_user_prompt("cours", 5, QuizOptions())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    return TestClient(main.app)


def test_quiz_with_my_questions_and_a_theme(client, monkeypatch):
    asked, seen = [], []

    async def fake_ask(system, schema, prompt, model=None):
        asked.append(prompt)
        listed = [line.split(". ", 1) for line in prompt.split("langue : français) :\n")[1].splitlines()]
        known = {"number": 0, "found": True, "type": "qcm", "kind": "cours",
                 "choices": ["L'encéphale et la moelle épinière", "Les nerfs", "Les ganglions", "Les muscles"],
                 "answer": "L'encéphale et la moelle épinière", "explanation": "Cf. cours.",
                 "source": "Le système nerveux central comprend l'encéphale et la moelle épinière."}
        unknown = {"number": 0, "found": False, "type": "qcm", "kind": "cours", "choices": [], "answer": "",
                   "explanation": "", "source": ""}
        return {"questions": [(known if "SNC" in text else unknown) | {"number": int(n)} for n, text in listed]}

    async def fake_generate(course_text, options, model=None, on_progress=None):
        seen.append((course_text, options))
        return [{"title": "Neuro", "questions": [
            {"type": "qcm", "kind": "cours", "question": f"Que relie le système nerveux périphérique ({i}) ?",
             "choices": ["Les organes au système nerveux central", "Deux neurones", "Les os", "Rien"],
             "answer": "Les organes au système nerveux central", "explanation": "",
             "source": "Le système nerveux périphérique relie les organes au système nerveux central."}
            for i in range(options.num_questions)]}]

    monkeypatch.setattr(ollama_provider, "ask", fake_ask)
    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", COURSE.encode(), "text/plain"))])
    response = client.post(f"/api/courses/{cid}/quizzes", data={
        "provider": "local", "model": "m", "num_questions": "3", "types": "qcm,vrai_faux",
        "focus": "les systèmes nerveux", "manual": "Qu'est-ce que le SNC ?\nQui a découvert le neurone ?"})
    events = [json.loads(line) for line in response.text.splitlines()]
    quiz = events[-1]["result"]
    assert [q.get("manual", False) for q in quiz["questions"]] == [True, False, False]
    assert quiz["questions"][0]["question"] == "Qu'est-ce que le SNC ?"
    assert quiz["dropped"] == ["Qui a découvert le neurone ?"]
    assert quiz["title"] == "Les systèmes nerveux" and quiz["focus"] == "les systèmes nerveux"
    text, options = seen[0]
    assert options.num_questions == 2 and "langage" not in text and options.focus == "les systèmes nerveux"
    assert "Qu'est-ce que le SNC ?" in options.avoid
    assert '1. Qu\'est-ce que le SNC ?' in asked[0] and 'type demandé : "qcm"' in asked[0]
    assert any("passages du cours" in e.get("message", "") for e in events)

    # Seulement mes questions, toutes introuvables : erreur claire
    response = client.post(f"/api/courses/{cid}/quizzes", data={
        "provider": "local", "model": "m", "num_questions": "1", "manual": "Qui a découvert le neurone ?"})
    last = json.loads(response.text.splitlines()[-1])
    assert last["type"] == "error" and "Aucune de tes questions" in last["message"]
