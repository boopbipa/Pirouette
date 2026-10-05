import io
import json
import os
import subprocess
import sys
from pathlib import Path

from app import claude_desktop, mcp_server
from app.storage import Store

TEXT = ("Chapitre 1 : Le neurone\nLe neurone est la cellule de base du système nerveux. "
        "Il transmet l'influx nerveux grâce à la synapse chimique.\n\n"
        "Chapitre 2 : La myéline\nLa gaine de myéline accélère la conduction de l'influx nerveux le long de l'axone.\n")


def _store(tmp_path):
    store = Store(tmp_path / "data")
    course = store.create_course("Neuro")
    store.add_file(course["id"], "neuro.txt", TEXT.encode(), TEXT,
                   [{"title": "Chapitre 1 : Le neurone", "line": 0}, {"title": "Chapitre 2 : La myéline", "line": 3}], 2)
    return store, course["id"]


def _talk(store, messages):
    out = io.BytesIO()
    mcp_server.serve(store, io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages)), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _call(store, name, **arguments):
    reply = _talk(store, [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}])[0]
    result = reply["result"]
    return result["content"][0]["text"], result["isError"]


def test_handshake_and_tool_list(tmp_path):
    store, _ = _store(tmp_path)
    replies = _talk(store, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
    ])
    assert [r["id"] for r in replies] == [1, 2, 3, 4]  # pas de réponse à la notification
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[0]["result"]["serverInfo"]["name"] == "pirouette"
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names == ["pirouette_cours", "pirouette_lire", "pirouette_decouper", "pirouette_creer_quiz", "pirouette_ajouter_cartes", "pirouette_figure", "pirouette_difficultes"]
    assert replies[3]["error"]["code"] == -32601


def test_list_and_read(tmp_path):
    store, cid = _store(tmp_path)
    listing, error = _call(store, "pirouette_cours")
    fid = store.get_course(cid)["files"][0]["id"]
    assert not error and cid in listing and f"clé {fid}-1" in listing and "Sans semestre" in listing
    text, error = _call(store, "pirouette_lire", cours=cid, chapitres=[f"{fid}-1"])
    assert not error and "myéline" in text and "synapse" not in text
    missing, error = _call(store, "pirouette_lire", cours="000000000000")
    assert error and "introuvable" in missing


def test_create_quiz_checks_every_question(tmp_path):
    store, cid = _store(tmp_path)
    fid = store.get_course(cid)["files"][0]["id"]
    good = {"type": "qcm", "kind": "cours", "question": "Quel est le rôle de la gaine de myéline ?",
            "choices": ["Accélérer la conduction", "Produire l'ATP", "Stocker le calcium", "Relier deux neurones"],
            "answer": "Accélérer la conduction", "explanation": "Elle accélère l'influx le long de l'axone.",
            "source": "La gaine de myéline accélère la conduction de l'influx nerveux le long de l'axone."}
    invented = dict(good, question="Quelle maladie détruit la myéline ?", answer="La sclérose en plaques",
                    choices=["La sclérose en plaques", "Le diabète", "L'asthme", "La grippe"],
                    source="La sclérose en plaques est une maladie auto-immune qui détruit la myéline.")
    broken = dict(good, question="Question sans bonne réponse ?", answer="Rien de tout ça")
    text, error = _call(store, "pirouette_creer_quiz", cours=cid, titre="La myéline", chapitres=[f"{fid}-1"],
                        questions=[good, invented, broken])
    assert not error and "1 question." in text
    assert "question 2" in text and "n'est pas une phrase du cours" in text and "question 3" in text
    quiz = store.get_quiz(store.list_quizzes(cid)[0]["id"])
    assert quiz["title"] == "La myéline" and quiz["provider"] == "claude-app" and quiz["scope"] == ["Chapitre 2 : La myéline"]
    # La même question dans un nouveau quiz : doublon
    again, error = _call(store, "pirouette_creer_quiz", cours=cid, titre="Encore", questions=[good])
    assert error and "déjà posée" in again


def test_add_cards_and_difficulties(tmp_path):
    store, cid = _store(tmp_path)
    cards = [{"recto": "Rôle de la myéline ?", "verso": "Accélérer la conduction de l'influx nerveux",
              "source": "La gaine de myéline accélère la conduction de l'influx nerveux le long de l'axone."},
             {"recto": "Inventée ?", "verso": "Le cervelet coordonne les mouvements",
              "source": "Le cervelet coordonne les mouvements volontaires."}]
    text, error = _call(store, "pirouette_ajouter_cartes", cours=cid, cartes=cards)
    assert not error and text.startswith("1 flashcard ajoutée") and "Inventée" in text
    assert store.get_doc(cid, "cards")["cards"][0]["front"] == "Rôle de la myéline ?"
    text, error = _call(store, "pirouette_difficultes", cours=cid)
    assert not error and "Rien de difficile" in text


