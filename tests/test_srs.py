from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import main, srs
from app.storage import Store

NOW = datetime(2026, 9, 27, 18, 0)


def card(**fields):
    return {"id": "c1", "front": "Q ?", "back": "R.", "status": "new", "reviews": 0, "last_reviewed": None} | fields


def test_intervals_grow_when_the_card_is_known():
    c = card()
    assert srs.is_due(c, NOW.date()) and srs.preview(c) == {"again": 0, "hard": 1, "good": 2, "easy": 4}
    srs.schedule(c, "good", NOW)
    assert c["status"] == "known" and c["due"] == "2026-09-29" and not srs.is_due(c, NOW.date())
    srs.schedule(c, "good", NOW)
    assert c["interval"] == 3
    srs.schedule(c, "good", NOW)
    assert c["interval"] == round(3 * 2.5) == 8
    srs.schedule(c, "easy", NOW)
    assert c["interval"] > 20 and c["ease"] == 2.65


def test_forgotten_card_comes_back_today_and_grows_slower():
    c = card(status="known", interval=10, ease=2.5, reviews=4, due="2026-09-27")
    srs.schedule(c, "again", NOW)
    assert c["status"] == "review" and c["due"] == "2026-09-27" and c["lapses"] == 1 and c["ease"] == 2.3
    assert srs.is_weak(c)
    srs.schedule(c, "good", NOW)
    assert c["interval"] == 2 and not srs.is_weak(c)


def test_cards_from_before_spaced_repetition():
    known = card(status="known", reviews=1, last_reviewed="2026-09-20T10:00:00")
    assert srs.due_date(known, NOW.date()) == date(2026, 9, 23) and srs.is_due(known, NOW.date())
    assert srs.preview(known)["good"] == 8  # interval 3 × 2,5
    review = card(status="review", reviews=1, last_reviewed="2026-09-26T10:00:00")
    assert srs.is_due(review, NOW.date()) and srs.is_weak(review)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    return TestClient(main.app)


def test_daily_session_mixes_due_cards_and_quiz_questions(client):
    store = main.store
    bio = store.create_course("Bio")["id"]
    old = store.create_course("Ancien semestre")["id"]
    cards = [card(id=f"n{i}", front=f"Q{i} ?") for i in range(25)]
    cards.append(card(id="late", status="known", reviews=3, interval=4, due="2026-01-01"))
    cards.append(card(id="later", status="known", reviews=3, interval=4, due="2099-01-01"))
    store.save_doc(bio, "cards", {"cards": cards})
    store.save_doc(old, "cards", {"cards": [card(id="o1")]})
    folder = store.create_folder("S0")
    store.move_course(old, folder["id"])
    store.update_folder(folder["id"], archived=True)
    questions = [{"type": "qcm", "question": f"Question {i} ?", "choices": ["a", "b"], "answer": "a"} for i in range(4)]
    quiz = store.save_quiz({"title": "Quiz", "questions": questions, "course_id": bio, "course_name": "Bio"})
    client.post(f"/api/quizzes/{quiz['id']}/answers", json={"answers": [{"index": 1, "correct": False},
                                                                        {"index": 2, "correct": True}]})

    today = client.get("/api/session?mode=today").json()
    kinds = [i["kind"] for i in today["items"]]
    # Le retard d'abord, puis 20 nouvelles cartes au plus ; pas l'ancien semestre archivé.
    assert today["cards"] == 21 and today["items"][0]["card"]["id"] == "late"
    assert all(i["course_id"] == bio for i in today["items"])
    # Questions : la ratée, puis celles jamais répondues (pas la réussie à l'instant)
    assert today["questions"] == 3 and kinds.count("question") == 3 and kinds[-1] == "card"
    assert {i["index"] for i in today["items"] if i["kind"] == "question"} == {0, 1, 3}

    # Nouvelles cartes vues aujourd'hui : le quota du jour baisse
    client.post(f"/api/courses/{bio}/cards/n0/review", json={"rating": "good"})
    assert client.get("/api/session?mode=today").json()["cards"] == 20
    # Un cours archivé reste révisable à la demande
    assert client.get(f"/api/session?mode=today&course={old}").json()["cards"] == 1

    client.post(f"/api/courses/{bio}/cards/n1/review", json={"rating": "again"})
    weak = client.get("/api/session?mode=weak").json()
    assert weak["cards"] == 1 and weak["questions"] == 1
    assert client.get(f"/api/courses/{bio}").json()["revision"]["weak"] == 2

    progress = client.get("/api/progress").json()
    assert progress["days"][-1]["cards"] == 2 and progress["days"][-1]["questions"] == 2
    assert progress["week"]["cards_success"] == 50 and progress["weak"] == {"cards": 1, "questions": 1}
    bio_row = next(c for c in progress["courses"] if c["id"] == bio)
    assert bio_row["questions_success"] == 50 and bio_row["cards"] == 27

    assert client.post(f"/api/courses/{bio}/cards/n2/review", json={"rating": "bof"}).status_code == 400
    assert client.get("/api/session?mode=cards&course=" + bio + "&filter=known").json()["cards"] == 3


