import asyncio
import json
import uuid

import httpx
import pytest

from app import online, sync
from app.storage import Store
from tests.test_online import FakeSupabase


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(online, "URL", "https://exemple.supabase.co")
    monkeypatch.setattr(online, "KEY", "cle-publique")
    return FakeSupabase()


def run(coro):
    return asyncio.run(coro)


def mac_with_cards(tmp_path, fake):
    store = Store(tmp_path / "mac")
    course = store.create_course("Neurosciences")
    store.save_doc(course["id"], "cards", {"cards": [
        {"id": "c1", "front": "Neurone ?", "back": "Cellule nerveuse"},
        {"id": "c2", "front": "Synapse ?", "back": "Jonction"}]})
    store.save_quiz({"id": "a1b2c3d4e5f6", "title": "Quiz neuro", "course_id": course["id"], "questions": [
        {"type": "vrai_faux", "kind": "notion", "question": "Le neurone conduit l'influx.", "choices": ["Vrai", "Faux"],
         "answer": "Vrai", "explanation": "", "source": "", "key_terms": []}]})
    mac = online.Online(store, transport=httpx.MockTransport(fake.handler))
    run(mac.start("Pilou"))
    return store, course, mac


def test_push_sends_only_what_changed_and_never_the_course_files(tmp_path, fake):
    store, course, mac = mac_with_cards(tmp_path, fake)
    s = sync.Sync(mac)
    first = run(s.run())
    kinds = sorted((r["kind"], r["id"]) for r in fake.tables["sync_docs"])
    assert kinds == [("deck", course["id"]), ("meta", "all"), ("quiz", "a1b2c3d4e5f6")]
    assert first["pushed"] == 3
    meta = next(r["data"] for r in fake.tables["sync_docs"] if r["kind"] == "meta")
    assert meta["courses"][0]["name"] == "Neurosciences"
    # Des cours (fichiers), seulement le nom et l'identifiant : jamais leur texte ni leurs chapitres
    assert all(set(f) == {"id", "name"} for f in meta["courses"][0]["files"])
    assert "neurone" not in json.dumps(meta["courses"], ensure_ascii=False).lower()
    assert run(s.run())["pushed"] == 0  # rien n'a changé
    store.delete_quiz("a1b2c3d4e5f6")
    again = run(s.run())
    assert again["removed"] == 1 and not any(r["kind"] == "quiz" for r in fake.tables["sync_docs"])


def test_phone_pairs_with_the_code_and_its_reviews_come_back_to_the_mac(tmp_path, fake):
    store, course, mac = mac_with_cards(tmp_path, fake)
    s = sync.Sync(mac)
    pairing = run(s.pair())
    assert len(pairing["code"]) == 8 and "-" not in pairing["code"] and "#jumeler=" in pairing["url"] and pairing["qr"].startswith("<svg")
    # L'iPhone : un compte anonyme, puis le code
    phone = online.Online(Store(tmp_path / "phone"), transport=httpx.MockTransport(fake.handler))
    signup = run(phone._call("POST", "/auth/v1/signup", auth=False, json={})).json()
    phone._keep(signup)
    owner = run(phone._call("POST", "/rest/v1/rpc/sync_pair", json={"pair_code": pairing["code"], "device_name": "iPhone"})).json()
    assert owner == mac._session()["user_id"]
    assert [d["name"] for d in run(s.devices())] == ["iPhone"]
    # Deux réponses sur l'iPhone : une carte sue, une question de quiz ratée
    fake.tables["sync_events"] += [
        {"id": str(uuid.uuid4()), "owner": owner, "kind": "card", "created_at": "2099-01-01T10:00:00+00:00",
         "payload": {"course_id": course["id"], "card_id": "c1", "rating": "good", "at": "2026-10-20T08:30:00"}},
        {"id": str(uuid.uuid4()), "owner": owner, "kind": "answers", "created_at": "2099-01-01T10:00:01+00:00",
         "payload": {"quiz_id": "a1b2c3d4e5f6", "answers": [{"index": 0, "correct": False}], "at": "2026-10-20T08:31:00"}},
        {"id": str(uuid.uuid4()), "owner": owner, "kind": "card", "created_at": "2099-01-01T10:00:02+00:00",
         "payload": {"course_id": course["id"], "card_id": "effacée", "rating": "good", "at": "2026-10-20T08:32:00"}}]
    result = run(s.run())
    assert result["applied"] == 2 and fake.tables["sync_events"] == []
    card = next(c for c in store.get_doc(course["id"], "cards")["cards"] if c["id"] == "c1")
    assert card["last_reviewed"] == "2026-10-20T08:30:00" and card["due"] == "2026-10-22"
    assert store.get_quiz("a1b2c3d4e5f6")["stats"]["0"]["last"] is False
    assert store.activity()["2026-10-20"][course["id"]]["cards"] == 1
    # La carte replanifiée repart en ligne
    deck = next(r["data"] for r in fake.tables["sync_docs"] if r["kind"] == "deck")
    assert next(c for c in deck["cards"] if c["id"] == "c1")["due"] == "2026-10-22"
    # Un vieil évènement (la carte a été revue depuis) ne la fait pas reculer
    assert store.apply_remote_review(course["id"], "c1", "again", "2026-10-19T08:00:00") is False
    run(s.remove_device(signup["user"]["id"]))
    assert run(s.devices()) == []


