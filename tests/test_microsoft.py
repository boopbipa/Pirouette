from fastapi.testclient import TestClient

from app import main, microsoft
from app.storage import Store


class FakeGraph:
    """OneDrive et OneNote pour les tests : un dossier « Neuro » avec deux cours, une section OneNote avec une page."""

    def __init__(self):
        self.version = "2026-11-09T10:00:00Z"

    def get(self, store, path, params=None, raw=False):
        if raw:
            if "/onenote/pages/" in path:
                return b"<html><head><title>x</title></head><body><h1>Le neurone</h1><p>Cellule <b>excitable</b>.</p><ul><li>axone</li></ul></body></html>"
            return b"contenu du cours"
        if path == "/me":
            return {"mail": "eleve@ecole.fr"}
        if path.startswith("/me/drive/root") or path.startswith("/me/drive/items/neuro") and "children" not in path:
            return {"id": "neuro", "name": "Neuro", "parentReference": {"id": "root"}}
        raise AssertionError(path)

    def all(self, store, path, params=None):
        if path.endswith("/children"):
            return [{"id": "f1", "name": "CM1.docx", "file": {}, "lastModifiedDateTime": self.version},
                    {"id": "f2", "name": "CM2.pdf", "file": {}, "lastModifiedDateTime": "2026-11-01T10:00:00Z"},
                    {"id": "d", "name": "Annales", "folder": {}},
                    {"id": "x", "name": "notes.xlsx", "file": {}}]
        if path.startswith("/me/onenote/sections/"):
            return [{"id": "p1", "title": "Le neurone", "lastModifiedDateTime": "2026-11-09T09:00:00Z"}]
        if path == "/me/onenote/notebooks":
            return [{"id": "b1", "displayName": "Psycho", "sections": [{"id": "s1", "displayName": "Neuro"}]}]
        raise AssertionError(path)


def _setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    graph = FakeGraph()
    monkeypatch.setattr(microsoft, "get", graph.get)
    monkeypatch.setattr(microsoft, "_all", graph.all)
    monkeypatch.setattr(main, "import_jobs", lambda: type("J", (), {"active": lambda self, c: None,
                                                                    "start": lambda self, c, files, similar: {"id": "job", "files": [n for n, _ in files]}})())
    return store, graph


def test_browse_and_follow_a_onedrive_folder(tmp_path, monkeypatch):
    store, graph = _setup(tmp_path, monkeypatch)
    course = store.create_course("Neurosciences")
    store.add_file(course["id"], "CM1.docx", b"old", "ancien")
    client = TestClient(main.app)
    listing = microsoft.browse_drive(store, "neuro")
    assert [i["name"] for i in listing["items"]] == ["Annales", "CM1.docx", "CM2.pdf"]  # pas le tableur
    found = client.put(f"/api/courses/{course['id']}/cloud", json={"kind": "drive", "id": "neuro", "name": "Neuro"}).json()
    assert found["new"] == ["CM2.pdf"] and found["updated"] == [] and found["cloud"]["name"] == "Neuro"
    # CM1 modifié en ligne (Word dans le navigateur) : relu tout seul
    graph.version = "2026-11-09T12:00:00Z"
    found = client.post(f"/api/courses/{course['id']}/watch/scan").json()
    assert found["updated"] == ["CM1.docx"] and found["job"]["files"] == ["CM1.docx"]
    assert client.post(f"/api/courses/{course['id']}/watch/add", json={"names": ["CM2.pdf"]}).json()["job"]["files"] == ["CM2.pdf"]


def test_a_onenote_section_becomes_courses(tmp_path, monkeypatch):
    store, _ = _setup(tmp_path, monkeypatch)
    course = store.create_course("Neurosciences")
    microsoft.link(store, course, "onenote", "s1", "Neuro")
    assert microsoft.scan(store, course)["new"] == ["Le neurone.md"]
    [(name, data)] = microsoft.take(store, course, ["Le neurone.md"])
    text = data.decode()
    assert name == "Le neurone.md" and "# Le neurone" in text and "Cellule excitable." in text and "- axone" in text
    assert microsoft.notebooks(store)[0]["sections"][0]["name"] == "Neuro"


def test_connection_flow(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(microsoft, "CLIENT_ID", "id-de-test")
    client = TestClient(main.app)
    assert client.get("/api/microsoft").json() == {"available": True, "connected": False, "account": ""}
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: None)
    url = client.post("/api/microsoft/connect").json()["url"]
    assert "client_id=id-de-test" in url and "code_challenge_method=S256" in url
    state = url.split("state=")[1].split("&")[0]

    class Response:
        status_code = 200

        def json(self):
            return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Response())
    monkeypatch.setattr(microsoft, "get", lambda store, path, params=None, raw=False: {"mail": "eleve@ecole.fr"})
    page = client.get(f"/?code=abc&state={state}").text
    assert "C'est bon" in page and "eleve@ecole.fr" in page
    assert client.get("/api/microsoft").json() == {"available": True, "connected": True, "account": "eleve@ecole.fr"}
