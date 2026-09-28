from app.quiz import QuizOptions, assemble_quiz, chunk_text, normalize_question, plan_chunks

ALL = ["qcm", "vrai_faux", "reponse_courte"]


def test_chunk_text_respects_limit_and_keeps_content():
    text = "\n\n".join(f"Paragraphe {i} " + "x" * 80 for i in range(50))
    chunks = chunk_text(text, 500)
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_chunk_text_splits_huge_paragraph():
    chunks = chunk_text("a" * 1200, 500)
    assert [len(c) for c in chunks] == [500, 500, 200]


def test_plan_chunks_distributes_exact_total():
    plan = plan_chunks(["a" * 100, "b" * 300, "c" * 100], 10)
    assert sum(n for _, n in plan) == 10
    assert plan[1][1] > plan[0][1]


def test_plan_chunks_more_chunks_than_questions():
    plan = plan_chunks([str(i) * 10 for i in range(20)], 4)
    assert len(plan) == 4 and sum(n for _, n in plan) == 4


def test_normalize_qcm_letter_answer():
    q = normalize_question({"type": "qcm", "question": "Q ?", "choices": ["A1", "B1", "C1", "D1"],
                            "answer": "B", "explanation": ""}, ALL)
    assert q["answer"] == "B1"


def test_normalize_qcm_invalid_answer_dropped():
    q = normalize_question({"type": "qcm", "question": "Q ?", "choices": ["x", "y"], "answer": "z",
                            "explanation": ""}, ALL)
    assert q is None


def test_normalize_true_false():
    q = normalize_question({"type": "vrai_faux", "question": "Q ?", "choices": [], "answer": "true",
                            "explanation": ""}, ALL)
    assert q["answer"] == "Vrai" and q["choices"] == ["Vrai", "Faux"]


def test_normalize_rejects_disallowed_type():
    q = normalize_question({"type": "reponse_courte", "question": "Q ?", "choices": [], "answer": "a",
                            "explanation": ""}, ["qcm"])
    assert q is None


def test_assemble_dedupes_and_caps():
    raw = {"type": "reponse_courte", "question": "Qu'est-ce ?", "choices": [], "answer": "a", "explanation": ""}
    other = dict(raw, question="Autre ?")
    quiz = assemble_quiz([{"title": "T", "questions": [raw, raw]}, {"title": "", "questions": [other]}],
                         QuizOptions(num_questions=5), "fallback")
    assert quiz["title"] == "T"
    assert len(quiz["questions"]) == 2


def test_schema_only_allows_chosen_types():
    from app.quiz import QUIZ_SCHEMA, quiz_schema

    enum = lambda s: s["properties"]["questions"]["items"]["properties"]["type"]["enum"]
    assert enum(quiz_schema(["qcm"])) == ["qcm"]
    assert enum(QUIZ_SCHEMA) == ["qcm", "vrai_faux", "reponse_courte", "texte_a_trous"]  # le modèle commun reste intact


def test_prompt_lists_questions_to_avoid():
    from app.quiz import QuizOptions, build_user_prompt

    prompt = build_user_prompt("cours", 3, QuizOptions(avoid=["Où a lieu la photosynthèse ?"]))
    assert "existent déjà" in prompt and "Où a lieu la photosynthèse ?" in prompt
    assert "existent déjà" not in build_user_prompt("cours", 3, QuizOptions())


def test_giveaway_questions_are_rejected():
    from app.quiz import QuizOptions, assemble_quiz, giveaway

    def q(stem, answer="élagage synaptique", qtype="qcm"):
        return {"type": qtype, "question": stem, "answer": answer,
                "choices": ["synaptogenèse", "myélinisation", "élagage synaptique", "neurogenèse"]}

    # La fin « d' » annonce un mot qui commence par une voyelle : une seule proposition possible
    assert giveaway(q("Lors de la maturation cérébrale chez l'enfant, il y a une phase d'"))
    assert giveaway(q("La phase qui suit la synaptogenèse est l’"))
    assert giveaway(q("Chez l'enfant, on observe une"))
    # L'énoncé contient déjà la réponse
    assert giveaway(q("Qu'appelle-t-on élagage synaptique, cette élimination des synapses ?"))
    # Bonnes questions
    assert not giveaway(q("Comment appelle-t-on l'élimination des synapses peu utilisées pendant l'enfance ?"))
    assert not giveaway(q("L'élagage synaptique a lieu pendant l'enfance.", "Vrai", "vrai_faux"))

    parts = [{"questions": [
        {**q("Lors de la maturation cérébrale, il y a une phase d'"), "explanation": "", "source": ""},
        {**q("Comment appelle-t-on l'élimination des synapses peu utilisées ?"), "explanation": "", "source": ""},
    ]}]
    quiz = assemble_quiz(parts, QuizOptions(types=["qcm"]), "Neuro")
    assert len(quiz["questions"]) == 1 and quiz["rejected"] == ["Lors de la maturation cérébrale, il y a une phase d'"]


