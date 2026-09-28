"""Plan de révision d'un semestre (ou d'un cours sans semestre) et rétroplanning jusqu'aux partiels.

Plan : « une séance tous les N jours, de M minutes ». Le temps est découpé en périodes de N jours à partir du début du
plan ; une période est « tenue » si l'on a révisé au moins une fois pendant. On en tire la prochaine séance et la
régularité (« 4 séances tenues sur 5 »). Chaque séance commence par les cartes les plus difficiles.

Rétroplanning : de la date du jour à la semaine des partiels, une séance tous les N jours. D'abord les chapitres,
répartis sur les premières séances ; puis la consolidation (erreurs, cartes difficiles) ; enfin des partiels blancs.
"""

from __future__ import annotations

from datetime import date, timedelta

RHYTHMS = (1, 2, 3, 7)        # une séance tous les … jours
MINUTES = (15, 30, 45)        # durée d'une séance
CARDS_PER_MINUTE = 2          # ≈ 30 s par carte
QUESTIONS_PER_MINUTE = 1 / 3  # une question de quiz toutes les 3 minutes


def check(every: int, minutes: int) -> None:
    if every not in RHYTHMS:
        raise ValueError("Rythme inconnu.")
    if minutes not in MINUTES:
        raise ValueError("Durée de séance inconnue.")


def session_size(minutes: int) -> tuple[int, int]:
    """(cartes, questions) d'une séance."""
    return minutes * CARDS_PER_MINUTE, max(1, round(minutes * QUESTIONS_PER_MINUTE))


def status(plan: dict, active_days: set[date], today: date) -> dict:
    """Où en est le plan : séance à faire aujourd'hui ou prochaine date, séances tenues, série en cours."""
    every = plan["every"]
    start = min(date.fromisoformat(plan["start"]), today)
    current = (today - start).days // every
    window = lambda i: {start + timedelta(days=i * every + d) for d in range(every)}  # noqa: E731
    kept = [bool(window(i) & active_days) for i in range(current + 1)]
    done_now = kept[-1]
    counted = kept if done_now else kept[:-1]  # la période en cours ne compte qu'une fois tenue
    streak = 0
    for ok in reversed(counted):
        if not ok:
            break
        streak += 1
    window_end = start + timedelta(days=(current + 1) * every - 1)
    return {
        "every": every, "minutes": plan["minutes"], "start": start.isoformat(),
        "done_today": done_now,
        "next": (window_end + timedelta(days=1)).isoformat() if done_now else today.isoformat(),
        "deadline": window_end.isoformat(),
        "kept": sum(counted), "total": len(counted), "streak": streak,
        "message": message(sum(counted), len(counted), streak, done_now),
    }


def message(kept: int, total: int, streak: int, done_now: bool) -> str:
    if total == 0:
        return "Première séance : c'est parti."
    if done_now and streak >= 3:
        return f"{streak} séances d'affilée : ta régularité paie."
    if done_now:
        return "Séance faite. À la prochaine !"
    if streak >= 2:
        return f"{streak} séances d'affilée : ne casse pas la série."
    if kept < total:
        return "Une séance aujourd'hui et tu repars du bon pied."
    return "Garde le rythme : une séance aujourd'hui."


# ---------- Rétroplanning ----------

def session_days(today: date, exam: date, every: int) -> list[date]:
    days, day = [], today
    while day < exam:
        days.append(day)
        day += timedelta(days=every)
    return days


def build_retro(today: date, exam: date, every: int, units: list[dict], courses: list[dict]) -> list[dict]:
    """Séances datées. `units` : chapitres à voir [{course_id, course, key, title}] ; `courses` : [{id, name}]."""
    days = session_days(today, exam, every)
    n = len(days)
    if not n:
        return []
    blanc = min(len(courses), max(1, n // 5)) if n >= 3 and courses else 0
    consolidate = n // 4 if n >= 4 else 0
    learn = max(1, n - blanc - consolidate)
    if learn + consolidate + blanc > n:  # très peu de séances : on garde surtout les chapitres
        consolidate = max(0, n - learn - blanc)
    slots: list[list[dict]] = [[] for _ in range(learn)]
    for i, unit in enumerate(units):
        slots[i * learn // len(units)].append(unit)
    sessions = [{"kind": "learn" if slot else "review", "units": slot} for slot in slots]
    sessions += [{"kind": "consolidate", "units": []} for _ in range(consolidate)]
    sessions += [{"kind": "blanc", "units": [], "course_id": c["id"], "course": c["name"]} for c in courses[:blanc]]
    return [s | {"date": d.isoformat(), "done": False} for d, s in zip(days, sessions)]