def test_data_size_counts_quizzes_and_cards(tmp_path, fake):
    store, _, _ = mac_with_cards(tmp_path, fake)
    size = sync.data_size(store)
    assert (size["quizzes"], size["questions"], size["cards"]) == (1, 1, 2) and size["bytes"] > 100


def test_custom_quiz_picks_chapters_questions_and_cards(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    store = Store(tmp_path / "data")
    monkeypatch.setattr(main, "store", store)
    course = store.create_course("Neuro")
    course["files"] = [{"id": "f1", "name": "neuro.pdf", "chapters": [{"title": "Chapitre 1 : Le neurone"}, {"title": "Chapitre 2 : La myéline"}]}]
    store._save_course(course)
    q = {"type": "vrai_faux", "kind": "notion", "question": "?", "choices": ["Vrai", "Faux"], "answer": "Vrai",
         "explanation": "", "source": "", "key_terms": []}
    store.save_quiz({"id": "a1b2c3d4e5f6", "title": "Neurone", "course_id": course["id"], "scope": ["Chapitre 1 : Le neurone"], "questions": [q] * 4})
    store.save_quiz({"id": "b1b2c3d4e5f6", "title": "Myéline", "course_id": course["id"], "scope": ["Chapitre 2 : La myéline"], "questions": [q] * 6})
    store.save_doc(course["id"], "cards", {"cards": [{"id": f"k{i}", "front": "a", "back": "b", "scope": ["Chapitre 2 : La myéline"]} for i in range(5)]})
    client = TestClient(main.app)
    info = client.get(f"/api/custom-quiz?course={course['id']}").json()
    assert info["questions"] == 10 and info["cards"] == 5
    assert [(c["title"], c["questions"], c["cards"]) for c in info["chapters"]] == [("Chapitre 1 : Le neurone", 4, 0), ("Chapitre 2 : La myéline", 6, 5)]
    whole = client.post("/api/custom-quiz", json={"course": course["id"], "questions": 0}).json()
    assert (whole["questions"], whole["cards"]) == (10, 0)  # toute la matière, toutes les questions
    one = client.post("/api/custom-quiz", json={"course": course["id"], "questions": 3, "cards": 2,
                                                "chapters": [{"course_id": course["id"], "title": "Chapitre 2 : La myéline"}]}).json()
    assert (one["questions"], one["cards"]) == (3, 2)
    assert all(i["quiz_id"] == "b1b2c3d4e5f6" for i in one["items"] if i["kind"] == "question")
