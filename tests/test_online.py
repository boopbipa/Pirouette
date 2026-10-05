"""Défis en ligne : le client Supabase de Pirouette, contre un faux Supabase en mémoire (pas d'internet)."""
import asyncio
import json
import uuid

import httpx
import pytest

from app import online
from app.storage import Store


class FakeSupabase:
    """Juste ce qu'il faut de Supabase : connexion par code, et les tables (filtres « eq. » et jointures simplifiées)."""

    def __init__(self):
        self.users = {}       # comptes créés
        self.tables = {"profiles": [], "friendships": [], "challenges": [], "attempts": []}
        self.token_user = {}  # jeton -> id

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        body = json.loads(request.content) if request.content else None
        if path == "/auth/v1/signup":  # compte anonyme
            user = str(uuid.uuid4())
            self.users[user] = user
            return httpx.Response(200, json={"access_token": uuid.uuid4().hex, "refresh_token": "r", "expires_in": 3600,
                                             "user": {"id": user, "is_anonymous": True}})
        table = path.removeprefix("/rest/v1/")
        rows = self.tables[table]
        filters = {k: v.removeprefix("eq.") for k, v in params.items() if v.startswith("eq.")}
        match = [r for r in rows if all(str(r.get(k)) == v for k, v in filters.items())]
        if request.method == "GET":
            return httpx.Response(200, json=[self.expand(table, r) for r in match])
        if request.method == "POST":
            row = dict(body)
            if table == "profiles":
                row.setdefault("friend_code", uuid.uuid4().hex[:6].upper())
            if table == "challenges":
                row.setdefault("id", str(uuid.uuid4()))
                row.setdefault("day", "2099-01-01")
                row.setdefault("created_at", "2099-01-01T10:00:00")
            if table == "friendships":
                row.setdefault("status", "pending")
            rows.append(row)
            return httpx.Response(201, json=[row])
        if request.method == "PATCH":
            for r in match:
                r.update(body)
            return httpx.Response(204)
        if request.method == "DELETE":
            for r in match:
                rows.remove(r)
            return httpx.Response(204)
        raise AssertionError(request)

    def pseudo(self, user_id):
        return next(({"pseudo": p["pseudo"]} for p in self.tables["profiles"] if p["id"] == user_id), None)

    def expand(self, table, row):
        row = dict(row)
        if table == "friendships":
            row |= {"a": self.pseudo(row["user_id"]), "b": self.pseudo(row["friend_id"])}
        if table == "challenges":
            row["author"] = self.pseudo(row["owner"])
            row["attempts"] = [a | {"player": self.pseudo(a["user_id"])}
                               for a in self.tables["attempts"] if a["challenge_id"] == row["id"]]
        return row


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(online, "URL", "https://exemple.supabase.co")
    monkeypatch.setattr(online, "KEY", "cle-publique")
    return FakeSupabase()


def player(tmp_path, fake, name):
    return online.Online(Store(tmp_path / name), transport=httpx.MockTransport(fake.handler))


def run(coro):
    return asyncio.run(coro)


def test_not_configured_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(online, "URL", "")
    client = online.Online(Store(tmp_path))
    assert run(client.me()) == {"connected": False, "configured": False}
    with pytest.raises(online.OnlineError, match="pas encore ouverts"):
        run(client.start("Alice"))


def test_friends_and_a_challenge(tmp_path, fake):
    alice, bob = player(tmp_path, fake, "alice"), player(tmp_path, fake, "bob")
    for client, pseudo in ((alice, "Alice"), (bob, "Bob")):
        with pytest.raises(online.OnlineError, match="2 lettres"):
            run(client.start("A"))
        me = run(client.start(pseudo))
        assert me["connected"] and me["profile"]["pseudo"] == pseudo
    assert run(alice.set_pseudo("Alice B."))["profile"]["pseudo"] == "Alice B."

    # Alice ajoute Bob avec son code ; Bob accepte en ajoutant le code d'Alice
    bob_code = run(bob.me())["profile"]["friend_code"]
    alice_code = run(alice.me())["profile"]["friend_code"]
    with pytest.raises(online.OnlineError, match="propre code"):
        run(alice.add_friend(alice_code))
    assert run(alice.add_friend(bob_code))["status"] == "pending"
    assert [f["pseudo"] for f in run(bob.friends())["received"]] == ["Alice B."]
    assert run(bob.add_friend(alice_code))["status"] == "accepted"
    assert [f["pseudo"] for f in run(alice.friends())["friends"]] == ["Bob"]

    # Alice lance un défi : 10 questions de son quiz (sans le cours)
    quiz = {"title": "Neuro", "course_name": "Neurosciences",
            "questions": [{"type": "qcm", "question": f"Q{i} ?", "choices": ["a", "b"], "answer": "a",
                           "explanation": "", "source": "phrase du cours"} for i in range(25)]}
    challenge = run(alice.create_challenge(quiz))
    assert len(challenge["questions"]) == 10 and "source" not in challenge["questions"][0]
    listed = run(bob.challenges(days=100000))
    assert listed[0]["author"] == "Alice B." and not listed[0]["done"] and "scores" not in listed[0]
    run(bob.submit(challenge["id"], 8, 10, 95))
    run(alice.submit(challenge["id"], 9, 10, 120))
    scores = run(bob.challenges(days=100000))[0]["scores"]
    assert [(s["pseudo"], s["score"]) for s in scores] == [("Alice B.", 9), ("Bob", 8)]
    assert [s["me"] for s in scores] == [False, True]

    run(alice.remove_friend(run(bob.me())["profile"]["id"]))
    assert run(alice.friends())["friends"] == []
    alice.logout()
    assert run(alice.me())["connected"] is False
