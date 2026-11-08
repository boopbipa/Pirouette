"""Les révisions dans l'agenda du Mac (calendrier « Pirouette ») : à quelle heure les placer, et comment garder
l'agenda à jour sans jamais défaire ce que tu y as changé.

- Chaque révision a une clé « matière:cours:date » (« …:avant » pour celle de la veille d'un cours), écrite dans la
  note de l'événement : c'est comme ça que Pirouette retrouve les siens.
- Placement : à l'heure choisie (18 h par défaut), après les cours de l'école s'ils débordent, les unes après les
  autres. Aujourd'hui : pas avant maintenant.
- Tu déplaces un événement dans Calendrier : Pirouette ne le remet jamais à sa place (il ne touche plus à l'heure).
- Tu le supprimes : la révision est remise au lendemain (et elle n'est pas recréée).
- Faite : l'événement reste, coché « ✓ ». Plus prévue (le rythme a changé) : supprimée, si elle n'est pas passée."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MARK = re.compile(r"\[pirouette:([0-9a-f]{12}):([0-9a-f]{8}):(\d{4}-\d{2}-\d{2})(:avant)?\]")
GAP = timedelta(minutes=5)
MASS_DELETE = 4  # tant d'événements disparus d'un coup : le calendrier a été supprimé ou vidé, pas des choix un à un


@dataclass
class Slot:
    key: str
    title: str
    notes: str
    start: datetime
    end: datetime


def key_of(course_id: str, file_id: str, day: str, before: bool = False) -> str:
    return f"{course_id}:{file_id}:{day}" + (":avant" if before else "")


def parse_key(key: str) -> tuple[str, str, str, bool]:
    parts = key.split(":")
    return parts[0], parts[1], parts[2], len(parts) > 3


def marker(key: str) -> str:
    return f"[pirouette:{key}]"


def key_in(notes: str | None) -> str | None:
    m = MARK.search(notes or "")
    return f"{m.group(1)}:{m.group(2)}:{m.group(3)}{m.group(4) or ''}" if m else None


def _round_up(moment: datetime, step: int = 5) -> datetime:
    moment = moment.replace(second=0, microsecond=0)
    extra = (-moment.minute) % step
    return moment + timedelta(minutes=extra)


def place(items: list[dict], busy: list[tuple[datetime, datetime]], hour: str, now: datetime) -> list[Slot]:
    """Les horaires des révisions. `items` : {course_id, file_id, day, before, course, title, minutes, reason}.
    `busy` : les cours de l'école (début, fin)."""
    h, m = (int(x) for x in hour.split(":"))
    out: list[Slot] = []
    by_day: dict[str, list[dict]] = {}
    for item in items:
        by_day.setdefault(item["day"], []).append(item)
    for day, todo in sorted(by_day.items()):
        cursor = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(hour=h, minute=m)
        if day == now.date().isoformat():
            cursor = max(cursor, _round_up(now + GAP))
        todo.sort(key=lambda i: (not i.get("before"), i["course"], i["title"]))
        for item in todo:
            length = timedelta(minutes=max(10, 5 * round(max(item["minutes"], 1) / 5)))
            moved = True
            while moved:  # après les cours qui chevauchent
                moved = False
                for start, end in busy:
                    if start < cursor + length and end > cursor:
                        cursor, moved = _round_up(end + GAP), True
            key = key_of(item["course_id"], item["file_id"], day, item.get("before", False))
            title = f"Réviser {item['course']} · {item['title']}"
            notes = f"{item['reason']} · environ {item['minutes']} min. Ouvre Pirouette pour faire le quiz.\n\n{marker(key)}"
            out.append(Slot(key, title, notes, cursor, cursor + length))
            cursor = cursor + length + GAP
    return out


DONE = "✓ "


def sync(calendar, slots: list[Slot], state: dict, is_done, now: datetime) -> dict:
    """Met l'agenda en accord avec les révisions prévues. `calendar` : events(début, fin) → [{id, key, title, start}],
    create(slot) → id, retitle(id, titre), delete(id). `state` : {"created": {clé: début}, "skipped": [clés]}.
    Renvoie {"postponed": [clés supprimées à la main], "created": n, "deleted": n, "done": n}."""
    today = now.date()
    found = {e["key"]: e for e in calendar.events(today - timedelta(days=1), today + timedelta(days=16)) if e.get("key")}
    created: dict = state.setdefault("created", {})
    skipped: list = state.setdefault("skipped", [])
    postponed = []
    gone = [k for k in created if k not in found and parse_key(k)[2] >= (today - timedelta(days=1)).isoformat()]
    if len(gone) >= MASS_DELETE:  # calendrier supprimé ou vidé : on repart de zéro, sans rien reporter
        for k in gone:
            created.pop(k, None)
        gone = []
    for k in gone:
        created.pop(k, None)
        if parse_key(k)[2] >= today.isoformat() and not is_done(k):
            postponed.append(k)
            skipped.append(k)
    wanted = {s.key for s in slots}
    stats = {"postponed": postponed, "created": 0, "deleted": 0, "done": 0}
    for slot in slots:
        if slot.key in skipped:
            continue
        event = found.get(slot.key)
        if event:
            created.setdefault(slot.key, event["start"].isoformat(timespec="minutes"))
            if event["title"] != slot.title and not event["title"].startswith(DONE):
                calendar.retitle(event["id"], slot.title)  # l'heure, elle, reste celle que tu as choisie
            continue
        calendar.create(slot)
        created[slot.key] = slot.start.isoformat(timespec="minutes")
        stats["created"] += 1
    for k, event in found.items():
        if k in wanted:
            continue
        if is_done(k):
            if not event["title"].startswith(DONE):
                calendar.retitle(event["id"], DONE + event["title"])
                stats["done"] += 1
        elif event["start"] > now:
            calendar.delete(event["id"])
            created.pop(k, None)
            stats["deleted"] += 1
    state["skipped"] = skipped[-300:]
    return stats
