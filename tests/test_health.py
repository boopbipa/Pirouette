from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import main, watch
from app.storage import Store


def test_sync_status_lists_what_is_kept_up_to_date(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "_calendar_soon", lambda: None)
    client = TestClient(main.app)
    assert client.get("/api/health").json() == {"rows": [], "errors": 0}
    # Un Calendrier à jour, un dossier suivi qui a disparu
    state = main._calendar_state()
    state.update(enabled=True, synced_at=datetime.now().isoformat(timespec="seconds"), count=3)
    main._calendar_save(state)
    course = store.create_course("Neurosciences")
    folder = tmp_path / "Neuro"
    folder.mkdir()
    c = store.get_course(course["id"])
    watch.link(c, str(folder))
    store._save_course(c)
    folder.rmdir()
    data = client.get("/api/health").json()
    rows = {r["id"]: r for r in data["rows"]}
    assert rows["calendar"]["state"] == "ok" and "3 révisions" in rows["calendar"]["detail"]
    assert rows[f"watch:{course['id']}"]["state"] == "error" and data["errors"] == 1
    # Pas de nouvelles du Calendrier depuis hier : « pas de mise à jour récente », pas une erreur
    state["synced_at"] = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    main._calendar_save(state)
    assert {r["id"]: r for r in client.get("/api/health").json()["rows"]}["calendar"]["state"] == "late"
