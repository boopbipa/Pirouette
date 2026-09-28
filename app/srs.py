"""Répétition espacée des flashcards (inspirée de SM-2, l'algorithme d'Anki).

Après chaque carte, on dit si on la savait : « À revoir », « Difficile », « Bien » ou « Facile ». Pirouette en déduit
quand la reposer : une carte bien sue revient dans 1 jour, puis 3, puis de plus en plus tard (×2,5 environ) ;
une carte oubliée revient le jour même et ses intervalles grandissent moins vite ensuite.

Champs d'une carte : `status` (new / review / known), `interval` (jours), `ease` (facteur de croissance),
`due` (date à laquelle la reposer), `lapses` (fois où elle a été oubliée), `reviews`, `last_reviewed`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

RATINGS = ("again", "hard", "good", "easy")
START_EASE, MIN_EASE, MAX_EASE = 2.5, 1.3, 3.2
MAX_INTERVAL = 365
NEW_PER_DAY = 20         # nouvelles cartes proposées au plus chaque jour dans la révision du jour
LEGACY_INTERVAL = 3      # carte « apprise » avant la répétition espacée : reposée 3 jours après la dernière révision


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def due_date(card: dict, today: date) -> date:
    """Quand reposer la carte. Nouvelle ou à revoir : maintenant."""
    due = _parse_day(card.get("due"))
    if due:
        return due
    if card.get("status") == "known":
        last = _parse_day(card.get("last_reviewed")) or today
        return last + timedelta(days=LEGACY_INTERVAL)
    return today


def is_new(card: dict) -> bool:
    return not card.get("reviews")


def is_due(card: dict, today: date) -> bool:
    return due_date(card, today) <= today


def _intervals(card: dict) -> dict[str, int]:
    interval = card.get("interval")
    if interval is None:  # carte d'avant la répétition espacée
        interval = LEGACY_INTERVAL if card.get("status") == "known" else 0
    ease = card.get("ease", START_EASE)
    if interval <= 0:  # jamais sue : premiers intervalles fixes
        good, hard, easy = 1, 1, 4
    elif interval < 3:
        good, hard, easy = 3, 2, 5
    else:
        good = round(interval * ease)
        hard = max(interval + 1, round(interval * 1.2))
        easy = round(interval * ease * 1.3)
    cap = lambda n: max(1, min(MAX_INTERVAL, n))  # noqa: E731
    return {"again": 0, "hard": cap(hard), "good": cap(max(good, hard + 1)), "easy": cap(max(easy, good + 2))}


def preview(card: dict) -> dict[str, int]:
    """Dans combien de jours la carte reviendra, selon la réponse (pour l'afficher sur les boutons)."""
    return _intervals(card)


def exam_cap(days: int, today: date, exam: date | None) -> int:
    """Semaine des partiels : la carte doit revenir avant. À l'approche (2 semaines), les rappels se resserrent
    pour que tout soit revu plusieurs fois ; pendant la semaine, chaque jour."""
    if exam is None or days <= 0:
        return days
    left = (exam - today).days
    if left <= 0:
        return 1 if left > -7 else days  # semaine en cours : tous les jours ; passée : plus de contrainte
    if left <= 14:
        return min(days, max(1, round(left / 3)))
    return min(days, left - 1)


def schedule(card: dict, rating: str, now: datetime | None = None, exam: date | None = None) -> dict:
    """Met à jour la carte après une réponse. Renvoie la carte. `exam` : début de la semaine des partiels."""
    if rating not in RATINGS:
        raise ValueError(rating)
    now = now or datetime.now()
    days = exam_cap(_intervals(card)[rating], now.date(), exam)
    ease = card.get("ease", START_EASE)
    if rating == "again":
        ease -= 0.2
        card["lapses"] = card.get("lapses", 0) + (0 if is_new(card) else 1)
    elif rating == "hard":
        ease -= 0.15
    elif rating == "easy":
        ease += 0.15
    card.update(
        status="review" if rating == "again" else "known",
        interval=days,
        ease=round(min(MAX_EASE, max(MIN_EASE, ease)), 2),
        due=(now.date() + timedelta(days=days)).isoformat(),
        reviews=card.get("reviews", 0) + 1,
        last_reviewed=now.isoformat(timespec="seconds"),
        last_rating=rating,
    )
    return card


def is_weak(card: dict) -> bool:
    """Carte souvent oubliée, ou ratée à la dernière révision."""
    if not card.get("reviews"):
        return False
    return (card.get("last_rating") == "again" or card.get("status") == "review"
            or card.get("lapses", 0) >= 2 or card.get("ease", START_EASE) <= 2.1)


def weakness(card: dict) -> tuple:
    """Pour trier les points faibles : les plus oubliées d'abord."""
    return (-card.get("lapses", 0), card.get("ease", START_EASE), card.get("last_reviewed") or "")
