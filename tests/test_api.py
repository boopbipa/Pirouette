import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.providers import ollama_provider
from app.providers.base import ProviderError
from app.storage import Store

QUIZ = {"title": "Biologie", "questions": [
    {"type": "qcm", "question": "Où a lieu la photosynthèse ?",
     "choices": ["Chloroplaste", "Noyau", "Ribosome", "Mitochondrie"],
     "answer": "Chloroplaste", "explanation": "Cf. cours.",
     "key_terms": [{"term": "Chloroplaste", "definition": "Organite de la cellule végétale."}]},
    {"type": "vrai_faux", "question": "Elle produit de l'O2.", "choices": ["Vrai", "Faux"],
     "answer": "Vrai", "explanation": ""},
]}


def grounded(parts, course_text):
    """Ce que rendrait un modèle qui cite son cours : chaque question ou carte a pour source le cours lui-même."""
    return [{**p, **{k: [{**item, "source": course_text} for item in p[k]] for k in ("questions", "cards") if k in p}}
            for p in parts]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    return TestClient(main.app)


def upload(client, course_id, name, text):
    return client.post(f"/api/courses/{course_id}/files", files=[("files", (name, text.encode(), "text/plain"))])


def generate(client, course_id, **data):
    response = client.post(f"/api/courses/{course_id}/quizzes", data={"provider": "local", "model": "test", **data})
    return [json.loads(line) for line in response.text.splitlines()]


def test_course_lifecycle(client, monkeypatch):
    seen = []

    # 2e quiz : d'autres questions (celles du 1er quiz seraient écartées comme déjà posées)
    QUIZ_2 = {"title": "Biologie", "questions": [
        {"type": "qcm", "question": "Que contient le chloroplaste ?", "choices": ["Du stroma", "Un noyau", "Des ribosomes", "De l'ADN"],
         "answer": "Du stroma", "explanation": "", "key_terms": []},
        {"type": "vrai_faux", "question": "Le stroma est un liquide.", "choices": ["Vrai", "Faux"], "answer": "Vrai",
         "explanation": ""},
    ]}

    async def fake_generate(course_text, options, model=None, on_progress=None):
        seen.append(course_text)
        await on_progress("partie 1/1")
        return grounded([QUIZ if len(seen) == 1 else QUIZ_2], course_text)

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)

    course = client.post("/api/courses", json={"name": "Bio ch.4"}).json()
    cid = course["id"]
    assert client.get("/api/courses").json()[0]["name"] == "Bio ch.4"

    # Pas de fichier : génération refusée
    assert client.post(f"/api/courses/{cid}/quizzes", data={"provider": "local"}).status_code == 400

    assert upload(client, cid, "bio.txt", "Version 1 : chloroplaste.").json()["results"] == {"bio.txt": "added"}
    quiz1 = generate(client, cid, num_questions="2")[-1]["result"]
    assert quiz1["course_id"] == cid and quiz1["course_version"] == 1
    assert quiz1["questions"][0]["key_terms"][0]["term"] == "Chloroplaste"
    assert quiz1["questions"][1]["key_terms"] == []

    # Nouvelle version du même fichier : remplace l'ancien, sans doublon
    result = upload(client, cid, "bio.txt", "Version 2 : chloroplaste et stroma.").json()
    assert result["results"] == {"bio.txt": "updated"}
    assert len(result["course"]["files"]) == 1 and result["course"]["files"][0]["revisions"] == 2
    quiz2 = generate(client, cid, num_questions="2")[-1]["result"]
    assert "Version 2" in seen[-1] and "Version 1" not in seen[-1]

    detail = client.get(f"/api/courses/{cid}").json()
    assert detail["version"] == 2
    assert {q["id"] for q in detail["quizzes"]} == {quiz1["id"], quiz2["id"]}

    # Scores
    client.post(f"/api/quizzes/{quiz1['id']}/attempts", json={"score": 1, "total": 2})
    client.post(f"/api/quizzes/{quiz1['id']}/attempts", json={"score": 2, "total": 2})
    summary = next(q for q in client.get(f"/api/courses/{cid}").json()["quizzes"] if q["id"] == quiz1["id"])
    assert summary["attempts"] == 2 and summary["best_score"]["score"] == 2 and summary["last_score"]["score"] == 2

    # Suppression d'un quiz puis du cours (qui supprime ses quiz)
    assert client.delete(f"/api/quizzes/{quiz1['id']}").status_code == 200
    assert len(client.get(f"/api/courses/{cid}").json()["quizzes"]) == 1
    assert client.delete(f"/api/courses/{cid}").status_code == 200
    assert client.get(f"/api/courses/{cid}").status_code == 404
    assert client.get(f"/api/quizzes/{quiz2['id']}").status_code == 404


