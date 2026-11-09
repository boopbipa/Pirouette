"""Les révisions dans l'agenda du Mac (calendrier « Pirouette ») : à quelle heure les placer, et comment garder
l'agenda à jour sans jamais défaire ce que tu y as changé.

- Chaque révision a une clé « matière:cours:date » (« …:avant » pour celle de la veille d'un cours), écrite dans la
  note de l'événement : c'est comme ça que Pirouette retrouve les siens.
- Placement : à l'heure choisie (18 h par défaut), après les cours de l'école s'ils débordent, les unes après les
  autres. Aujourd'hui : pas avant maintenant.
- Tu déplaces un événement dans Calendrier : Pirouette ne le remet jamais à sa place ; à un autre jour, Pirouette
  suit (la révision est prévue ce jour-là, dans l'accueil et le planning aussi). Tu le renommes : il garde ton titre.
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
    fixed: bool = False  # heure choisie (déplacée dans le planning de Pirouette ou dans Calendrier)


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
        midnight = datetime.combine(date.fromisoformat(day), datetime.min.time())
        cursor = midnight.replace(hour=h, minute=m)
        if day == now.date().isoformat():
            cursor = max(cursor, _round_up(now + GAP))
        todo.sort(key=lambda i: (not i.get("at"), not i.get("before"), i["course"], i["title"]))
        taken = list(busy)
        for item in todo:
            length = timedelta(minutes=max(5, 5 * round(max(item["minutes"], 1) / 5)))
            key = key_of(item["course_id"], item["file_id"], day, item.get("before", False))
            title = f"Réviser {item['course']}"
            notes = f"{item['reason']} · environ {item['minutes']} min. Ouvre Pirouette pour faire le quiz.\n\n{marker(key)}"
            if item.get("at"):  # à l'heure choisie : les autres se rangent autour
                ah, am = (int(x) for x in item["at"].split(":"))
                start = midnight.replace(hour=ah, minute=am)
                out.append(Slot(key, title, notes, start, start + length, fixed=True))
                taken.append((start, start + length))
                continue
            moved = True
            while moved:  # après les cours (et les révisions placées à la main) qui chevauchent
                moved = False
                for start, end in taken:
                    if start < cursor + length and end > cursor:
                        cursor, moved = _round_up(end + GAP), True
            out.append(Slot(key, title, notes, cursor, cursor + length))
            cursor = cursor + length + GAP
    return out


DONE = "✓ "


def sync(calendar, slots: list[Slot], state: dict, is_done, now: datetime) -> dict:
    """Met l'agenda en accord avec les révisions prévues. `calendar` : events(début, fin) → [{id, key, title, notes,
    start}], create(slot) → id, retitle(id, titre), renote(id, note), delete(id). `state` : {"created": {clé: début},
    "skipped": [clés]}. Renvoie {"postponed": [clés supprimées à la main], "moved": [(clé, nouveau jour)], …} :
    à Pirouette de reporter ou de suivre (puis de relancer un tour pour que tout concorde)."""
    today = now.date()
    # Large : une révision déplacée loin (dans un mois) ne doit pas passer pour supprimée
    found = {e["key"]: e for e in calendar.events(today - timedelta(days=31), today + timedelta(days=62)) if e.get("key")}
    created: dict = state.setdefault("created", {})
    skipped: list = state.setdefault("skipped", [])
    stats = {"postponed": [], "moved": [], "resized": [], "retimed": [], "created": 0, "deleted": 0, "done": 0}
    starts: dict = state.setdefault("starts", {})  # heure de début de chaque révision la dernière fois qu'on l'a vue
    lens: dict = state.setdefault("lens", {})  # durée (min) de chaque révision la dernière fois qu'on l'a vue

    # Déplacée à un autre jour dans Calendrier : Pirouette suit (la révision est prévue ce jour-là), et l'événement
    # prend la clé de son nouveau jour (sinon Pirouette en créerait un second)
    for k, event in list(found.items()):
        course_id, file_id, day, before = parse_key(k)
        new_day = event["start"].date().isoformat()
        if new_day == day or is_done(k):
            continue
        new_key = key_of(course_id, file_id, new_day, before)
        calendar.renote(event["id"], (event.get("notes") or "").replace(marker(k), marker(new_key)))
        found[new_key] = found.pop(k)
        if k in lens:
            lens[new_key] = lens.pop(k)
        starts.pop(k, None)
        created.pop(k, None)
        created[new_key] = event["start"].isoformat(timespec="minutes")
        if new_key in skipped:
            skipped.remove(new_key)
        stats["moved"].append((k, new_day))

    gone = [k for k in created if k not in found and parse_key(k)[2] >= (today - timedelta(days=1)).isoformat()]
    if len(gone) >= MASS_DELETE:  # calendrier supprimé ou vidé : on repart de zéro, sans rien reporter
        for k in gone:
            created.pop(k, None)
        gone = []
    for k in gone:
        created.pop(k, None)
        if parse_key(k)[2] >= today.isoformat() and not is_done(k):
            stats["postponed"].append(k)
            skipped.append(k)
    if stats["moved"] or stats["postponed"]:  # Pirouette ajuste d'abord ses dates, puis un nouveau tour écrit le reste
        state["skipped"] = skipped[-300:]
        return stats

    wanted = {s.key for s in slots}
    # Une révision pas faite qui change de jour (en retard, reportée) : on déplace son événement plutôt que d'en créer
    # un second (sinon : un la veille, pas fait, et un nouveau le jour d'après)
    def same(k):
        course_id, file_id, _, before = parse_key(k)
        return course_id, file_id, before
    stale: dict = {}
    for k, event in found.items():
        if k not in wanted and not is_done(k):
            stale.setdefault(same(k), []).append(k)
    for keys in stale.values():
        keys.sort(key=lambda k: found[k]["start"])
    for slot in slots:
        if slot.key in skipped:
            continue
        event = found.get(slot.key)
        if event:  # déjà là : le titre n'est pas touché (tu as pu le changer)
            # L'heure : déplacée dans Calendrier → Pirouette suit ; déplacée dans le planning de Pirouette → l'événement suit
            at = event["start"].strftime("%H:%M")
            if slot.key in starts and at != starts[slot.key]:
                stats["retimed"].append((slot.key, at))
            elif slot.fixed and event["start"] != slot.start:
                calendar.move(event["id"], slot.start, slot.start + (event["end"] - event["start"]))
                event["end"] = slot.start + (event["end"] - event["start"])
                event["start"] = slot.start
                at = slot.start.strftime("%H:%M")
            starts[slot.key] = at
            created[slot.key] = event["start"].isoformat(timespec="minutes")
            # La durée : étirée dans Calendrier → Pirouette suit ; changée dans Pirouette → l'événement suit
            in_calendar = round((event["end"] - event["start"]).total_seconds() / 60)
            wanted_len = round((slot.end - slot.start).total_seconds() / 60)
            if slot.key in lens and in_calendar != lens[slot.key]:
                stats["resized"].append((slot.key, in_calendar))
            elif in_calendar != wanted_len:
                calendar.resize(event["id"], event["start"] + (slot.end - slot.start))
                in_calendar = wanted_len
            lens[slot.key] = in_calendar
            continue
        old = stale.get(same(slot.key))
        if old:  # l'événement d'avant (le plus récent) part au nouveau jour, avec la nouvelle clé
            k = old.pop()
            event = found.pop(k)
            calendar.move(event["id"], slot.start, slot.end)
            calendar.renote(event["id"], (event.get("notes") or "").replace(marker(k), marker(slot.key)))
            event.update(start=slot.start, end=slot.end)
            found[slot.key] = event
            for book in (created, starts, lens):
                book.pop(k, None)
            starts[slot.key] = slot.start.strftime("%H:%M")
            lens[slot.key] = round((slot.end - slot.start).total_seconds() / 60)
            created[slot.key] = slot.start.isoformat(timespec="minutes")
            stats["moved_late"] = stats.get("moved_late", 0) + 1
            continue
        calendar.create(slot)
        starts[slot.key] = slot.start.strftime("%H:%M")
        lens[slot.key] = round((slot.end - slot.start).total_seconds() / 60)
        created[slot.key] = slot.start.isoformat(timespec="minutes")
        stats["created"] += 1
    wanted_files = {same(w) for w in wanted}
    # Les doublons déjà là (révision pas faite restée à son ancien jour alors qu'elle a un événement ailleurs) : retirés
    for keys in stale.values():
        for k in keys:
            if same(k) in wanted_files and k in found:
                calendar.delete(found.pop(k)["id"])
                for book in (created, starts, lens):
                    book.pop(k, None)
                stats["deleted"] += 1
    for k, event in found.items():
        if k in wanted:
            continue
        if is_done(k):
            if not event["title"].startswith(DONE):
                calendar.retitle(event["id"], DONE + event["title"])
                stats["done"] += 1
        elif event["start"] > now and parse_key(k)[2] <= (today + timedelta(days=16)).isoformat():
            calendar.delete(event["id"])  # plus prévue (le rythme a changé)
            created.pop(k, None)
            stats["deleted"] += 1
    state["skipped"] = skipped[-300:]
    for k in [k for k in lens if k not in found and k not in wanted]:
        lens.pop(k, None)
    for k in [k for k in starts if k not in found and k not in wanted]:
        starts.pop(k, None)
    return stats
