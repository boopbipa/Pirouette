"""Vérifie qu'une question ou une carte vient bien du cours, et pas des connaissances générales du modèle.

Le modèle recopie la phrase du cours qui contient la réponse (« source ») ; on vérifie :
1. que cette phrase existe dans les chapitres choisis (à quelques mots près : ponctuation, coupure de ligne…) ;
2. que les mots importants de la réponse se trouvent dans ces chapitres (un modèle peut citer une vraie phrase
   sur le stress puis répondre « axe hypothalamo-hypophysaire », absent du cours).
"""

from __future__ import annotations

import re
import unicodedata

# Mots trop courants pour dire quoi que ce soit de la notion.
COMMON = set("""
alors aussi autre autres avec avoir comme dans donc elle elles entre est etre fait faire leur leurs mais meme
moins notamment nous peut plus pour quand quel quelle quels quelles sans selon sont sous tous tout toute toutes
tres vers votre cela cette ceux chez dont etait ainsi apres avant lors celui celle principalement principal
principale surtout generalement souvent toujours jamais aucun aucune chaque plusieurs certains certaines
""".split())

QUOTE_MATCH = 0.6     # part des groupes de 3 mots de la citation retrouvés dans le cours
ANSWER_MATCH = 0.5    # part des mots importants de la réponse présents dans le cours


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _stem(word: str) -> str:
    # « sécrétions » / « sécrétion », « hormonal » / « hormonale » : on compare les débuts de mots.
    return word[:6]


def _trigrams(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def content_words(text: str) -> set[str]:
    return {w for w in normalize(text).split() if len(w) >= 4 and w not in COMMON and not w.isdigit()}


class Grounding:
    def __init__(self, course_text: str):
        self.text = normalize(course_text)
        words = self.text.split()
        self.stems = {_stem(w) for w in words}
        self.trigrams = _trigrams(words)

    def quote_found(self, quote: str) -> bool:
        q = normalize(quote)
        words = q.split()
        if len(words) < 3:
            return False  # trop court pour prouver quoi que ce soit
        if q in self.text:
            return True
        grams = _trigrams(words)
        return len(grams & self.trigrams) / len(grams) >= QUOTE_MATCH

    def words_found(self, text: str, ratio: float = ANSWER_MATCH) -> bool:
        words = content_words(text)
        if not words:
            return True  # « Vrai », « 1905 »… : rien à vérifier
        found = sum(_stem(w) in self.stems for w in words)
        return found / len(words) >= ratio

    def check_question(self, question: dict) -> bool:
        if not self.quote_found(question.get("source", "")):
            return False
        return question["type"] == "vrai_faux" or self.words_found(question["answer"])

    def check_card(self, card: dict) -> bool:
        return self.quote_found(card.get("source", "")) and self.words_found(card["back"], 0.4)
