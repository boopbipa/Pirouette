from app import main
from app.providers.ollama_provider import MAX_PER_CALL, _split_big
from app.quiz import COVER_MAX, COVER_MIN, QuizOptions, build_user_prompt, coverage_size


def test_coverage_size_follows_length_and_definitions():
    assert coverage_size("court " * 50) == COVER_MIN
    assert coverage_size("x" * 9000) == 20
    assert coverage_size("x" * 90000) == COVER_MAX
    assert coverage_size("x" * 3000, definitions=17) == 17  # une question par définition repérée


def test_cover_prompt_asks_for_the_whole_text():
    prompt = build_user_prompt("Le texte.", 20, QuizOptions(num_questions=20, cover=True))
    assert "Couvre tout le texte" in prompt
    assert "Couvre tout le texte" not in build_user_prompt("Le texte.", 10, QuizOptions())


def test_local_ai_gets_at_most_a_dozen_questions_per_call():
    text = ("Une phrase du cours sur la cellule. " * 30 + "\n\n") * 12
    plan = _split_big(text, 30)
    assert sum(n for _, n in plan) == 30 and all(n <= MAX_PER_CALL for _, n in plan) and len(plan) >= 3
    assert _split_big(text, 8) == [(text, 8)]


def test_bank_score_counts_the_whole_bank():
    small = {"count": 10, "known": 3, "best_score": {"score": 9, "total": 10}}
    bank = {"count": 40, "known": 10, "best_score": {"score": 10, "total": 10}}
    assert main._quiz_score(small) == 90 and main._quiz_score(bank) == 25