def test_claude_desktop_config_keeps_other_tools(tmp_path):
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"autre": {"command": "x"}}, "theme": "dark"}))
    assert claude_desktop.status(path) == {"installed": False, "claude_found": True, "config": str(path)}
    assert claude_desktop.install(path)["installed"]
    data = json.loads(path.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["autre"] == {"command": "x"}
    assert data["mcpServers"]["pirouette"]["args"][-1] == "--mcp"
    assert not claude_desktop.uninstall(path)["installed"]
    assert json.loads(path.read_text())["mcpServers"] == {"autre": {"command": "x"}}
    path.write_text("{pas du json")
    try:
        claude_desktop.install(path)
        raise AssertionError("un fichier illisible ne doit pas être écrasé")
    except ValueError:
        assert path.read_text() == "{pas du json"


def test_desktop_mcp_mode_speaks_only_json(tmp_path):
    """Lancé par l'app Claude : la sortie standard ne doit contenir que les réponses (sinon la connexion casse)."""
    store, _ = _store(tmp_path)
    root = Path(__file__).resolve().parent.parent
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "pirouette_cours", "arguments": {}}}]
    run = subprocess.run([sys.executable, str(root / "desktop.py"), "--mcp"], cwd=root, timeout=60,
                         input="".join(json.dumps(m) + "\n" for m in messages).encode(), capture_output=True,
                         env=os.environ | {"QUIZZ_DATA_DIR": str(store.root)})
    lines = run.stdout.decode().splitlines()
    assert len(lines) == 2, run.stderr.decode()
    assert "Neuro" in json.loads(lines[1])["result"]["content"][0]["text"]


def test_complete_an_existing_quiz(tmp_path):
    store, cid = _store(tmp_path)
    first = {"type": "reponse_courte", "kind": "cours", "question": "Quelle cellule est la base du système nerveux ?",
             "choices": [], "answer": "Le neurone", "explanation": "", "source": "Le neurone est la cellule de base du système nerveux."}
    text, error = _call(store, "pirouette_creer_quiz", cours=cid, titre="Tout le cours", questions=[first])
    quiz_id = store.list_quizzes(cid)[0]["id"]
    assert not error and f"Identifiant du quiz : {quiz_id}" in text
    second = {"type": "reponse_courte", "kind": "cours", "question": "Qu'accélère la gaine de myéline ?", "choices": [],
              "answer": "La conduction de l'influx nerveux", "explanation": "",
              "source": "La gaine de myéline accélère la conduction de l'influx nerveux le long de l'axone."}
    text, error = _call(store, "pirouette_creer_quiz", cours=cid, titre="x", questions=[second], quiz=quiz_id)
    assert not error and "1 question ajoutée" in text and "2 questions en tout" in text
    assert len(store.list_quizzes(cid)) == 1
    missing, error = _call(store, "pirouette_creer_quiz", cours=cid, titre="x", questions=[second], quiz="000000000000")
    assert error and "introuvable" in missing


def test_claude_splits_an_unsplit_file(tmp_path):
    store = Store(tmp_path / "data")
    cid = store.create_course("Neuro")["id"]
    store.add_file(cid, "neuro.txt", TEXT.encode(), TEXT, [], 1, chapters_by="none")
    fid = store.get_course(cid)["files"][0]["id"]
    listing, _ = _call(store, "pirouette_cours")
    assert f"pas encore découpé en chapitres : pirouette_decouper) — clé {fid}" in listing
    # Une ligne de début qui n'existe pas : rien n'est enregistré
    _, error = _call(store, "pirouette_decouper", cours=cid, fichier=fid,
                     chapitres=[{"titre": "Le neurone", "debut": "Chapitre 1 : Le neurone"}, {"titre": "X", "debut": "Chapitre 9"}])
    assert error and store.get_course(cid)["files"][0]["chapters_by"] == "none"
    # Lignes recopiées (ponctuation et casse près) : les chapitres sont enregistrés, avec leurs clés
    text, error = _call(store, "pirouette_decouper", cours=cid, fichier=fid,
                        chapitres=[{"titre": "Le neurone", "debut": "Chapitre 1 : Le neurone"},
                                   {"titre": "La myéline", "debut": "chapitre 2 - la myéline"}])
    entry = store.get_course(cid)["files"][0]
    assert not error and f"{fid}-1 : La myéline" in text
    assert entry["chapters_by"] == "ai" and [c["title"] for c in entry["chapters"]] == ["Le neurone", "La myéline"]
    read, _ = _call(store, "pirouette_lire", cours=cid, chapitres=[f"{fid}-1"])
    assert "myéline" in read and "synapse" not in read
    # Déjà découpé : on garde ses chapitres
    again, error = _call(store, "pirouette_decouper", cours=cid, fichier=fid,
                         chapitres=[{"titre": "A", "debut": "Chapitre 1 : Le neurone"}, {"titre": "B", "debut": "Chapitre 2 : La myéline"}])
    assert error and "déjà découpé" in again