def test_rename_and_remove_file(client):
    cid = client.post("/api/courses", json={"name": "Histoire"}).json()["id"]
    assert client.patch(f"/api/courses/{cid}", json={"name": "Histoire moderne"}).json()["name"] == "Histoire moderne"
    course = upload(client, cid, "a.md", "texte").json()["course"]
    fid = course["files"][0]["id"]
    assert client.delete(f"/api/courses/{cid}/files/{fid}").json()["files"] == []


def test_rejects_unsupported_file(client):
    cid = client.post("/api/courses", json={"name": "X"}).json()["id"]
    response = client.post(f"/api/courses/{cid}/files", files=[("files", ("photo.png", b"x", "image/png"))])
    assert response.status_code == 400
    assert client.get(f"/api/courses/{cid}").json()["files"] == []


def test_provider_error_is_streamed(client, monkeypatch):
    async def failing(*args, **kwargs):
        raise ProviderError("Ollama est éteint")

    monkeypatch.setattr(ollama_provider, "generate", failing)
    cid = client.post("/api/courses", json={"name": "X"}).json()["id"]
    upload(client, cid, "a.txt", "Le chloroplaste : question, réponse, notion, du texte.")
    assert generate(client, cid)[-1] == {"type": "error", "message": "Ollama est éteint"}


def test_old_quizzes_without_course_are_listed(client):
    main.store.save_quiz({"title": "Ancien", "questions": []})
    orphans = client.get("/api/quizzes?orphans=true").json()
    assert [q["title"] for q in orphans] == ["Ancien"]


def test_cards_are_added_to_the_deck_without_duplicates(client, monkeypatch):
    calls = []

    async def fake_cards(course_text, n_cards, language, model=None, on_progress=None, avoid=(), **kwargs):
        calls.append((n_cards, list(avoid)))
        start = len(calls) * 100
        # Reformulations d'une même notion, carte vide, et une carte déjà dans le paquet
        return grounded([{"cards": [{"front": f"Question {start + i} ?", "back": f"Réponse {start + i}"} for i in range(n_cards)]
                          + [{"front": "Définis la notion 0", "back": "x"}, {"front": "", "back": "vide"}]}], course_text)

    monkeypatch.setattr(ollama_provider, "generate_cards", fake_cards)
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    upload(client, cid, "a.txt", "Le chloroplaste : question, réponse, notion, du texte.")
    assert client.get(f"/api/courses/{cid}/cards").json()["cards"] == []

    def add(count):
        events = [json.loads(line) for line in client.post(
            f"/api/courses/{cid}/cards", data={"provider": "local", "count": str(count)}).text.splitlines()]
        return events[-1]["result"]

    deck = add(3)
    assert [c["front"] for c in deck["cards"]] == ["Question 100 ?", "Question 101 ?", "Question 102 ?"]
    assert deck["added"] == 3 and all(c["status"] == "new" for c in deck["cards"])

    deck = add(2)  # s'ajoute au paquet, et l'IA connaît les cartes existantes
    assert len(deck["cards"]) == 5 and deck["added"] == 2
    assert calls[-1] == (2, ["Question 100 ?", "Question 101 ?", "Question 102 ?"])

    first, second = deck["cards"][0]["id"], deck["cards"][1]["id"]
    client.post(f"/api/courses/{cid}/cards/{first}/review", json={"known": True})
    client.post(f"/api/courses/{cid}/cards/{second}/review", json={"known": False})
    assert client.get(f"/api/courses/{cid}").json()["cards"] == {"total": 5, "known": 1, "review": 4}
    assert client.post(f"/api/courses/{cid}/cards/deadbeef/review", json={"known": True}).status_code == 404

    client.delete(f"/api/courses/{cid}/cards/{second}")
    assert client.get(f"/api/courses/{cid}").json()["cards"]["total"] == 4
    client.delete(f"/api/courses/{cid}/cards")
    assert client.get(f"/api/courses/{cid}").json()["cards"]["total"] == 0


