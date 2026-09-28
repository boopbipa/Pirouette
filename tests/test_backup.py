import csv
import zipfile
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import backup, main
from app.storage import Store


def test_backup_zip_and_csv(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    folder = client.post("/api/folders", json={"name": "Semestre 1"}).json()["id"]
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.put(f"/api/courses/{cid}/folder", json={"folder_id": folder})
    store.save_doc(cid, "cards", {"cards": [{"id": "a", "front": "Le neurone ; quoi ?", "back": "Une cellule", "status": "new",
                                              "scope": ["Chapitre 1"]}]})
    dest = tmp_path / "Sauvegardes"
    store.save_settings(backup_dir=str(dest), anthropic_api_key="sk-ant-secret")
    now = datetime(2026, 10, 1, 9, 30)
    made = backup.make(store, now=now)
    assert made.name == "Pirouette 2026-10-01 09h30"
    with zipfile.ZipFile(made / "pirouette-donnees.zip") as archive:
        assert any(name.endswith("course.json") for name in archive.namelist())
        assert "sk-ant-secret" not in archive.read("settings.json").decode()
    with (made / "flashcards.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0][:3] == ["Recto", "Verso", "Cours"] and rows[1][:5] == ["Le neurone ; quoi ?", "Une cellule", "Neuro", "Semestre 1", "Chapitre 1"]
    assert (made / "quiz.csv").exists()
    # Fréquence : chaque semaine par défaut
    assert not backup.due(store, now + timedelta(days=6)) and backup.due(store, now + timedelta(days=7))
    store.save_settings(backup_mode="open")
    assert backup.due(store, now + timedelta(days=1)) and not backup.due(store, now + timedelta(hours=2))
    store.save_settings(backup_mode="off")
    assert not backup.due(store, now + timedelta(days=30))
    # On garde les 10 dernières
    for i in range(12):
        backup.make(store, now=now + timedelta(minutes=i + 1))
    assert len([p for p in dest.iterdir() if p.name.startswith("Pirouette ")]) == backup.KEEP
    # API
    assert client.put("/api/settings", json={"backup_mode": "chaque heure"}).status_code == 400
    assert client.put("/api/settings", json={"backup_mode": "week"}).json()["backup_mode"] == "week"
    assert client.post("/api/backup").json()["last_backup"]
