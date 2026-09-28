from app.revision import build_cards_prompt, is_duplicate, normalize_cards


def test_normalize_cards_limit_and_ids():
    cards = normalize_cards([{"cards": [{"front": "Q1 ?", "back": "R1"}, {"front": "q1", "back": "dup"},
                                        {"front": "Q2 ?", "back": "R2"}, {"front": "Q3 ?", "back": "R3"}]}], 2)
    assert [c["front"] for c in cards] == ["Q1 ?", "Q2 ?"]
    assert len({c["id"] for c in cards}) == 2


def test_rephrased_cards_are_duplicates():
    existing = [{"front": "Qu'est-ce que la plasticité cérébrale ?",
                 "back": "La capacité du cerveau à se modifier avec l'expérience."}]
    assert is_duplicate("Définis la plasticité cérébrale.", "Autre formulation.", existing)
    assert is_duplicate("Que désigne le terme « plasticité cérébrale » ?", "…", existing)
    assert is_duplicate("Plasticité du cerveau ?", "La capacité du cerveau à se modifier avec l'expérience", existing)
    # Une autre notion sur le même sujet n'est pas un doublon
    assert not is_duplicate("Quel est le rôle de la plasticité cérébrale dans l'apprentissage ?", "Elle permet…", existing)
    assert not is_duplicate("Qu'est-ce que la mémoire de travail ?", "…", existing)


def test_normalize_cards_skips_cards_already_in_deck():
    existing = [{"front": "Qu'est-ce que l'ATP ?", "back": "Molécule énergétique."}]
    cards = normalize_cards([{"cards": [{"front": "Définis l'ATP", "back": "x"}, {"front": "Le stroma ?", "back": "y"}]}],
                            5, existing)
    assert [c["front"] for c in cards] == ["Le stroma ?"]


def test_cards_prompt_lists_existing_cards():
    assert "existent déjà" in build_cards_prompt("cours", 5, "français", avoid=["Q1 ?"])
    assert "existent déjà" not in build_cards_prompt("cours", 5, "français")
