"""L'emploi du temps de l'école : un lien d'abonnement (.ics, « iCal ») relu toutes les heures. Chaque cours de
l'agenda est relié à une matière de Pirouette (reconnue par son nom, ou choisie une fois dans les Réglages) ; les
réunions et les cours sans matière ne donnent pas de révision.

Gardé dans data/agenda.json : le lien, la dernière lecture, les cours, les matières choisies (`map` : nom du cours
dans l'agenda → identifiant de matière, ou "none"), les séances déjà traitées après le cours (`processed`) et les
rappels « Tu as eu … » écartés (`dismissed`)."""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

STALE_AFTER = timedelta(minutes=55)   # relu à peu près toutes les heures
KEEP = timedelta(days=60)             # séances passées gardées (pour « Tu as eu … » et le planning)

# Mots qui ne disent rien de la matière (« CM », « TD », « S1 »…) : ignorés pour reconnaître le nom
FILLER = {"de", "du", "des", "la", "le", "les", "l", "d", "et", "en", "a", "au", "aux", "pour", "cm", "td", "tp", "ed",
          "cours", "s1", "s2", "s3", "s4", "s5", "s6", "ue", "l1", "l2", "l3", "m1", "m2", "1", "2", "3", "4", "i", "ii"}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def words(text: str) -> set[str]:
    return {w for w in norm(text).split() if w not in FILLER}


# ---------- Lecture du fichier .ics ----------
def _unescape(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _when(prop: str, value: str) -> tuple[datetime | None, bool]:
    """Une date de l'agenda en heure locale (sans fuseau) ; et si c'est une journée entière."""
    value = value.strip()
    try:
        if "VALUE=DATE" in prop.upper() and "T" not in value:
            return datetime.strptime(value[:8], "%Y%m%d"), True
        if value.endswith("Z"):
            utc = datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            return utc.astimezone().replace(tzinfo=None), False
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S"), False  # TZID=Europe/Paris : l'heure de l'école
    except ValueError:
        return None, False


def parse_ics(text: str) -> list[dict]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    events, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            if current.get("start"):
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name = head.split(";", 1)[0].upper()
            if name == "UID":
                current["uid"] = value.strip()
            elif name == "SUMMARY":
                current["summary"] = _unescape(value).strip()
            elif name == "LOCATION":
                current["location"] = _unescape(value).strip()
            elif name in ("DTSTART", "DTEND"):
                when, all_day = _when(head, value)
                if when:
                    current["start" if name == "DTSTART" else "end"] = when.isoformat(timespec="minutes")
                    if name == "DTSTART":
                        current["all_day"] = all_day
            elif name == "STATUS" and value.strip().upper() == "CANCELLED":
                current["cancelled"] = True
    for e in events:
        summary = e.get("summary") or "Cours"
        if re.match(r"^\s*annul[ée]e?s?\b", summary, re.I):  # « ANNULÉ – Psychanalyse »
            e["cancelled"] = True
            summary = re.sub(r"^\s*annul[ée]e?s?\s*[-–—:]*\s*", "", summary, flags=re.I) or summary
        e["summary"] = summary
        e.setdefault("uid", f"{e['start']}-{summary}")
        e.setdefault("end", e["start"])
        e["cancelled"] = bool(e.get("cancelled"))
    return sorted(events, key=lambda e: e["start"])


def fetch(url: str, timeout: float = 25) -> str:
    """Le fichier de l'agenda. Par httpx, comme les autres accès à Internet de Pirouette : il apporte ses propres
    certificats (le Python de l'app empaquetée n'a pas ceux du Mac, et urllib refusait toute connexion https)."""
    import httpx

    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("Ce lien ne commence pas par https://")
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "Pirouette (emploi du temps)"})
    except httpx.TimeoutException as exc:
        raise ValueError("Le site de l'agenda ne répond pas (réessaie plus tard).") from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"Impossible de joindre le site de l'agenda (connexion Internet ?) : {exc}") from exc
    text = response.text
    if response.status_code >= 400 and "BEGIN:VCALENDAR" not in text:
        first = text.strip().splitlines()[0][:120] if text.strip() else f"erreur {response.status_code}"
        raise ValueError(f"Le lien est refusé (le site répond : « {first} »). Vérifie qu'il est complet, clé comprise.")
    if "BEGIN:VCALENDAR" not in text:
        first = text.strip().splitlines()[0][:120] if text.strip() else "réponse vide"
        raise ValueError(f"Ce lien ne donne pas un agenda (le site répond : « {first} »).")
    return text