def test_questions_already_asked_in_previous_quizzes_are_replaced():
    from app.quiz import QuizOptions, assemble_quiz, build_user_prompt

    previous = [{"type": "qcm", "question": "Qu'est-ce que la plasticité cérébrale ?",
                 "answer": "La capacité du cerveau à se modifier avec l'expérience."},
                {"type": "vrai_faux", "question": "Le cortisol est libéré par les surrénales.", "answer": "Vrai"}]

    def q(stem, answer, qtype="qcm"):
        return {"type": qtype, "question": stem, "answer": answer, "explanation": "", "source": "",
                "choices": ["Vrai", "Faux"] if qtype == "vrai_faux" else [answer, "Autre chose", "Encore autre", "Rien"]}

    parts = [{"questions": [
        q("Définis la plasticité cérébrale.", "La capacité du cerveau à se modifier avec l'expérience."),  # reformulée
        q("Le cortisol est libéré par les glandes surrénales.", "Vrai", "vrai_faux"),                      # quasi identique
        q("Le sommeil consolide la mémoire.", "Vrai", "vrai_faux"),                                         # nouvelle
        q("Comment appelle-t-on la gaine qui entoure les axones ?", "La myéline"),                          # nouvelle
    ]}]
    quiz = assemble_quiz(parts, QuizOptions(types=["qcm", "vrai_faux"]), "Neuro", previous=previous)
    assert [x["question"] for x in quiz["questions"]] == ["Le sommeil consolide la mémoire.",
                                                          "Comment appelle-t-on la gaine qui entoure les axones ?"]
    assert len(quiz["rejected"]) == 2
    prompt = build_user_prompt("cours", 5, QuizOptions(avoid=[p["question"] for p in previous]))
    assert "quiz précédents" in prompt and "Qu'est-ce que la plasticité cérébrale ?" in prompt


def _q(i, kind):
    return {"type": "qcm", "kind": kind, "question": f"Question {i} sur la notion {i} ?", "answer": f"Réponse {i}",
            "choices": [f"Réponse {i}", "B", "C", "D"], "explanation": "", "source": ""}


def test_course_quota():
    from app.quiz import course_quota

    assert course_quota(10, 0.4) == 4 and course_quota(5, 0.7) == 4 and course_quota(3, 1.0) == 3
    assert course_quota(10, 0.7) == 7  # pas 8 à cause d'un arrondi flottant


def test_quiz_keeps_the_share_of_course_questions():
    from app.quiz import QuizOptions, assemble_quiz

    # 8 questions de réflexion d'abord, puis 5 de cours : il en faut au moins 7 de cours sur 10
    parts = [{"questions": [_q(i, "reflexion") for i in range(8)] + [_q(10 + i, "cours") for i in range(5)]}]
    quiz = assemble_quiz(parts, QuizOptions(num_questions=10, types=["qcm"], course_share=0.7), "Neuro")
    kinds = [q["kind"] for q in quiz["questions"]]
    assert kinds.count("cours") == 5 and kinds.count("reflexion") == 3  # 2 places gardées pour du cours
    assert quiz["missing_course"] == 2

    # Assez de questions de cours : le quiz est complet, avec le bon mélange
    parts = [{"questions": [_q(i, "reflexion") for i in range(8)] + [_q(10 + i, "cours") for i in range(8)]}]
    quiz = assemble_quiz(parts, QuizOptions(num_questions=10, types=["qcm"], course_share=0.4), "Neuro")
    kinds = [q["kind"] for q in quiz["questions"]]
    assert len(kinds) == 10 and kinds.count("cours") == 4 and quiz["missing_course"] == 0


def test_prompt_asks_for_course_questions():
    from app.quiz import QuizOptions, build_user_prompt

    prompt = build_user_prompt("cours", 10, QuizOptions(course_share=0.7))
    assert "Au moins 7 questions sur 10 sont des questions de cours" in prompt and "De quoi est composé" in prompt
    prompt = build_user_prompt("cours", 5, QuizOptions(course_share=1.0, types=["reponse_courte"]))
    assert "Toutes les questions sont des questions de cours" in prompt and "réécrire la définition" in prompt


def test_formatting_marks_are_removed():
    from app.quiz import plain_text

    assert plain_text("***GABA*** Inhibiteur, il aide à réduire le **stress** et l’**anxiété**") == \
        "GABA Inhibiteur, il aide à réduire le stress et l’anxiété"
    assert plain_text("*Italique* et 5 * 3 = 15") == "Italique et 5 * 3 = 15"
    q = normalize_question({"type": "qcm", "question": "Quel est le rôle du **GABA** ?", "choices": ["**Inhibiteur**", "Excitateur"],
                            "answer": "Inhibiteur", "explanation": "", "source": "***GABA*** Inhibiteur"}, ALL)
    assert q["question"] == "Quel est le rôle du GABA ?" and q["answer"] == "Inhibiteur" and q["source"] == "GABA Inhibiteur"


def test_explanations_start_directly():
    from app.quiz import strip_course_intro

    assert strip_course_intro("Le cours précise que les oligodendrocytes produisent la myéline.") == \
        "Les oligodendrocytes produisent la myéline."
    assert strip_course_intro("Selon le cours, le GABA est inhibiteur.") == "Le GABA est inhibiteur."
    assert strip_course_intro("Le cours stipule qu'il ralentit l'activité.") == "Il ralentit l'activité."
    assert strip_course_intro("Le cortex est la couche externe.") == "Le cortex est la couche externe."
