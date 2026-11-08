from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from app import agenda, main
from app.storage import Store

ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:1@x\r
DTSTART;TZID=Europe/Paris:{d1}T090000\r
DTEND;TZID=Europe/Paris:{d1}T110000\r
SUMMARY:Neurosciences\r
LOCATION:Salle L24\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:2@x\r
DTSTART;TZID=Europe/Paris:{d2}T083000\r
DTEND;TZID=Europe/Paris:{d2}T100000\r
SUMMARY:Psychometrie\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:3@x\r
DTSTART;TZID=Europe/Paris:{d2}T140000\r
DTEND;TZID=Europe/Paris:{d2}T160000\r
SUMMARY:ANNULÉ – Psychanalyse\r
STATUS:CANCELLED\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:4@x\r
DTSTART;TZID=Europe/Paris:{d2}T180000\r
DTEND;TZID=Europe/Paris:{d2}T190000\r
SUMMARY:Réunion information AFEV avec un titre assez long pour être replié sur deux lignes dans le fich\r
 ier\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_parse_and_match():
    events = agenda.parse_ics(ICS.format(d1="20261109", d2="20261110"))
    assert [e["summary"] for e in events][:2] == ["Neurosciences", "Psychometrie"]
    assert events[0]["start"] == "2026-11-09T09:00" and events[0]["location"] == "Salle L24"
    cancelled = next(e for e in events if e["uid"] == "3@x")
    assert cancelled["cancelled"] and cancelled["summary"] == "Psychanalyse"
    assert events[-1]["summary"].endswith("dans le fichier")
    courses = [{"id": "n", "name": "Neurosciences"}, {"id": "p", "name": "Psychométrie"}, {"id": "s", "name": "Statistiques"}]
    assert agenda.auto_match("Psychometrie", courses) == "p"
    assert agenda.auto_match("Traitement statistique des donnees", courses) == "s"
    assert agenda.auto_match("Réunion information AFEV", courses) is None


def _setup(tmp_path, monkeypatch, today):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    d1, d2 = today.strftime("%Y%m%d"), (today + timedelta(days=1)).strftime("%Y%m%d")
    monkeypatch.setattr(agenda, "fetch", lambda url, timeout=25: ICS.format(d1=d1, d2=d2))
    neuro = store.create_course("Neurosciences")
    psy = store.create_course("Psychométrie")
    return store, neuro, psy


def test_agenda_settings_and_week(tmp_path, monkeypatch):
    today = date.today()
    store, neuro, psy = _setup(tmp_path, monkeypatch, today)
    client = TestClient(main.app)
    view = client.put("/api/agenda", json={"url": "https://exemple.fr/edt.ics"}).json()
    assert view["count"] == 4 and not view["error"]
    rows = {r["summary"]: r for r in view["subjects"]}
    assert rows["Neurosciences"]["course_id"] == neuro["id"] and rows["Réunion information AFEV avec un titre assez long pour être replié sur deux lignes dans le fichier"]["course_id"] is None
    view = client.put("/api/agenda/map", json={"summary": "Neurosciences", "course_id": "none"}).json()
    assert {r["summary"]: r for r in view["subjects"]}["Neurosciences"]["course_id"] is None
    week = client.get(f"/api/agenda/week?start={today.isoformat()}").json()
    assert any(c["summary"] == "Psychometrie" and c["course_id"] == psy["id"] for c in week["classes"]) or today.weekday() == 6