def test_missing_cards_are_requested_again(client, monkeypatch):
    calls = []

    async def fake_cards(course_text, n_cards, language, model=None, on_progress=None, avoid=(), **kwargs):
        calls.append(n_cards)
        start = len(calls) * 100
        # L'IA en rend toujours 3 de moins que demandé, dont deux fois la même notion
        cards = [{"front": f"Notion {start + i} ?", "back": f"R {start + i}"} for i in range(max(1, n_cards - 3))]
        return grounded([{"cards": cards + [{"front": f"Qu'est-ce que la notion {start} ?", "back": "autre"}]}], course_text)

    monkeypatch.setattr(ollama_provider, "generate_cards", fake_cards)
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    upload(client, cid, "a.txt", "Le chloroplaste : question, réponse, notion, du texte.")
    events = [json.loads(line) for line in client.post(
        f"/api/courses/{cid}/cards", data={"provider": "local", "count": "20"}).text.splitlines()]
    deck = events[-1]["result"]
    # 17 + 1 + 1 + 1 : jamais plus que demandé, sans doublon, et au plus trois relances
    assert calls == [20, 3, 2, 1] and len(deck["cards"]) == 20


def test_profile_and_stats(client, monkeypatch):
    assert client.get("/api/profile").json() == {"name": "", "asked": False}
    assert client.put("/api/profile", json={"name": "  Pierre-Louis "}).json() == {"name": "Pierre-Louis", "asked": True}
    assert client.get("/api/profile").json()["name"] == "Pierre-Louis"

    async def fake_generate(course_text, *args, **kwargs):
        return grounded([QUIZ], course_text)

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    upload(client, cid, "a.txt", "Le chloroplaste : question, réponse, notion, du texte.")
    quiz = generate(client, cid)[-1]["result"]
    client.post(f"/api/quizzes/{quiz['id']}/attempts", json={"score": 1, "total": 2})
    main.store.save_doc(cid, "cards", {"cards": [{"id": "a1b2c3d4", "front": "Q", "back": "R", "status": "known",
                                                   "reviews": 1, "last_reviewed": None}]})
    stats = client.get("/api/stats").json()
    assert stats | {"streak": 0} == {"courses": 1, "quizzes": 1, "quizzes_done": 1, "decks": 1, "cards_known": 1,
                                     "cards_review": 0, "streak": 0, "week_success": 50, "cards_today": 0,
                                     "next_exam": None}
    assert stats["streak"] == 1  # le quiz fait aujourd'hui compte


