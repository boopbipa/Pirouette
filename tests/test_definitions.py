import json

from fastapi.testclient import TestClient

from app import main
from app.definitions import analyse, definitions_block, definitions_for, extract_definitions
from app.providers import ollama_provider
from app.storage import Store

# Le cours tel que Pirouette le lit (extrait d'un fichier Pages) : terme centré en gras + italique, puis sa définition.
COURS = """***La neuroscience***
C’est l’étude scientifique du système nerveux, tant du point de vue de sa structure que de son fonctionnement.
Plusieurs acteurs sont concernés : chercheurs, psychologues, biologistes, neurologues…

***Neuroanatomie***
C’est regarder la structure du cerveau. À l’échelle microscopique on regarde **à quoi il ressemble.**
L’outil d’observation du cerveau est l’IRM

***Neurophysiologie***
*imagerie fonctionnelle*
C’est l’étude du fonctionnement du cerveau. À l’échelle microscopique on regarde **comment il** fonctionne.
Les outils d’analyse du cerveau sont l’Électroencéphalographie (EEG) et l’IRM fonctionnelle

Les études du comportement et la psychologie cognitive font également partie des neurosciences.

***Comprendre les troubles psychologiques autrement***

***La dépression***
Longtemps expliquée uniquement en termes psychodynamiques ou cognitifs, on sait aujourd’hui qu’elle implique des dérégulations de circuits."""


def test_bold_italic_headings_are_definitions():
    found = extract_definitions(COURS, "gras_italique")
    assert [d["term"] for d in found] == ["La neuroscience", "Neuroanatomie", "Neurophysiologie", "La dépression"]
    assert found[0]["definition"].startswith("C’est l’étude scientifique du système nerveux")
    assert "Plusieurs acteurs" in found[0]["definition"]              # tout le paragraphe
    assert "à quoi il ressemble." in found[1]["definition"] and "**" not in found[1]["definition"]
    assert found[2]["definition"].startswith("imagerie fonctionnelle C’est l’étude du fonctionnement")
    assert "études du comportement" not in found[2]["definition"]      # s'arrête au paragraphe vide


def test_analyse_picks_the_right_rule():
    result = analyse(COURS)
    assert result["best"] == "gras_italique"
    counts = {r["id"]: len(r["definitions"]) for r in result["rules"]}
    assert counts["gras_italique"] == 4 and counts["gras"] == 0
    assert analyse("Juste du texte, sans définitions.")["best"] is None


def test_colon_rule_and_automatic_mode():
    text = "Synapse : zone de contact entre deux neurones qui transmet l'information.\nChapitre 2 : La suite du cours et le reste."
    assert [d["term"] for d in extract_definitions(text, "deux_points")] == ["Synapse"]
    assert len(definitions_for(COURS, None)) == 4          # automatique : la règle qui en trouve au moins 3
    assert definitions_for(COURS, "aucune") == []
    assert "- Neuroanatomie : C’est regarder la structure du cerveau" in definitions_block(COURS, "gras_italique")
    assert definitions_block("rien", "gras_italique") == ""


def test_calibration_api_and_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    result = client.post("/api/definitions/analyse", data={"text": COURS}).json()
    assert result["best"] == "gras_italique" and result["current"] == "auto"
    assert client.post("/api/definitions/analyse", data={"text": " "}).status_code == 400

    assert client.put("/api/settings", json={"definition_rule": "gras_italique"}).json()["definition_rule"] == "gras_italique"
    assert client.put("/api/settings", json={"definition_rule": "n'importe"}).status_code == 400

    prompts = []

    async def fake_generate(course_text, options, model=None, on_progress=None):
        from app.quiz import build_user_prompt

        prompts.append(build_user_prompt(course_text, options.num_questions, options))
        return [{"questions": []}]

    monkeypatch.setattr(ollama_provider, "generate", fake_generate)
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.post(f"/api/courses/{cid}/files", files=[("files", ("cours.md", COURS.encode(), "text/plain"))])
    course = client.get(f"/api/courses/{cid}").json()
    assert course["files"][0]["definitions"] == 4
    fid = course["files"][0]["id"]
    assert [d["term"] for d in client.get(f"/api/courses/{cid}/files/{fid}/definitions").json()][0] == "La neuroscience"
    client.post(f"/api/courses/{cid}/quizzes", data={"provider": "local", "num_questions": "4"})
    assert "repérées dans ce cours grâce à sa mise en forme" in prompts[0] and "- La dépression :" in prompts[0]


def test_old_files_are_read_again_with_formatting(tmp_path, monkeypatch):
    import io

    import docx

    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    document = docx.Document()
    run = document.add_paragraph().add_run("Synapse")
    run.bold = run.italic = True
    document.add_paragraph("Zone de contact entre deux neurones.")
    buffer = io.BytesIO()
    document.save(buffer)
    cid = client.post("/api/courses", json={"name": "Neuro"}).json()["id"]
    client.post(f"/api/courses/{cid}/files", files=[("files", ("cours.docx", buffer.getvalue(), "application/octet-stream"))])
    # Simule un fichier importé avec l'ancienne version (texte sans mise en forme)
    course = main.store.get_course(cid)
    fid = course["files"][0]["id"]
    main.store.replace_text(cid, fid, "Synapse\nZone de contact entre deux neurones.", 1)
    assert "***" not in main.store.file_text(cid, fid)
    client.get(f"/api/courses/{cid}")
    assert main.store.file_text(cid, fid).startswith("***Synapse***")
    assert main.store.get_course(cid)["files"][0]["extract_version"] == 2
