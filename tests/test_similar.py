from fastapi.testclient import TestClient

from app import main
from app.similar import resemblance
from app.storage import Store

LECTURE = " ".join(f"La phrase numéro {i} du cours explique la méthode expérimentale et ses hypothèses." for i in range(60))
OTHER = " ".join(f"Le chapitre {i} de statistiques porte sur la variance et les tests du khi deux." for i in range(60))


def test_resemblance_sees_a_new_version_but_not_another_lecture():
    assert resemblance(LECTURE, LECTURE + " Un ajout de la nouvelle version.") > 0.9
    assert resemblance(LECTURE, OTHER) < 0.1


def test_a_renamed_new_version_is_proposed_then_replaces_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    course = client.post("/api/courses", json={"name": "Méthode"}).json()
    url = f"/api/courses/{course['id']}/files"
    client.post(url, files={"files": ("CM1.md", LECTURE.encode())})
    old_id = client.get(f"/api/courses/{course['id']}").json()["files"][0]["id"]

    answer = client.post(url + "?similar=1", files={"files": ("CM1 v2.md", (LECTURE + " Ajout.").encode())}).json()
    assert answer["results"] == {} and answer["pending"][0]["target"] == {"id": old_id, "name": "CM1.md"}
    # Un autre cours, sans rapport : ajouté directement
    assert client.post(url + "?similar=1", files={"files": ("Stats.md", OTHER.encode())}).json()["results"] == {"Stats.md": "added"}

    done = client.post(f"{url}/pending/{answer['pending'][0]['token']}", json={"replace": True}).json()
    assert done["results"] == {"CM1 v2.md": "updated"}
    files = {f["id"]: f["name"] for f in done["course"]["files"]}
    assert files[old_id] == "CM1 v2.md" and len(files) == 2


def test_keeping_both_adds_the_file_next_to_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    course = client.post("/api/courses", json={"name": "Méthode"}).json()
    url = f"/api/courses/{course['id']}/files"
    client.post(url, files={"files": ("CM1.md", LECTURE.encode())})
    token = client.post(url + "?similar=1", files={"files": ("Copie.md", LECTURE.encode())}).json()["pending"][0]["token"]
    done = client.post(f"{url}/pending/{token}", json={"replace": False}).json()
    assert done["results"] == {"Copie.md": "added"} and len(done["course"]["files"]) == 2
    assert client.post(f"{url}/pending/{token}", json={"replace": False}).status_code == 404