def test_daily_reminder(client, monkeypatch, tmp_path):
    import plistlib

    from app import reminder

    assert client.put("/api/settings", json={"reminder_time": "19:00"}).status_code == 400  # hors de l'app Mac
    calls = []
    monkeypatch.setattr(reminder, "supported", lambda: True)
    monkeypatch.setattr(reminder, "install", lambda h, m: calls.append(("install", h, m)))
    monkeypatch.setattr(reminder, "uninstall", lambda: calls.append(("uninstall",)))
    assert client.put("/api/settings", json={"reminder_time": "25:00"}).status_code == 400
    assert client.put("/api/settings", json={"reminder_time": "19:05"}).json()["reminder_time"] == "19:05"
    assert client.put("/api/settings", json={"reminder_time": ""}).json()["reminder_time"] == ""
    assert calls == [("install", 19, 5), ("uninstall",)]
    assert client.put("/api/settings", json={"new_per_day": 10}).json()["new_per_day"] == 10

    data = plistlib.loads(reminder.plist(19, 5, ["/Applications/Pirouette.app/Contents/MacOS/Pirouette", "--remind"]))
    assert data["StartCalendarInterval"] == {"Hour": 19, "Minute": 5} and data["ProgramArguments"][-1] == "--remind"
    assert reminder.message(0) is None and reminder.message(1) == "1 carte t'attend aujourd'hui. On révise ?"
    assert reminder._quote('Dis "bonjour"') == '"Dis \\"bonjour\\""'


def test_partiel_and_question_removal(client, monkeypatch):
    from app.providers import ollama_provider

    store = main.store
    cid = store.create_course("Neuro")["id"]
    make = lambda i: {"type": "qcm", "question": f"Q{i} ?", "choices": ["a", "b"], "answer": "a"}  # noqa: E731
    q1 = store.save_quiz({"title": "A", "questions": [make(i) for i in range(5)], "course_id": cid})
    q2 = store.save_quiz({"title": "B", "questions": [make(10 + i) for i in range(2)], "course_id": cid})
    store.save_doc(cid, "cards", {"cards": [card(id=f"c{i}") for i in range(4)]})
    data = client.get(f"/api/courses/{cid}/partiel?questions=4&cards=10").json()
    kinds = [i["kind"] for i in data["items"]]
    assert kinds.count("question") == 4 and kinds.count("card") == 4
    assert data["available"] == {"questions": 7, "cards": 4}
    # Réparties entre les deux quiz
    assert {i["quiz_id"] for i in data["items"] if i["kind"] == "question"} == {q1["id"], q2["id"]}

    # Supprimer une question décale le suivi des suivantes
    client.post(f"/api/quizzes/{q1['id']}/answers", json={"answers": [{"index": 1, "correct": False},
                                                                     {"index": 3, "correct": True}]})
    assert client.delete(f"/api/quizzes/{q1['id']}/questions/1").json() == {"questions": 4}
    quiz = client.get(f"/api/quizzes/{q1['id']}").json()
    assert [q["question"] for q in quiz["questions"]] == ["Q0 ?", "Q2 ?", "Q3 ?", "Q4 ?"]
    assert set(quiz["stats"]) == {"2"} and quiz["stats"]["2"]["right"] == 1
    assert client.delete(f"/api/quizzes/{q1['id']}/questions/9").status_code == 404

    # Nom complet modifiable
    client.patch(f"/api/quizzes/{q2['id']}", json={"title": "Mon quiz de révision"})
    summary = next(q for q in client.get(f"/api/courses/{cid}").json()["quizzes"] if q["id"] == q2["id"])
    assert summary["custom_title"] is True and summary["title"] == "Mon quiz de révision"

    # Correction par l'IA
    async def fake_ask(system, schema, prompt, model=None):
        assert "Réponse de l'étudiant : cellule nerveuse" in prompt
        return {"grades": [{"number": 1, "verdict": "juste", "reason": "Même idée."}, {"number": 9, "verdict": "faux", "reason": ""}]}
    monkeypatch.setattr(ollama_provider, "ask", fake_ask)
    graded = client.post("/api/partiel/grade", json={"items": [
        {"question": "Qu'est-ce qu'un neurone ?", "expected": "La cellule du système nerveux.", "given": "cellule nerveuse"},
        {"question": "Autre ?", "expected": "x", "given": ""}]}).json()
    assert graded["grades"] == [{"verdict": "juste", "reason": "Même idée."}, None]

    saved = client.post(f"/api/courses/{cid}/partiels", json={"score": 12.5, "points": 5, "total": 8, "duration": 600}).json()
    client.post(f"/api/courses/{cid}/partiels", json={"id": saved["id"], "score": 15, "points": 6, "total": 8})
    history = client.get(f"/api/courses/{cid}/partiel").json()["history"]
    assert len(history) == 1 and history[0]["score"] == 15 and history[0]["duration"] == 600