def test_manifest_served_at_root(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200 and response.json()["name"] == "Pirouette"


def test_settings_api_key_saved_privately(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    view = client.get("/api/settings").json()
    assert view["claude"]["configured"] is False and view["desktop"] is False

    assert client.put("/api/settings", json={"api_key": "pas-une-cle"}).status_code == 400
    view = client.put("/api/settings", json={"api_key": " sk-ant-test-1234567890abcd ", "name": "Pilou"}).json()
    assert view["claude"] == view["claude"] | {"configured": True, "saved_in_app": True, "hint": "sk-ant-…abcd"}
    assert view["name"] == "Pilou"
    settings_file = main.store.root / "settings.json"
    assert oct(settings_file.stat().st_mode & 0o777) == "0o600"
    assert "sk-ant-test" not in json.dumps(view)  # la clé complète n'est jamais renvoyée

    view = client.put("/api/settings", json={"api_key": ""}).json()
    assert view["claude"]["configured"] is False and main.store.get_settings() == {}


def test_test_claude_without_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    response = client.post("/api/settings/test-claude")
    assert response.status_code == 400 and "Aucune clé" in response.json()["detail"]


def test_appearance_saved_and_injected(client):
    palette = {"accent": "#2f4be0", "ink": "#2F4BE0", "soft": "#E2E3F1",
               "d_accent": "#4059E2", "d_ink": "#7A8CEB", "d_soft": "#1C2241"}
    assert "--accent:" not in client.get("/").text.split("appearance-style")[1][:40]
    result = client.put("/api/appearance", json={"palette": palette}).json()
    assert result["appearance"]["accent"] == "#2F4BE0"
    page = client.get("/").text
    assert "--accent:#2F4BE0" in page and "--accent-ink:#7A8CEB" in page
    assert client.get("/api/settings").json()["appearance"]["soft"] == "#E2E3F1"

    assert client.put("/api/appearance", json={"palette": palette | {"ink": "red; } body {"}}).status_code == 400
    assert client.put("/api/appearance", json={"palette": {"accent": "#000000"}}).status_code == 400

    assert client.put("/api/appearance", json={"palette": None}).json()["css"] == ""
    assert "--accent:#2F4BE0" not in client.get("/").text


def test_pages_file_without_pages_app_gives_help(client, monkeypatch):
    # sur ce serveur de test (Linux), il n'y a pas d'app Pages
    cid = client.post("/api/courses", json={"name": "Neurosciences"}).json()["id"]
    response = client.post(f"/api/courses/{cid}/files", files=[("files", ("cours.pages", b"...", "application/octet-stream"))])
    assert response.status_code == 400
    assert "Exporter vers" in response.json()["detail"]


def test_missing_questions_are_requested_again(client, monkeypatch):
    calls = []

    def qcm(i):
        return {"type": "qcm", "question": f"Question {i} ?", "choices": ["A", "B", "C", "D"], "answer": "A",
                "explanation": "", "key_terms": []}

    async def fake_generate(course_text, options, model=None, on_progress=None):
        calls.append((options.num_questions, list(options.avoid)))
        start = sum(n for n, _ in calls[:-1])
        # Le modèle n'en rend que 6 par demande, dont une invalide (réponse absente des choix)
        bad = {**qcm(999), "answer": "Z"}
        return grounded([{"title": "Bio", "questions": [qcm(start + i) for i in range(min(5, options.num_questions))] + [bad]}],
                        course_text)

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    upload(client, cid, "bio.txt", "Le chloroplaste.")
    events = generate(client, cid, num_questions="12", types="qcm")
    quiz = events[-1]["result"]
    assert len(quiz["questions"]) == 12
    assert [n for n, _ in calls] == [12, 7, 2]
    assert len(calls[1][1]) == 5 and "Question 0 ?" in calls[1][1]
    assert any("en demande 7 de plus" in e.get("message", "") for e in events)


def test_streak_and_week_success(tmp_path):
    from datetime import date

    store = Store(tmp_path)
    cid = store.create_course("Bio")["id"]
    store.save_doc(cid, "cards", {"cards": [
        {"id": "a1", "front": "Q", "back": "R", "status": "known", "reviews": 1, "last_reviewed": "2026-09-25T10:00:00"},
        {"id": "a2", "front": "Q2", "back": "R2", "status": "review", "reviews": 1, "last_reviewed": "2026-09-24T22:00:00"},
        {"id": "a3", "front": "Q3", "back": "R3", "status": "new", "reviews": 0, "last_reviewed": None},
    ]})
    quiz = store.save_quiz({"title": "Q", "questions": [], "course_id": cid})
    for day, score in (("2026-09-22", 2), ("2026-09-10", 0)):
        data = store.get_quiz(quiz["id"])
        data["attempts"].append({"date": f"{day}T09:00:00", "score": score, "total": 4})
        store.save_quiz(data)

    # Pas encore révisé aujourd'hui (26) : la série s'arrête hier (25, 24) ; le 22 est trop loin.
    stats = store.stats(today=date(2026, 9, 26))
    assert stats["streak"] == 2 and stats["cards_review"] == 2 and stats["week_success"] == 50
    assert store.stats(today=date(2026, 9, 28))["streak"] == 0
    assert [g["course_name"] for g in store.review_cards()] == ["Bio"]
    assert [c["id"] for c in store.review_cards()[0]["cards"]] == ["a2", "a3"]


def test_review_across_courses(client):
    client.post("/api/courses", json={"name": "A"})
    assert client.get("/api/review").json() == []
    assert client.get("/api/courses").json()[0]["card_count"] == 0


def test_rename_quiz_and_report_a_question(client, monkeypatch):
    async def fake_generate(course_text, options, model=None, on_progress=None):
        return grounded([QUIZ], course_text)

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    upload(client, cid, "a.txt", "Le chloroplaste : question, réponse, notion, du texte.")
    quiz = generate(client, cid)[-1]["result"]

    assert client.patch(f"/api/quizzes/{quiz['id']}", json={"title": "  Photosynthèse  "}).json()["title"] == "Photosynthèse"
    assert client.get(f"/api/courses/{cid}").json()["quizzes"][0]["title"] == "Photosynthèse"
    assert client.patch(f"/api/quizzes/{quiz['id']}", json={"title": " "}).status_code == 400

    body = {"quiz_id": quiz["id"], "index": 0, "given": "Noyau", "message": "La réponse est discutable."}
    result = client.post("/api/feedback", json=body).json()
    assert result["to"] == "gonnetpierrelouis@gmail.com" and result["opened"] is False
    assert result["mailto"].startswith("mailto:gonnetpierrelouis@gmail.com?subject=")
    text = result["body"]
    for expected in ("Où a lieu la photosynthèse ?", "✓ Chloroplaste", "• Noyau", "Bonne réponse selon le quiz : Chloroplaste",
                     "Réponse donnée : Noyau", "La réponse est discutable.", "Cours : Bio"):
        assert expected in text, expected
    saved = json.loads((main.store.root / "feedback.json").read_text())
    assert saved[0]["message"] == "La réponse est discutable." and saved[0]["question"]["answer"] == "Chloroplaste"
    assert client.post("/api/feedback", json=body | {"index": 9}).status_code == 400


def test_new_quiz_knows_the_questions_of_previous_quizzes(client, monkeypatch):
    avoids = []

    async def fake_generate(course_text, options, model=None, on_progress=None):
        avoids.append(list(options.avoid))
        return grounded([QUIZ], course_text)

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    upload(client, cid, "a.txt", "Le chloroplaste : question, réponse, notion, du texte.")
    first = generate(client, cid, num_questions="2")[-1]["result"]
    assert len(first["questions"]) == 2 and avoids[0] == []

    # 2e quiz : l'IA reçoit les questions du 1er, et les mêmes questions ne sont pas reprises
    events = generate(client, cid, num_questions="2")
    assert avoids[1] == [q["question"] for q in first["questions"]]
    assert events[-1]["type"] == "error"  # le faux modèle ne sait proposer que les mêmes questions


def test_missing_course_questions_are_requested_specifically(client, monkeypatch):
    shares = []

    def q(i, kind):
        return {"type": "qcm", "kind": kind, "question": f"Question {i} sur la notion {i} ?", "answer": "A",
                "choices": ["A", "B", "C", "D"], "explanation": "", "key_terms": []}

    async def fake_generate(course_text, options, model=None, on_progress=None):
        shares.append((options.num_questions, options.course_share))
        if len(shares) == 1:  # 1re réponse : que de la réflexion
            return grounded([{"questions": [q(i, "reflexion") for i in range(4)]}], course_text)
        return grounded([{"questions": [q(100 + i, "cours") for i in range(options.num_questions)]}], course_text)

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    upload(client, cid, "a.txt", "Le chloroplaste : question, réponse, notion, du texte.")
    quiz = generate(client, cid, num_questions="4", types="qcm", course_share="beaucoup")[-1]["result"]
    assert shares == [(4, 0.7), (3, 1.0)]  # 70 % de 4 → 3 questions de cours, redemandées précisément
    assert [x["kind"] for x in quiz["questions"]].count("cours") == 3 and len(quiz["questions"]) == 4


def test_folders_group_courses_by_semester(client):
    bio = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    neuro = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    assert client.post("/api/folders", json={"name": "  "}).status_code == 400
    s1 = client.post("/api/folders", json={"name": "Semestre 1"}).json()
    assert s1["archived"] is False
    client.put(f"/api/courses/{bio}/folder", json={"folder_id": s1["id"]})
    assert client.put(f"/api/courses/{neuro}/folder", json={"folder_id": "inconnu"}).status_code == 404
    folders = {c["id"]: c.get("folder_id") for c in client.get("/api/courses").json()}
    assert folders == {bio: s1["id"], neuro: None}

    archived = client.patch(f"/api/folders/{s1['id']}", json={"archived": True, "name": "S1 (2025)"}).json()
    assert archived["archived"] and archived["name"] == "S1 (2025)"
    assert client.get("/api/folders").json() == [archived]

    # Supprimer le dossier garde ses cours : ils reviennent dans « Mes cours ».
    client.delete(f"/api/folders/{s1['id']}")
    assert client.get("/api/folders").json() == []
    assert client.get(f"/api/courses/{bio}").json()["folder_id"] is None


def test_cards_edited_and_written_by_hand(client):
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    assert client.post(f"/api/courses/{cid}/cards/manual", json={"front": "", "back": "x"}).status_code == 400
    added = client.post(f"/api/courses/{cid}/cards/manual",
                        json={"front": "Qu'est-ce qu'un neurone ?", "back": "La cellule du système nerveux."}).json()
    card = added["card"]
    assert added["similar"] is None and card["manual"] and card["status"] == "new"
    again = client.post(f"/api/courses/{cid}/cards/manual",
                        json={"front": "Qu'est-ce qu'un neurone ?", "back": "Cellule nerveuse."}).json()
    assert again["similar"]["front"] == "Qu'est-ce qu'un neurone ?"  # signalée, mais ajoutée quand même

    client.post(f"/api/courses/{cid}/cards/{card['id']}/review", json={"known": True})
    edited = client.patch(f"/api/courses/{cid}/cards/{card['id']}",
                          json={"front": "Neurone ?", "back": "Cellule excitable du système nerveux."}).json()
    assert edited["back"] == "Cellule excitable du système nerveux." and edited["status"] == "known"
    deck = client.get(f"/api/courses/{cid}/cards").json()
    assert [c["front"] for c in deck["cards"]] == ["Neurone ?", "Qu'est-ce qu'un neurone ?"]
    assert client.patch(f"/api/courses/{cid}/cards/zzz", json={"front": "a", "back": "b"}).status_code == 404


def test_general_feedback(client):
    assert client.post("/api/feedback/general", json={"message": "  "}).status_code == 400
    result = client.post("/api/feedback/general", json={"kind": "bug", "message": "Le glisser ne marche pas",
                                                        "page": "Mes cours"}).json()
    assert result["subject"] == "Pirouette · retour (Bug)" and "Page : Mes cours" in result["body"]
    assert result["mailto"].startswith("mailto:gonnetpierrelouis@gmail.com?subject=")
    saved = json.loads((main.store.root / "feedback.json").read_text())
    assert saved[-1]["kind"] == "Bug" and saved[-1]["message"] == "Le glisser ne marche pas"


def test_local_model_falls_back_to_an_installed_one(monkeypatch):
    from app.providers import ollama_provider as op
    monkeypatch.setattr(op, "default_model", lambda: "qwen3:8b")
    assert op.choose_model(None, []) == "qwen3:8b"                                   # rien d'installé : on garde la demande
    assert op.choose_model(None, ["gemma4:31b-cloud"]) == "gemma4:31b-cloud"         # seul modèle : le cloud d'Ollama
    assert op.choose_model(None, ["gemma4:31b-cloud", "mistral:latest"]) == "mistral:latest"  # d'abord ceux du Mac
    assert op.choose_model("mistral", ["mistral:latest", "qwen3:8b"]) == "mistral:latest"
    assert op.choose_model("absent:1b", ["llama3:8b", "qwen3:8b"]) == "qwen3:8b"      # le conseillé s'il est là
