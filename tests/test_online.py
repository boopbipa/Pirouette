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
        self.tables = {"profiles": [], "friendships": [], "challenges": [], "attempts": [],
                       "rooms": [], "room_members": [], "messages": []}
        self.clock = 0  # horodatage croissant des lignes créées
        self.token_user = {}  # jeton -> id

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        body = json.loads(request.content) if request.content else None
        if path == "/auth/v1/signup":  # compte anonyme
            user = str(uuid.uuid4())
            self.users[user] = user
            token = uuid.uuid4().hex
            self.token_user[token] = user
            return httpx.Response(200, json={"access_token": token, "refresh_token": "r", "expires_in": 3600,
                                             "user": {"id": user, "is_anonymous": True}})
        if path == "/auth/v1/user/identities/authorize":  # relier un compte anonyme à Google
            self.linking = self.token_user.get(request.headers["Authorization"].removeprefix("Bearer "))
            return httpx.Response(200, json={"url": "https://accounts.google.com/o/oauth2/auth?lien=1"})
        if path == "/auth/v1/token" and params.get("grant_type") == "pkce":
            if body["auth_code"] != "bon-code" or not body["code_verifier"]:
                return httpx.Response(400, json={"msg": "invalid flow state"})
            user = getattr(self, "linking", None) or str(uuid.uuid4())
            token = uuid.uuid4().hex
            self.token_user[token] = user
            return httpx.Response(200, json={"access_token": token, "refresh_token": "r", "expires_in": 3600,
                                             "user": {"id": user, "email": "pilou@gmail.com", "is_anonymous": False,
                                                      "user_metadata": {"full_name": "Pierre-Louis"}}})
        table = path.removeprefix("/rest/v1/")
        if getattr(self, "old_base", False) and table == "challenges" and (
                "time_limit" in params.get("select", "") or (body and "time_limit" in body)):
            return httpx.Response(400, json={"message": "column challenges.time_limit does not exist"})
        rows = self.tables[table]
        def ok(row):
            for key, value in params.items():
                if key in ("select", "order", "limit"):
                    continue
                op, _, arg = value.partition(".")
                cell = row.get(key)
                if op == "eq" and str(cell) != arg:
                    return False
                if op == "in" and str(cell) not in arg.strip("()").split(","):
                    return False
                if op in ("gt", "gte") and not (str(cell) > arg if op == "gt" else str(cell) >= arg):
                    return False
            return True
        match = [r for r in rows if ok(r)]
        if request.method == "GET":
            if "order" in params:
                column, _, way = params["order"].partition(".")
                match = sorted(match, key=lambda r: str(r.get(column)), reverse=way == "desc")
            if "limit" in params:
                match = match[:int(params["limit"])]
            out = [self.expand(table, r) for r in match]
            columns = params.get("select", "")
            if columns and "(" not in columns:  # colonnes simples : seulement celles demandées
                out = [{k: r.get(k) for k in columns.split(",")} for r in out]
            return httpx.Response(200, json=out)
        if request.method == "POST" and isinstance(body, list):
            for item in body:
                if not any(all(r.get(k) == v for k, v in item.items()) for r in rows):
                    rows.append(self.defaults(table, dict(item)))
            return httpx.Response(201, json=[])
        if request.method == "POST":
            row = self.defaults(table, dict(body))
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
        wants_rows = "return=representation" in request.headers.get("Prefer", "")
        if request.method == "PATCH":
            for r in match:
                r.update(body)
            return httpx.Response(200, json=match) if wants_rows else httpx.Response(204)
        if request.method == "DELETE":
            for r in match:
                rows.remove(r)
            return httpx.Response(200, json=match) if wants_rows else httpx.Response(204)
        raise AssertionError(request)

    def defaults(self, table, row):
        self.clock += 1
        stamp = f"2099-01-01T10:{self.clock // 60:02d}:{self.clock % 60:02d}+00:00"
        if table in ("rooms", "messages"):
            row.setdefault("id", str(uuid.uuid4()))
            row.setdefault("created_at", stamp)
        if table == "room_members":
            row.setdefault("last_read", "2000-01-01T00:00:00+00:00")
        return row

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