def test_exam_week_orients_revisions(client):
    from datetime import date, timedelta

    today = date.today()
    # Loin des partiels : intervalle normal, mais jamais après le début de la semaine
    assert srs.exam_cap(40, today, today + timedelta(days=100)) == 40
    assert srs.exam_cap(40, today, today + timedelta(days=30)) == 29
    # Deux dernières semaines : rappels resserrés ; pendant la semaine : chaque jour
    assert srs.exam_cap(8, today, today + timedelta(days=9)) == 3
    assert srs.exam_cap(8, today, today - timedelta(days=2)) == 1
    assert srs.exam_cap(8, today, today - timedelta(days=20)) == 8
    assert srs.exam_cap(0, today, today + timedelta(days=9)) == 0

    store = main.store
    cid = store.create_course("Neuro")["id"]
    other = store.create_course("Bio")["id"]
    folder = client.post("/api/folders", json={"name": "S1"}).json()
    client.put(f"/api/courses/{cid}/folder", json={"folder_id": folder["id"]})
    week = (today + timedelta(days=12)).isoformat()
    assert client.patch(f"/api/folders/{folder['id']}", json={"exam_week": "demain"}).status_code == 400
    client.patch(f"/api/folders/{folder['id']}", json={"exam_week": week})
    exam = client.get(f"/api/courses/{cid}").json()["exam"]
    assert exam == {"date": week, "days": 12, "from": "dossier"}
    assert client.get("/api/stats").json()["next_exam"]["days"] == 12
    # Un cours peut avoir sa propre date
    own = (today + timedelta(days=5)).isoformat()
    assert client.put(f"/api/courses/{other}/exam", json={"date": own}).json()["exam"]["from"] == "cours"
    assert client.get("/api/stats").json()["next_exam"]["course"] == "Bio"

    # Carte sue juste avant les partiels : elle revient avant la semaine
    store.save_doc(cid, "cards", {"cards": [card(id=f"n{i}") for i in range(60)]})
    reviewed = client.post(f"/api/courses/{cid}/cards/n0/review", json={"rating": "easy"}).json()
    assert reviewed["interval"] <= 4
    # 59 nouvelles cartes en 10 jours : 6 par jour, moins que les 20 habituelles (déjà 1 vue aujourd'hui)
    assert len(store.today_cards([cid])) == 19
    # Partiels dans 4 jours : il faut en voir 30 par jour, le quota monte
    client.put(f"/api/courses/{cid}/exam", json={"date": (today + timedelta(days=4)).isoformat()})
    assert len(store.today_cards([cid])) == 29
