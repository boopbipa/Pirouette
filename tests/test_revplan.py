from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from app import main, revplan
from app.storage import Store


class FakeCalendar:
    """Le Calendrier du Mac, pour les tests : des événements qu'on peut déplacer ou supprimer « à la main »."""

    def __init__(self):
        self.items, self.n = {}, 0

    def events(self, start, end):
        return [{"id": i, "key": revplan.key_in(e["notes"]), "title": e["title"], "notes": e["notes"], "start": e["start"]}
                for i, e in self.items.items() if start.isoformat() <= e["start"].date().isoformat() < end.isoformat()]

    def create(self, slot):
        self.n += 1
        self.items[str(self.n)] = {"title": slot.title, "notes": slot.notes, "start": slot.start, "end": slot.end}
        return str(self.n)

    def retitle(self, event_id, title):
        self.items[event_id]["title"] = title

    def renote(self, event_id, notes):
        self.items[event_id]["notes"] = notes

    def delete(self, event_id):
        self.items.pop(event_id, None)

    def by_key(self, key):
        return next((i, e) for i, e in self.items.items() if revplan.key_in(e["notes"]) == key)


def test_placed_at_the_chosen_hour_after_classes():
    now = datetime(2026, 11, 9, 8, 0)
    items = [{"course_id": "a" * 12, "file_id": "b" * 8, "day": "2026-11-09", "course": "Neuro", "title": "CM1", "minutes": 12, "reason": "J+1"},
             {"course_id": "c" * 12, "file_id": "d" * 8, "day": "2026-11-09", "course": "Psycho", "title": "CM2", "minutes": 4, "reason": "J+3"}]
    busy = [(datetime(2026, 11, 9, 17, 30), datetime(2026, 11, 9, 19, 0))]
    a, b = revplan.place(items, busy, "18:00", now)
    assert a.start == datetime(2026, 11, 9, 19, 5) and a.end == datetime(2026, 11, 9, 19, 15)
    assert b.start == datetime(2026, 11, 9, 19, 20) and (b.end - b.start) == timedelta(minutes=10)
    assert revplan.key_in(a.notes) == a.key


def _setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    course = store.create_course("Neurosciences")
    store.add_file(course["id"], "CM1.pdf", b"%PDF", "texte")
    store.add_card(course["id"], "Q", "R")
    fid = store.get_course(course["id"])["files"][0]["id"]
    state = main._calendar_state()
    state["enabled"] = True
    main._calendar_save(state)
    return store, course["id"], fid


def test_sync_creates_marks_done_and_respects_moves(tmp_path, monkeypatch):
    store, cid, fid = _setup(tmp_path, monkeypatch)
    cal = FakeCalendar()
    today = date.today()
    now = datetime.combine(today, datetime.min.time()) + timedelta(hours=9)
    main._calendar_sync(now, cal)
    key = revplan.key_of(cid, fid, today.isoformat())
    event_id, event = cal.by_key(key)
    assert event["title"] == "Réviser Neurosciences · CM1" and event["start"].hour == 18
    # Tu la déplaces à 21 h : Pirouette ne la remet pas à 18 h
    event["start"] = event["start"].replace(hour=21)
    main._calendar_sync(now, cal)
    assert cal.by_key(key)[1]["start"].hour == 21 and len(cal.items) == 1
    # Faite : cochée, et la suivante (J+1) est ajoutée
    TestClient(main.app).post(f"/api/revisions/{cid}/{fid}/done", json={"score": 1})
    main._calendar_sync(now, cal)
    assert cal.by_key(key)[1]["title"].startswith("✓ ")
    assert cal.by_key(revplan.key_of(cid, fid, (today + timedelta(days=1)).isoformat()))


def test_deleting_a_revision_postpones_it_to_tomorrow(tmp_path, monkeypatch):
    store, cid, fid = _setup(tmp_path, monkeypatch)
    cal = FakeCalendar()
    today = date.today()
    now = datetime.combine(today, datetime.min.time()) + timedelta(hours=9)
    main._calendar_sync(now, cal)
    key = revplan.key_of(cid, fid, today.isoformat())
    cal.delete(cal.by_key(key)[0])
    state = main._calendar_sync(now, cal)
    assert state["postponed"] == 1
    assert store.get_course(cid)["files"][0]["rev"]["due"] == (today + timedelta(days=1)).isoformat()
    assert not any(revplan.key_in(e["notes"]) == key for e in cal.items.values())  # pas recréée
    assert cal.by_key(revplan.key_of(cid, fid, (today + timedelta(days=1)).isoformat()))


def test_a_deleted_calendar_is_not_a_mass_postponement(tmp_path, monkeypatch):
    store, cid, fid = _setup(tmp_path, monkeypatch)
    for name in ("CM2.pdf", "CM3.pdf", "CM4.pdf"):
        store.add_file(cid, name, b"%PDF", "texte")
    cal = FakeCalendar()
    now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    main._calendar_sync(now, cal)
    assert len(cal.items) == 4
    cal.items.clear()
    state = main._calendar_sync(now, cal)
    assert state["postponed"] == 0 and len(cal.items) == 4


def test_calendar_settings(tmp_path, monkeypatch):
    from app import apple_calendar
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(apple_calendar, "available", lambda: False)  # (sur le Mac des tests : pas de vrai Calendrier)
    client = TestClient(main.app)
    view = client.put("/api/calendar", json={"hour": "19:30"}).json()
    assert view["hour"] == "19:30" and not view["enabled"] and view["available"] is False
    assert client.put("/api/calendar", json={"hour": "25:00"}).status_code == 400
    view = client.put("/api/calendar", json={"enabled": True}).json()
    assert view["enabled"] and "pas disponible" in view["error"]


def test_moving_a_revision_to_another_day_moves_it_in_pirouette(tmp_path, monkeypatch):
    store, cid, fid = _setup(tmp_path, monkeypatch)
    cal = FakeCalendar()
    today = date.today()
    now = datetime.combine(today, datetime.min.time()) + timedelta(hours=9)
    main._calendar_sync(now, cal)
    event_id, event = cal.by_key(revplan.key_of(cid, fid, today.isoformat()))
    later = today + timedelta(days=2)
    event["start"] = datetime.combine(later, datetime.min.time()) + timedelta(hours=20)
    event["title"] = "Neuro avant le sport"  # renommée aussi
    state = main._calendar_sync(now, cal)
    assert state["moved"] == 1 and len(cal.items) == 1  # pas de doublon
    assert store.get_course(cid)["files"][0]["rev"]["due"] == later.isoformat()  # l'accueil et le planning suivent
    assert revplan.key_in(cal.items[event_id]["notes"]) == revplan.key_of(cid, fid, later.isoformat())
    assert cal.items[event_id]["title"] == "Neuro avant le sport"  # ton titre est gardé
    # Le lendemain : toujours un seul événement, au jour choisi
    main._calendar_sync(now + timedelta(days=1), cal)
    assert len(cal.items) == 1 and cal.items[event_id]["start"].date() == later
