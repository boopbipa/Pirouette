from app.grounding import is_logistics
from app.quiz import QuizOptions, assemble_quiz
from app.revision import normalize_cards


def test_course_organisation_is_not_something_to_learn():
    assert is_logistics("Rédaction d'un dossier : 30% de la note")
    assert is_logistics("Les modalités d'évaluation sont les suivantes")
    assert is_logistics("Contact : prof.dupont@univ-lyon2.fr")
    assert is_logistics("Contrôle continu et examen terminal")
    # Le contenu du cours, lui, passe (même avec des pourcentages ou le mot « examen »)
    assert not is_logistics("30 % des neurones du cortex sont des interneurones")
    assert not is_logistics("L'examen clinique repose sur l'observation du patient")
    assert not is_logistics("La mémoire de travail a une capacité limitée")


def test_generated_items_about_organisation_are_dropped():
    source = "Rédaction d'un dossier : 30% de la note"
    quiz = assemble_quiz([{"questions": [
        {"type": "reponse_courte", "kind": "cours", "question": "Quelle est la part de la note attribuée au dossier ?",
         "choices": [], "answer": "30%", "explanation": "", "source": source},
        {"type": "reponse_courte", "kind": "cours", "question": "Quelle capacité a la mémoire de travail ?",
         "choices": [], "answer": "Limitée", "explanation": "", "source": "La mémoire de travail a une capacité limitée."},
    ]}], QuizOptions(num_questions=5), "Quiz")
    assert [q["answer"] for q in quiz["questions"]] == ["Limitée"] and len(quiz["rejected"]) == 1

    class Anything:
        def check_card(self, card):
            return True

    rejected = []
    cards = normalize_cards([{"cards": [{"front": "Part du dossier ?", "back": "30% de la note", "source": source},
                                        {"front": "Mémoire de travail ?", "back": "Capacité limitée", "source": "x"}]}],
                            10, grounding=Anything(), rejected=rejected)
    assert [c["front"] for c in cards] == ["Mémoire de travail ?"] and rejected == ["Part du dossier ?"]
