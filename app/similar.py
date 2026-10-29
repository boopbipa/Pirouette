"""Un cours redéposé sous un autre nom (« CM1 v2.pdf » au lieu de « CM1.pdf ») : on le reconnaît à son texte."""

from __future__ import annotations

import re

SHINGLE = 5          # suites de 5 mots : les en-têtes et formules qui reviennent d'un cours à l'autre comptent peu
MIN_SHINGLES = 40    # un texte trop court ne dit rien
THRESHOLD = 0.5      # au moins la moitié du texte de l'un se retrouve dans l'autre


def _shingles(text: str) -> set[int]:
    words = re.findall(r"\w+", re.sub(r"\[Page \d+\]", " ", text).lower())
    return {hash(" ".join(words[i:i + SHINGLE])) for i in range(max(len(words) - SHINGLE + 1, 0))}


def resemblance(a: str, b: str) -> float:
    """Part du texte en commun, de 0 à 1 : la plus grande des deux parts (une nouvelle version qui ajoute des pages
    contient presque toute l'ancienne)."""
    sa, sb = _shingles(a), _shingles(b)
    if len(sa) < MIN_SHINGLES or len(sb) < MIN_SHINGLES:
        return 0.0
    common = len(sa & sb)
    return max(common / len(sa), common / len(sb))


def best_match(text: str, candidates: list[tuple[str, str]]) -> tuple[str, float] | None:
    """Parmi (identifiant, texte), celui qui ressemble assez au texte : (identifiant, part en commun), ou None."""
    best = max(((cid, resemblance(text, other)) for cid, other in candidates), key=lambda x: x[1], default=None)
    return best if best and best[1] >= THRESHOLD else None
