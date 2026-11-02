import os
import time

from fastapi.testclient import TestClient

from app import main
from app.storage import Store


def wait_done(client, job_id):
    end = time.time() + 5
    while time.time() < end:
        job = client.get(f"/api/imports/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("import trop long")


def test_a_followed_folder_updates_courses_and_proposes_new_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path / "data"))
    client = TestClient(main.app)
    folder = tmp_path / "Neuro"
    folder.mkdir()
    (folder / "CM1.md").write_text("# CM1\n\nLe neurone.", encoding="utf-8")
    (folder / "notes.xlsx").write_bytes(b"pas un cours")
    (folder / "CM2.md").write_text("# CM2\n\nLa synapse.", encoding="utf-8")
    course = client.post("/api/courses", json={"name": "Neuro"}).json()
    client.post(f"/api/courses/{course['id']}/files", files={"files": ("CM1.md", b"# CM1\n\nLe neurone.")})

    # Suivre le dossier : CM1 est reconnu (rien à relire), CM2 est proposé, le tableur jamais
    found = client.post(f"/api/courses/{course['id']}/watch", json={"path": str(folder)}).json()
    assert found["updated"] == [] and found["new"] == ["CM2.md"] and found["job"] is None

    # « Ignorer » CM2 : plus jamais proposé
    client.post(f"/api/courses/{course['id']}/watch/ignore", json={"names": ["CM2.md"]})
    assert client.post(f"/api/courses/{course['id']}/watch/scan").json()["new"] == []

    # Un nouveau cours dans le dossier : « Ajouter »
    (folder / "CM3.md").write_text("# CM3\n\nLe potentiel d'action.", encoding="utf-8")
    assert client.post(f"/api/courses/{course['id']}/watch/scan").json()["new"] == ["CM3.md"]
    job = client.post(f"/api/courses/{course['id']}/watch/add", json={"names": ["CM3.md"]}).json()["job"]
    assert wait_done(client, job["id"])["result"]["results"] == {"CM3.md": "added"}

    # CM1 modifié dans le dossier : relu tout seul (nouvelle version), une seule fois
    (folder / "CM1.md").write_text("# CM1\n\nLe neurone et la myéline.", encoding="utf-8")
    later = time.time() + 10
    os.utime(folder / "CM1.md", (later, later))
    scan = client.post(f"/api/courses/{course['id']}/watch/scan").json()
    assert scan["updated"] == ["CM1.md"] and scan["new"] == []
    assert wait_done(client, scan["job"]["id"])["result"]["results"] == {"CM1.md": "updated"}
    assert client.post(f"/api/courses/{course['id']}/watch/scan").json()["updated"] == []


def test_adding_a_renamed_version_from_the_folder_proposes_to_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path / "data"))
    client = TestClient(main.app)
    text = " ".join(f"La phrase {i} du cours explique la méthode expérimentale et ses hypothèses." for i in range(60))
    folder = tmp_path / "Methode"
    folder.mkdir()
    (folder / "CM1 original.md").write_text(text, encoding="utf-8")
    course = client.post("/api/courses", json={"name": "Méthode"}).json()
    client.post(f"/api/courses/{course['id']}/files", files={"files": ("CM1 export.md", text.encode())})
    found = client.post(f"/api/courses/{course['id']}/watch", json={"path": str(folder)}).json()
    assert found["new"] == ["CM1 original.md"]
    job = client.post(f"/api/courses/{course['id']}/watch/add", json={"names": ["CM1 original.md"]}).json()["job"]
    result = wait_done(client, job["id"])["result"]
    assert result["results"] == {} and result["pending"][0]["target"]["name"] == "CM1 export.md"
    token = result["pending"][0]["token"]
    done = client.post(f"/api/courses/{course['id']}/files/pending/{token}", json={"replace": True}).json()
    assert [f["name"] for f in done["course"]["files"]] == ["CM1 original.md"]
    # Désormais suivi : plus proposé comme nouveau
    assert client.post(f"/api/courses/{course['id']}/watch/scan").json()["new"] == []
