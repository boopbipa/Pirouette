from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import main, plan, reminder
from app.storage import Store

TODAY = date(2026, 10, 1)


def test_status_counts_kept_sessions():
    start = TODAY - timedelta(days=6)  # périodes de 2 jours : [-6,-5] [-4,-3] [-2,-1] [0,1]
    days = {TODAY - timedelta(days=6), TODAY - timedelta(days=3), TODAY - timedelta(days=1)}
    s = plan.status({"every": 2, "minutes": 15, "start": start.isoformat()}, days, TODAY)
    assert (s["kept"], s["total"], s["streak"], s["done_today"]) == (3, 3, 3, False)
    assert s["next"] == TODAY.isoformat() and s["deadline"] == (TODAY + timedelta(days=1)).isoformat()
    done = plan.status({"every": 2, "minutes": 15, "start": start.isoformat()}, days | {TODAY}, TODAY)
    assert done["done_today"] and done["kept"] == 4 and done["next"] == (TODAY + timedelta(days=2)).isoformat()
    missed = plan.status({"every": 2, "minutes": 15, "start": start.isoformat()}, {TODAY - timedelta(days=6)}, TODAY)
    assert (missed["kept"], missed["total"], missed["streak"]) == (1, 3, 0)
    assert plan.status({"every": 1, "minutes": 30, "start": TODAY.isoformat()}, set(), TODAY)["total"] == 0


def test_retro_spreads_chapters_then_consolidation_then_mock_exams():
    units = [{"course_id": "a", "course": "Neuro", "key": f"f-{i}", "title": f"Chap {i}"} for i in range(6)]
    courses = [{"id": "a", "name": "Neuro"}, {"id": "b", "name": "Bio"}]
    sessions = plan.build_retro(TODAY, TODAY + timedelta(days=20), 2, units, courses)
    assert len(sessions) == 10 and sessions[0]["date"] == TODAY.isoformat()
    kinds = [s["kind"] for s in sessions]
    assert kinds == ["learn"] * 6 + ["consolidate"] * 2 + ["blanc"] * 2
    assert [u["title"] for s in sessions for u in s["units"]] == [f"Chap {i}" for i in range(6)]
    assert sessions[-1]["course"] == "Bio"
    # Plus de chapitres que de séances : plusieurs par séance, aucun oublié
    many = plan.build_retro(TODAY, TODAY + timedelta(days=3), 1, units, courses[:1])
    assert sum(len(s["units"]) for s in many) == 6 and many[-1]["kind"] == "blanc"
    assert plan.build_retro(TODAY, TODAY, 1, units, courses) == []


def _setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    folder = client.post("/api/folders", json={"name": "Semestre 1"}).json()["id"]
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.put(f"/api/courses/{cid}/folder", json={"folder_id": folder})
    text = "Chapitre 1 : Le neurone\n" + "Le neurone transmet l'influx. " * 20 + "\nChapitre 2 : La synapse\n" + "La synapse. " * 30
    client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", text.encode(), "text/plain"))])
    cards = [{"id": f"c{i}", "front": f"Question {i}", "back": "Réponse", "status": "new", "scope": ["Chapitre 1 : Le neurone"]}
             for i in range(40)]
    cards[3].update(status="review", reviews=4, lapses=3, last_rating="again", due=TODAY.isoformat())
    store.save_doc(cid, "cards", {"cards": cards})
    return store, client, folder, cid


def test_plan_api_and_session(tmp_path, monkeypatch):
    store, client, folder, cid = _setup(tmp_path, monkeypatch)
    empty = client.get(f"/api/plan?folder={folder}").json()
    assert empty["plan"] is None and empty["hard"][0]["front"] == "Question 3"
    assert client.put("/api/plan", json={"folder": folder, "every": 5, "minutes": 15}).status_code == 400
    data = client.put("/api/plan", json={"folder": folder, "every": 2, "minutes": 15}).json()
    assert data["status"]["total"] == 0 and data["status"]["next"] == date.today().isoformat()
    items = client.get(f"/api/session?mode=plan&folder={folder}&minutes=15&questions=false").json()["items"]
    assert len(items) == 21 and items[0]["card"]["id"] == "c3"  # la plus difficile d'abord, puis 20 nouvelles
    chapter = client.get(f"/api/session?mode=chapter&course={cid}&chapter=Chapitre 1 : Le neurone").json()
    assert chapter["cards"] == 40
    assert reminder.daily_text(store) and "Semestre 1" in reminder.daily_text(store)
    store.log_activity(cid, cards=3)
    assert reminder.daily_text(store) is None  # séance faite : pas de rappel
    assert client.get(f"/api/plan?folder={folder}").json()["status"]["done_today"]
    client.delete(f"/api/plan?folder={folder}")
    assert client.get(f"/api/plan?folder={folder}").json()["plan"] is None


def test_retro_api(tmp_path, monkeypatch):
    store, client, folder, cid = _setup(tmp_path, monkeypatch)
    assert client.post("/api/retro", json={"folder": folder, "every": 2}).status_code == 400  # pas de partiels
    exam = (date.today() + timedelta(days=14)).isoformat()
    data = client.post("/api/retro", json={"folder": folder, "every": 2, "exam": exam}).json()
    assert data["exam"] == exam and store.get_folder(folder)["exam_week"] == exam
    sessions = data["retro"]["sessions"]
    assert len(sessions) == 7 and sessions[0]["today"]
    titles = [u["title"] for s in sessions for u in s["units"]]
    assert titles == ["Chapitre 1 : Le neurone", "Chapitre 2 : La synapse"]
    assert sessions[-1]["kind"] == "blanc" and sessions[-1]["course_id"] == cid
    first = sessions[0]["date"]
    checked = client.put("/api/retro/session", json={"folder": folder, "date": first, "done": True}).json()
    assert checked["retro"]["sessions"][0]["done"]
    # Recalcul : le chapitre de la séance faite n'est pas reprogrammé
    again = client.post("/api/retro", json={"folder": folder, "every": 3}).json()["retro"]["sessions"]
    assert [u["title"] for s in again for u in s["units"]].count("Chapitre 1 : Le neurone") == 1
    assert again[0]["done"]
