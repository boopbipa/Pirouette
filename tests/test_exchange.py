from fastapi.testclient import TestClient

from app import exchange, main
from app.storage import Store

QUIZ = {"title": "La synapse", "scope": ["Chapitre 2 : La synapse"], "questions": [
    {"type": "qcm", "kind": "cours", "question": "Où passe le message nerveux ?", "choices": ["La synapse", "L'os", "Le muscle", "La peau"],
     "answer": "La synapse", "explanation": "La synapse relie deux neurones.", "source": "Le message passe par la synapse.", "key_terms": []},
    {"type": "vrai_faux", "kind": "cours", "question": "La synapse est chimique ?", "choices": ["Vrai", "Faux"], "answer": "Vrai",
     "explanation": "", "source": "", "key_terms": []},
    {"type": "reponse_courte", "kind": "cours", "question": "Quelle cellule transmet l'influx ?", "choices": [], "answer": "Le neurone",
     "explanation": "", "source": "", "key_terms": []},
    {"type": "texte_a_trous", "kind": "cours", "question": "Le neurone transmet l'_____ nerveux.", "choices": [], "answer": "influx",
     "explanation": "", "source": "", "key_terms": []},
]}


def test_quiz_round_trip():
    text = exchange.quiz_to_text(QUIZ, "Neuro")
    assert "B. L'os" in text and "A. La synapse  ✓" in text and "Chapitres : Chapitre 2 : La synapse" in text
    back = exchange.parse(text)
    assert back["kind"] == "quiz" and back["title"] == "La synapse" and back["chapters"] == ["Chapitre 2 : La synapse"]
    assert [q["type"] for q in back["questions"]] == ["qcm", "vrai_faux", "reponse_courte", "texte_a_trous"]
    assert back["questions"][0]["answer"] == "La synapse" and back["questions"][0]["explanation"] == "La synapse relie deux neurones."
    assert back["questions"][3]["answer"] == "influx"


def test_hand_written_quiz():
    text = """Mon quiz de neuro
1) Quel neurotransmetteur est excitateur ?
a) Le GABA
b) Le glutamate *
c) La glycine
2. Combien de lobes a le cortex ?
Réponse : 4
3. Le cervelet coordonne les mouvements
a) Vrai
b) Faux
Réponse : a
"""
    found = exchange.parse(text)
    assert found["kind"] == "quiz" and len(found["questions"]) == 3
    assert found["questions"][0]["answer"] == "Le glutamate" and found["questions"][0]["type"] == "qcm"
    assert found["questions"][1]["type"] == "reponse_courte" and found["questions"][1]["answer"] == "4"
    assert found["questions"][2]["type"] == "vrai_faux" and found["questions"][2]["answer"] == "Vrai"


def test_cards_from_other_apps():
    anki = exchange.cards_to_text([{"front": "GABA ?", "back": "Inhibiteur"}, {"front": "Glutamate ?", "back": "Excitateur"}], "Neuro")
    assert anki.startswith("#separator:tab") and "GABA ?\tInhibiteur" in anki
    assert exchange.parse(anki)["cards"] == [{"front": "GABA ?", "back": "Inhibiteur"}, {"front": "Glutamate ?", "back": "Excitateur"}]
    assert exchange.parse("Recto;Verso;Cours\nNeurone;Cellule nerveuse;Neuro\n")["cards"] == [{"front": "Neurone", "back": "Cellule nerveuse"}]
    assert exchange.parse("Myéline :: gaine isolante\nAxone :: prolongement\n")["cards"][1] == {"front": "Axone", "back": "prolongement"}
    assert exchange.parse("Q : Rôle du GABA ?\nR : Inhiber\n\nQ : Rôle de la myéline ?\nR : Accélérer\n")["cards"][1]["back"] == "Accélérer"
    assert exchange.parse("rien de reconnaissable ici")["kind"] is None


def test_export_and_import_api(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    filler = "Le neurone transmet l'influx nerveux grâce à la synapse chimique. " * 12
    text = f"Chapitre 1 : Le neurone\n{filler}\nChapitre 2 : La synapse\n{filler}"
    client.post(f"/api/courses/{cid}/files", files=[("files", ("neuro.txt", text.encode(), "text/plain"))])
    mine = store.save_quiz({**QUIZ, "course_id": cid, "course_name": "Neuro", "questions": QUIZ["questions"][:1]})
    response = client.get(f"/api/export/quiz/{mine['id']}")
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    # Un ami renvoie le quiz complet : il rejoint le quiz du chapitre, sans la question déjà présente
    friend = exchange.quiz_to_text(QUIZ, "Neuro").encode()
    result = client.post(f"/api/courses/{cid}/import", files=[("file", ("synapse.txt", friend, "text/plain"))]).json()
    assert result == {"kind": "quiz", "quiz_id": mine["id"], "title": "La synapse", "added": 3, "skipped": 1, "merged": True}
    cards = client.post(f"/api/courses/{cid}/import", files=[("file", ("c.txt", b"GABA\tInhibiteur\nGABA\tInhibiteur\n", "text/plain"))]).json()
    assert cards == {"kind": "cards", "added": 1, "skipped": 1}
    assert "GABA\tInhibiteur" in client.get(f"/api/export/cards/{cid}").text
    bad = client.post(f"/api/courses/{cid}/import", files=[("file", ("x.txt", b"bonjour", "text/plain"))])
    assert bad.status_code == 400
