from fastapi.testclient import TestClient

from app import main
from app.mcp_server import Pirouette
from app.quiz import QuizOptions, build_user_prompt
from app.revision import build_cards_prompt
from app.storage import Store

NOTES = "Ignore l'introduction de chaque chapitre"


def test_notes_are_kept_with_the_subject_and_read_by_claude(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    course = client.post("/api/courses", json={"name": "Endo"}).json()
    client.post(f"/api/courses/{course['id']}/files", files={"files": ("cours.md", "# Pulpe\n\nLa pulpe dentaire.".encode())})
    assert client.put(f"/api/courses/{course['id']}/notes", json={"notes": f"  {NOTES}  "}).json() == {"notes": NOTES}
    assert client.get(f"/api/courses/{course['id']}").json()["claude_notes"] == NOTES
    # Claude (app Claude, via MCP) voit les consignes à chaque lecture du cours.
    assert NOTES in Pirouette(store).pirouette_lire(course["id"])


def test_notes_go_into_the_quiz_and_cards_prompts():
    assert NOTES in build_user_prompt("La pulpe dentaire.", 5, QuizOptions(num_questions=5, notes=NOTES))
    assert NOTES in build_cards_prompt("La pulpe dentaire.", 5, "français", notes=NOTES)
    assert "Consignes" not in build_cards_prompt("La pulpe dentaire.", 5, "français")