def test_google_sign_in_and_linking(tmp_path, fake):
    new = player(tmp_path, fake, "new")
    url = run(new.google_url("http://127.0.0.1:5000/api/online/google/callback"))
    assert url.startswith("https://exemple.supabase.co/auth/v1/authorize?provider=google")
    assert "code_challenge=" in url and "redirect_to=http%3A%2F%2F127.0.0.1%3A5000" in url
    with pytest.raises(online.OnlineError):
        run(new.google_finish("mauvais-code"))
    run(new.google_url("http://127.0.0.1:5000/api/online/google/callback"))
    me = run(new.google_finish("bon-code"))
    assert me["connected"] and me["google"] and me["name"] == "Pierre-Louis" and me["profile"] is None
    with pytest.raises(online.OnlineError, match="expiré"):
        run(new.google_finish("bon-code"))  # un code ne sert qu'une fois

    # Un compte « pseudo » est relié à Google : même identifiant, mêmes amis et scores
    old = player(tmp_path, fake, "old")
    before = run(old.start("Pilou"))["profile"]["id"]
    url = run(old.google_url("http://127.0.0.1:5000/api/online/google/callback"))
    assert url.startswith("https://accounts.google.com/")
    me = run(old.google_finish("bon-code"))
    assert me["google"] and me["profile"]["id"] == before and me["profile"]["pseudo"] == "Pilou"


def test_timed_challenge_and_old_base(tmp_path, fake):
    alice = player(tmp_path, fake, "alice")
    run(alice.start("Alice"))
    quiz = {"title": "Neuro", "questions": [{"type": "qcm", "question": f"Q{i} ?", "choices": ["a", "b"], "answer": "a"}
                                            for i in range(30)]}
    challenge = run(alice.create_challenge(quiz, size=15, time_limit=300))
    assert len(challenge["questions"]) == 15 and challenge["time_limit"] == 300
    assert run(alice.challenge(challenge["id"]))["time_limit"] == 300
    # Base pas encore mise à jour (pas de colonne du chrono) : le défi se crée quand même, sans chrono
    fake.old_base = True
    other = run(alice.create_challenge(quiz, size=5, time_limit=120))
    assert "time_limit" not in other and len(other["questions"]) == 5
    assert len(run(alice.challenges(days=100000))) == 2


def test_messages_direct_and_group(tmp_path, fake):
    alice, bob, chloe = (player(tmp_path, fake, n) for n in ("alice", "bob", "chloe"))
    for client, pseudo in ((alice, "Alice"), (bob, "Bob"), (chloe, "Chloé")):
        run(client.start(pseudo))
    code = lambda c: run(c.me())["profile"]["friend_code"]  # noqa: E731
    bob_id, chloe_id = run(bob.me())["profile"]["id"], run(chloe.me())["profile"]["id"]
    for friend in (bob, chloe):  # Alice est amie avec Bob et Chloé
        run(alice.add_friend(code(friend)))
        run(friend.add_friend(code(alice)))

    # Conversation à deux : créée une fois, retrouvée ensuite
    room = run(alice.direct(bob_id))
    assert run(alice.direct(bob_id))["id"] == room["id"]
    with pytest.raises(online.OnlineError, match="amis"):
        run(bob.direct(chloe_id))  # Bob et Chloé ne sont pas amis
    run(alice.send(room["id"], "Salut !"))
    quiz = {"title": "Freud", "questions": [{"question": "Q ?", "answer": "a"}]}
    sent = run(alice.send(room["id"], "", "quiz", "Freud", {"count": 1}, quiz))
    bob_rooms = run(bob.rooms())
    assert [(r["name"], r["unread"], r["last"]["text"]) for r in bob_rooms] == [("Alice", 2, "📝 Quiz : Freud")]
    got = run(bob.messages(room["id"]))["messages"]
    assert [(m["pseudo"], m["kind"], m["mine"]) for m in got] == [("Alice", "text", False), ("Alice", "quiz", False)]
    assert "payload" not in got[1] and run(bob.payload(sent["id"]))["payload"] == quiz
    assert run(bob.rooms())[0]["unread"] == 0  # lus
    assert run(bob.messages(room["id"], after=got[-1]["created_at"]))["messages"] == []

    # Groupe : Alice y met ses deux amis, on y épingle un message
    group = run(alice.group("L1 Psycho", [bob_id, chloe_id]))
    run(chloe.send(group["id"], "Qui fait le défi ?"))
    names = {r["name"]: r for r in run(bob.rooms())}
    assert names["L1 Psycho"]["unread"] == 1 and len(names["L1 Psycho"]["members"]) == 3
    run(alice.update_room(group["id"], pinned=sent["id"]))
    assert next(r for r in run(alice.rooms()) if r["kind"] == "group")["pinned"] == sent["id"]
    run(chloe.leave(group["id"]))
    assert [r["name"] for r in run(chloe.rooms())] == []
    with pytest.raises(online.OnlineError, match="nom"):
        run(alice.group(" ", [bob_id]))


