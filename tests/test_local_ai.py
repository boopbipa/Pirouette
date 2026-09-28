import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import local_ai, main
from app.storage import Store


def test_recommended_model_by_ram():
    assert local_ai.recommended_model(6) is None
    assert local_ai.recommended_model(7.9)["name"] == "qwen3.5:4b"
    assert local_ai.recommended_model(17.9)["name"] == "qwen3.5:9b"   # MacBook M3 Pro 18 Go
    assert local_ai.recommended_model(36)["name"] == "qwen3.5:27b"
    assert local_ai.recommended_model(None)["name"] == "qwen3.5:9b"   # mémoire inconnue


def test_recommended_context_by_ram():
    assert local_ai.recommended_context(7.9) == 16384
    assert local_ai.recommended_context(17.9) == 32768   # 18 Go : 32 768 tokens
    assert local_ai.recommended_context(36) == 65536
    assert local_ai.recommended_context(None) == 16384


def fake_ollama(monkeypatch, handler):
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(local_ai.httpx, "AsyncClient", client)
    monkeypatch.setattr("app.providers.ollama_provider.httpx.AsyncClient", client)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    monkeypatch.setattr(local_ai, "ram_gb", lambda: 17.9)
    return TestClient(main.app)


def test_status_and_pull_progress(client, monkeypatch):
    pulled = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in pulled]})
        if request.url.path == "/api/pull":
            pulled.append(json.loads(request.content)["model"])
            lines = [
                {"status": "pulling manifest"},
                {"status": "pulling a", "digest": "sha256:a", "total": 300, "completed": 0},
                {"status": "pulling b", "digest": "sha256:b", "total": 100, "completed": 100},
                {"status": "pulling a", "digest": "sha256:a", "total": 300, "completed": 300},
                {"status": "verifying sha256 digest"},
                {"status": "success"},
            ]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        return httpx.Response(404)

    fake_ollama(monkeypatch, handler)
    status = client.get("/api/ollama/status").json()
    assert status["running"] is True and status["recommended"] == "qwen3.5:9b" and status["ram_gb"] == 17.9
    assert not any(o["installed"] for o in status["options"])

    events = [json.loads(x) for x in client.post("/api/ollama/pull", json={"model": "qwen3.5:9b"}).text.splitlines()]
    percents = [e["percent"] for e in events if e["type"] == "progress" and e["percent"] is not None]
    assert percents[-1] == 100.0 and 25.0 in percents  # 100 / 400 octets après le 2e fichier
    assert events[-1] == {"type": "done", "model": "qwen3.5:9b"}

    status = client.get("/api/ollama/status").json()
    assert next(o for o in status["options"] if o["name"] == "qwen3.5:9b")["installed"] is True


def test_pull_reports_ollama_errors(client, monkeypatch):
    fake_ollama(monkeypatch, lambda request: httpx.Response(200, content=b'{"error": "disque plein"}'))
    events = [json.loads(x) for x in client.post("/api/ollama/pull", json={"model": "qwen3.5:4b"}).text.splitlines()]
    assert events == [{"type": "error", "message": "Téléchargement impossible : disque plein"}]


def test_pull_when_ollama_is_off(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    fake_ollama(monkeypatch, handler)
    assert client.get("/api/ollama/status").json()["running"] is False
    events = [json.loads(x) for x in client.post("/api/ollama/pull", json={"model": "qwen3.5:4b"}).text.splitlines()]
    assert events[-1]["type"] == "error" and "ouvre l'app Ollama" in events[-1]["message"]


def test_pull_rejects_unknown_model(client):
    assert client.post("/api/ollama/pull", json={"model": "llama-inconnu"}).status_code == 400


def test_thinking_is_off_by_default_and_only_sent_to_thinking_models(client, monkeypatch):
    import asyncio

    from app.providers import ollama_provider

    monkeypatch.setattr(ollama_provider, "_thinking_models", {})
    sent = []

    def handler(request):
        if request.url.path == "/api/show":
            name = json.loads(request.content)["model"]
            return httpx.Response(200, json={"capabilities": ["completion"] + (["thinking"] if "qwen3" in name else [])})
        body = json.loads(request.content)
        sent.append((body["model"], body.get("think", "absent")))
        return httpx.Response(200, json={"message": {"content": json.dumps({"chapters": []})}})

    fake_ollama(monkeypatch, handler)

    def ask(model):
        asyncio.run(ollama_provider.ask("système", {"type": "object"}, "demande", model))

    assert client.get("/api/settings").json()["local_thinking"] is False
    ask("qwen3:14b")
    ask("qwen2.5:7b")
    assert sent == [("qwen3:14b", False), ("qwen2.5:7b", "absent")]

    assert client.put("/api/settings", json={"local_thinking": True}).json()["local_thinking"] is True
    ask("qwen3:14b")
    assert sent[-1] == ("qwen3:14b", True)
    client.put("/api/settings", json={"local_thinking": False})


def test_claude_is_hidden_for_now(client):
    config = client.get("/api/config").json()
    assert config["claude"]["enabled"] is False and config["default_provider"] == "local"
    assert client.get("/api/settings").json()["claude_enabled"] is False
    cid = client.post("/api/courses", json={"name": "Bio"}).json()["id"]
    client.post(f"/api/courses/{cid}/files", files=[("files", ("a.txt", b"du texte", "text/plain"))])
    response = client.post(f"/api/courses/{cid}/quizzes", data={"provider": "claude"})
    assert response.status_code == 400


def test_context_is_automatic_for_18_gb_and_can_be_chosen(client, monkeypatch):
    import asyncio

    from app.providers import ollama_provider

    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    monkeypatch.delenv("OLLAMA_CHUNK_CHARS", raising=False)
    monkeypatch.setattr(ollama_provider, "_thinking_models", {"qwen3.5:9b": False})
    sent = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})
        sent.append(json.loads(request.content)["options"]["num_ctx"])
        return httpx.Response(200, json={"message": {"content": "{}"}})

    fake_ollama(monkeypatch, handler)
    monkeypatch.setattr(local_ai, "ram_gb", lambda: 17.9)
    ask = lambda: asyncio.run(ollama_provider.ask("s", {"type": "object"}, "d", "qwen3.5:9b"))

    context = client.get("/api/ollama/status").json()["context"]
    assert context["auto"] == context["used"] == 32768 and context["chosen"] is None
    assert [o["too_big"] for o in context["options"]] == [False, False, False, True]
    ask()
    assert sent[-1] == 32768 and ollama_provider.chunk_chars() == 49152

    client.put("/api/settings", json={"local_context": 16384})
    assert client.get("/api/ollama/status").json()["context"]["used"] == 16384
    ask()
    assert sent[-1] == 16384
    assert client.put("/api/settings", json={"local_context": 12345}).status_code == 400
    client.put("/api/settings", json={"local_context": 0})   # retour à l'automatique
    assert client.get("/api/ollama/status").json()["context"] == context