def test_before_class_and_after_class(tmp_path, monkeypatch):
    today = date.today()
    store, neuro, psy = _setup(tmp_path, monkeypatch, today)
    # Un cours de Psychométrie ajouté il y a 5 jours (prochaine révision J+7, pas aujourd'hui)
    store.add_file(psy["id"], "CM1.pdf", b"%PDF", "texte")
    course = store.get_course(psy["id"])
    course["files"][0]["added_at"] = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")
    store._save_course(course)
    main._agenda().set_url("https://exemple.fr/edt.ics")
    main._agenda().refresh()
    now = datetime.combine(today, datetime.min.time()) + timedelta(hours=12)  # midi : Neurosciences (9 h – 11 h) est fini
    revs = main._revisions(today, now)
    extra = [r for r in revs["today"] if r.get("extra")]
    assert extra and extra[0]["course_id"] == psy["id"] and extra[0]["reason"] == "Avant ton cours de demain 8 h 30"
    # Faite : notée, sans changer le rythme du cours
    client = TestClient(main.app)
    fid = course["files"][0]["id"]
    before = store.get_course(psy["id"])["files"][0]["rev"]["due"]
    client.post(f"/api/revisions/{psy['id']}/{fid}/done", json={"score": 1, "extra": True})
    assert store.get_course(psy["id"])["files"][0]["rev"]["due"] == before
    revs = main._revisions(today, now)
    assert not [r for r in revs["today"] if r.get("extra")] and revs["done"]
    # Neurosciences ce matin, rien d'ajouté : le rappel
    [hint] = revs["after_class"]
    assert hint["course_id"] == neuro["id"] and hint["when"] == "ce matin"
    client.post("/api/agenda/dismiss", json={"uid": hint["uid"]})
    assert main._revisions(today, now)["after_class"] == []


def test_after_class_the_followed_folder_is_read_again(tmp_path, monkeypatch):
    today = date.today()
    store, neuro, psy = _setup(tmp_path, monkeypatch, today)
    folder = tmp_path / "Neuro"
    folder.mkdir()
    (folder / "CM1.txt").write_text("Le neurone.", encoding="utf-8")
    client = TestClient(main.app)
    client.post(f"/api/courses/{neuro['id']}/watch", json={"path": str(folder)})
    store.add_file(neuro["id"], "CM1.txt", b"Le neurone.", "Le neurone.")
    course = store.get_course(neuro["id"])
    from app import watch
    watch.link(course, str(folder))
    course["files"][0]["rev"] = {"start": (today - timedelta(days=9)).isoformat(), "step": 3, "due": (today + timedelta(days=4)).isoformat()}
    store._save_course(course)
    calls = []
    monkeypatch.setattr(main, "_watch_scan", lambda cid, restart_updated=False: calls.append((cid, restart_updated)) or {"updated": [], "new": []})
    main._agenda().set_url("https://exemple.fr/edt.ics")
    now = datetime.combine(today, datetime.min.time()) + timedelta(hours=12)
    out = main._agenda_tick(now)
    assert out["refreshed"] and calls == [(neuro["id"], True)]
    assert main._agenda_tick(now)["scanned"] == [] and len(calls) == 1  # une seule fois par séance


def test_a_course_edited_during_class_starts_again_at_day_0(tmp_path, monkeypatch):
    import os
    import time
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    neuro = store.create_course("Neurosciences")
    folder = tmp_path / "Neuro"
    folder.mkdir()
    (folder / "CM1.txt").write_text("Le neurone.", encoding="utf-8")
    store.add_file(neuro["id"], "CM1.txt", b"Le neurone.", "Le neurone.")
    course = store.get_course(neuro["id"])
    from app import watch
    watch.link(course, str(folder))
    course["files"][0]["rev"] = {"start": "2026-01-01", "step": 4, "due": "2099-01-01"}
    store._save_course(course)
    later = time.time() + 120
    (folder / "CM1.txt").write_text("Le neurone. Notes prises en cours.", encoding="utf-8")
    os.utime(folder / "CM1.txt", (later, later))
    found = main._watch_scan(neuro["id"], restart_updated=True)
    assert found["updated"] == ["CM1.txt"]
    rev = store.get_course(neuro["id"])["files"][0]["rev"]
    assert rev["step"] == 0 and rev["due"] == date.today().isoformat()