def test_send_and_import_a_quiz_and_cards(tmp_path, fake, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    stores = {"alice": Store(tmp_path / "alice"), "bob": Store(tmp_path / "bob")}
    who = {"me": "alice"}
    monkeypatch.setattr(main, "store", stores["alice"])
    monkeypatch.setattr(main, "_online", lambda: online.Online(stores[who["me"]], transport=httpx.MockTransport(fake.handler)))
    api = TestClient(main.app)

    def as_(name):
        who["me"] = name
        monkeypatch.setattr(main, "store", stores[name])

    for name in ("alice", "bob"):
        as_(name)
        api.post("/api/online/start", json={"text": name.title()})
    codes = {}
    for name in ("alice", "bob"):
        as_(name)
        codes[name] = api.get("/api/online").json()["profile"]
    as_("alice")
    api.post("/api/online/friends", json={"text": codes["bob"]["friend_code"]})
    as_("bob")
    api.post("/api/online/friends", json={"text": codes["alice"]["friend_code"]})

    # Alice envoie un quiz et ses flashcards
    as_("alice")
    store = stores["alice"]
    course = store.create_course("Psychanalyse")
    quiz = store.save_quiz({"course_id": course["id"], "course_name": "Psychanalyse", "title": "Freud", "scope": [],
                            "questions": [{"type": "qcm", "question": "Le ça ?", "choices": ["a", "b"], "answer": "a",
                                           "explanation": "", "stats": {"x": 1}}]})
    store.add_card(course["id"], "Le moi ?", "Instance")
    room = api.post("/api/online/rooms", json={"friend_id": codes["bob"]["id"]}).json()
    sent_quiz = api.post(f"/api/online/rooms/{room['id']}/messages", json={"kind": "quiz", "quiz_id": quiz["id"]}).json()
    sent_cards = api.post(f"/api/online/rooms/{room['id']}/messages", json={"kind": "cards", "course_id": course["id"]}).json()
    assert sent_quiz["meta"]["count"] == 1 and sent_cards["meta"]["cards"] == 1

    # Bob les ajoute à ses cours (un nouveau cours, puis le même : sans doublon de cartes)
    as_("bob")
    assert api.get("/api/online/unread").json() == {"unread": 2}
    done = api.post(f"/api/online/messages/{sent_quiz['id']}/import", json={"new_course": "Psycha (Alice)"}).json()
    assert done["questions"] == 1 and done["course_name"] == "Psycha (Alice)"
    got = stores["bob"].list_quizzes(done["course_id"])
    assert got[0]["title"] == "Freud" and "stats" not in stores["bob"].get_quiz(got[0]["id"])["questions"][0]
    for _ in range(2):
        cards = api.post(f"/api/online/messages/{sent_cards['id']}/import", json={"course_id": done["course_id"]}).json()
    assert cards["cards"] == 0 and len(stores["bob"].get_doc(done["course_id"], "cards")["cards"]) == 1
    result = api.post(f"/api/online/rooms/{room['id']}/messages",
                      json={"kind": "result", "title": "Freud", "score": 1, "total": 1, "seconds": 30}).json()
    assert result["meta"] == {"score": 1, "total": 1, "seconds": 30}


def test_edit_and_delete_my_messages(tmp_path, fake):
    alice, bob = player(tmp_path, fake, "alice"), player(tmp_path, fake, "bob")
    for client, pseudo in ((alice, "Alice"), (bob, "Bob")):
        run(client.start(pseudo))
    run(alice.add_friend(run(bob.me())["profile"]["friend_code"]))
    run(bob.add_friend(run(alice.me())["profile"]["friend_code"]))
    room = run(alice.direct(run(bob.me())["profile"]["id"]))
    sent = run(alice.send(room["id"], "Salut Bbo"))
    run(alice.edit_message(sent["id"], "Salut Bob"))
    got = run(bob.messages(room["id"]))["messages"][0]
    assert got["body"] == "Salut Bob" and got["edited_at"]
    with pytest.raises(online.OnlineError, match="pas encore activé"):
        run(bob.edit_message(sent["id"], "pas à moi"))  # le message d'un autre : refusé
    run(alice.delete_message(sent["id"]))
    assert run(bob.messages(room["id"]))["messages"] == []