# ---------- Cours de l'agenda ↔ matières ----------
def auto_match(summary: str, courses: list[dict]) -> str | None:
    """La matière dont le nom correspond au cours de l'agenda (« Psychometrie » → « Psychométrie »,
    « CM ANATOMIE GÉNÉRALE » → « Anatomie »). Rien si aucune ou si deux matières se ressemblent autant."""
    have = words(summary)
    if not have:
        return None
    best, best_score, tie = None, 0.0, False
    for course in courses:
        want = words(course["name"])
        if not want:
            continue
        common = sum(1 for w in want if any(h == w or (len(w) >= 5 and (h.startswith(w) or w.startswith(h))) for h in have))
        score = common / len(want) if common else 0.0
        if score > best_score:
            best, best_score, tie = course["id"], score, False
        elif score == best_score and score > 0:
            tie = True
    return best if best_score >= 0.99 and not tie else None


class Agenda:
    def __init__(self, root: Path):
        self.path = Path(root) / "agenda.json"
        self.lock = threading.Lock()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        data.setdefault("url", "")
        data.setdefault("events", [])
        data.setdefault("map", {})
        data.setdefault("processed", [])
        data.setdefault("dismissed", [])
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def set_url(self, url: str) -> dict:
        with self.lock:
            data = self.load()
            if url.strip() != data["url"]:
                data.update(url=url.strip(), events=[], fetched_at=None, error="")
            self.save(data)
        return data

    def stale(self, now: datetime | None = None) -> bool:
        data = self.load()
        if not data["url"]:
            return False
        try:
            last = datetime.fromisoformat(data.get("fetched_at") or "")
        except ValueError:
            return True
        return (now or datetime.now()) - last >= STALE_AFTER

    def refresh(self, now: datetime | None = None) -> dict:
        """Relit le lien. En cas d'échec, les cours déjà connus restent (et l'erreur est montrée)."""
        now = now or datetime.now()
        data = self.load()
        if not data["url"]:
            return data
        try:
            events = parse_ics(fetch(data["url"]))
            error = ""
        except Exception as exc:  # réseau coupé, lien changé… : on garde ce qu'on avait
            events, error = None, str(exc) or exc.__class__.__name__
        with self.lock:
            data = self.load()
            if events is not None:
                oldest = (now - KEEP).isoformat(timespec="minutes")
                data["events"] = [e for e in events if e["end"] >= oldest]
            data["fetched_at"] = now.isoformat(timespec="seconds")
            data["error"] = error
            self.save(data)
        return data

    def course_of(self, data: dict, summary: str, courses: list[dict]) -> str | None:
        chosen = data["map"].get(norm(summary))
        if chosen == "none":
            return None
        ids = {c["id"] for c in courses}
        if chosen in ids:
            return chosen
        return auto_match(summary, courses)

    def set_map(self, summary: str, course_id: str) -> None:
        """La matière d'un cours de l'agenda : un identifiant, "none" (pas de révision), "" (reconnaître seul)."""
        with self.lock:
            data = self.load()
            key = norm(summary)
            if course_id:
                data["map"][key] = course_id
            else:
                data["map"].pop(key, None)
            self.save(data)

    def mark(self, field: str, uid: str) -> None:
        with self.lock:
            data = self.load()
            if uid not in data[field]:
                data[field] = (data[field] + [uid])[-500:]
            self.save(data)


def classes(data: dict, agenda: Agenda, courses: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Les séances entre deux dates, avec leur matière (ou aucune)."""
    a, b = start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")
    out = []
    for e in data["events"]:
        if e["end"] > a and e["start"] < b:
            out.append(e | {"course_id": agenda.course_of(data, e["summary"], courses)})
    return out


def subjects(data: dict, agenda: Agenda, courses: list[dict], today: date) -> list[dict]:
    """Les noms de cours de l'agenda (une ligne chacun) et leur matière, pour les Réglages."""
    rows: dict[str, dict] = {}
    for e in data["events"]:
        key = norm(e["summary"])
        row = rows.setdefault(key, {"summary": e["summary"], "count": 0, "next": None})
        row["count"] += 1
        if not e["cancelled"] and e["start"][:10] >= today.isoformat() and not row["next"]:
            row["next"] = e["start"]
    for key, row in rows.items():
        chosen = data["map"].get(key)
        row["course_id"] = agenda.course_of(data, row["summary"], courses)
        row["chosen"] = chosen if chosen == "none" or chosen in {c["id"] for c in courses} else ""
    return sorted(rows.values(), key=lambda r: (r["course_id"] is None, -r["count"], r["summary"]))
