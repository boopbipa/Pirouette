"""Les révisions espacées de chaque cours (fichier d'une matière) : le jour où on l'a eu, puis 1, 3, 7, 15 et 30 jours
après, puis tous les 30 jours. C'est la répétition espacée (Ebbinghaus, Cepeda et al. 2008) appliquée au cours entier,
en se testant plutôt qu'en relisant (Roediger et Karpicke 2006) : chaque révision est un petit quiz sur le cours.

Une révision ratée (moins de 60 %) revient le lendemain, sans avancer ; faite en retard, la suivante garde son écart
(J+7 faite à J+9 : la suivante 8 jours plus tard, pas 6). Le suivi est gardé dans le fichier : `entry["rev"]` =
{"start": jour du cours, "step": révisions réussies, "due": prochaine date, "last": dernière faite, "score": en %}."""

from __future__ import annotations

from datetime import date, timedelta

OFFSETS = [0, 1, 3, 7, 15, 30]  # jours après le cours
EVERY_AFTER = 30                # ensuite, une fois par mois
PASS = 0.6                      # en dessous : la révision est à refaire le lendemain


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _offset(step: int) -> int:
    if step < len(OFFSETS):
        return OFFSETS[step]
    return OFFSETS[-1] + (step - len(OFFSETS) + 1) * EVERY_AFTER


def _gap(step: int) -> int:
    """L'écart voulu entre la révision `step - 1` et la révision `step`."""
    return _offset(step) - _offset(step - 1) if step > 0 else 0


def ensure(entry: dict, today: date) -> dict:
    """Le suivi du fichier, créé s'il manque. Un cours ajouté aujourd'hui : 1ʳᵉ révision aujourd'hui. Un cours ajouté
    avant les révisions espacées : on le prend à l'étape où il en serait, sans rattrapage (sinon tout tomberait le
    même jour) ; au-delà de 30 jours, il rejoint le rythme mensuel, étalé sur les deux semaines à venir."""
    rev = entry.get("rev")
    if isinstance(rev, dict) and _day(rev.get("due")):
        return rev
    start = _day(entry.get("added_at")) or today
    elapsed = (today - start).days
    if elapsed <= 0:
        rev = {"start": start.isoformat(), "step": 0, "due": start.isoformat()}
    else:
        step = next((s for s, off in enumerate(OFFSETS) if off >= elapsed), None)
        if step is None:
            spread = 1 + sum(map(ord, str(entry.get("id", "")))) % 14
            rev = {"start": start.isoformat(), "step": len(OFFSETS), "due": (today + timedelta(days=spread)).isoformat()}
        else:
            rev = {"start": start.isoformat(), "step": step, "due": (start + timedelta(days=OFFSETS[step])).isoformat()}
    entry["rev"] = rev
    return rev


def done(entry: dict, today: date, score: float | None = None) -> dict:
    """Une révision faite aujourd'hui (`score` entre 0 et 1, ou None : juste relu) : la date de la suivante."""
    rev = ensure(entry, today)
    start = _day(rev["start"]) or today
    step = int(rev.get("step", 0))
    if score is not None and score < PASS:
        due = today + timedelta(days=1)  # à refaire demain, à la même étape
    else:
        step += 1
        due = max(start + timedelta(days=_offset(step)), today + timedelta(days=max(1, _gap(step))))
    rev.update(step=step, due=due.isoformat(), last=today.isoformat(),
               score=None if score is None else round(100 * score))
    return rev


def view(entry: dict, today: date) -> dict:
    """Ce que montre l'accueil : à faire aujourd'hui ? pourquoi (« Ton cours d'aujourd'hui », « 7 jours après »,
    « en retard ») ; faite aujourd'hui ? la suivante dans combien de jours."""
    rev = ensure(entry, today)
    due, start, last = _day(rev["due"]), _day(rev["start"]) or today, _day(rev.get("last"))
    step = int(rev.get("step", 0))
    after = (due - start).days
    if step == 0 and after <= 0:
        reason = "Ton cours d'aujourd'hui" if start == today else "1ʳᵉ révision"
    elif last and rev.get("score") is not None and rev["score"] < round(100 * PASS) and (due - last).days == 1:
        reason = "À refaire"
    elif step >= len(OFFSETS):
        reason = "Révision du mois"
    else:
        reason = f"{after} jour{'s' if after > 1 else ''} après le cours"
    late = (today - due).days
    return {"due": rev["due"], "start": rev["start"], "step": step, "reason": reason,
            "today": due <= today, "late": max(0, late), "done_today": last == today,
            "last": rev.get("last"), "score": rev.get("score"), "in_days": max(0, (due - today).days)}


KINDS = {".pdf": "PDF", ".doc": "Word", ".docx": "Word", ".odt": "Writer", ".rtf": "Texte", ".txt": "Texte",
         ".md": "Texte", ".ppt": "PowerPoint", ".pptx": "PowerPoint", ".key": "Keynote", ".pages": "Pages",
         ".odp": "Impress", ".png": "Image", ".jpg": "Image", ".jpeg": "Image", ".heic": "Image"}


def kind(name: str) -> str:
    """L'étiquette du bouton « Relire le cours » : PDF, Word, PowerPoint…"""
    dot = name.rfind(".")
    return KINDS.get(name[dot:].lower(), "") if dot >= 0 else ""
