"""Le calendrier « Pirouette » dans l'app Calendrier du Mac (EventKit). Mis dans iCloud quand c'est possible :
il arrive alors tout seul sur l'iPhone et l'iPad. Le Mac demande l'autorisation une fois (Réglages Système ›
Confidentialité et sécurité › Calendriers)."""

from __future__ import annotations

import sys
import threading
from datetime import date, datetime

from . import revplan

TITLE = "Pirouette"


class CalendarError(Exception):
    """Message montré tel quel dans les Réglages."""


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import EventKit  # noqa: F401
    except ImportError:
        return False
    return True


def _granted(store) -> None:
    import EventKit

    status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
    if status == 3:  # autorisé (accès complet)
        return
    if status in (1, 2, 4):  # restreint, refusé, ou écriture seule (on doit aussi relire tes déplacements)
        raise CalendarError("Pirouette n'a pas accès à ton Calendrier. Autorise-le dans Réglages Système › "
                            "Confidentialité et sécurité › Calendriers (accès complet), puis réessaie.")
    done, answer = threading.Event(), {}

    def handler(granted, error):
        answer["ok"] = bool(granted)
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):  # macOS 14 et plus
        store.requestFullAccessToEventsWithCompletion_(handler)
    else:
        store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, handler)
    done.wait(120)
    if not answer.get("ok"):
        raise CalendarError("Accès au Calendrier refusé. Tu peux l'autoriser dans Réglages Système › "
                            "Confidentialité et sécurité › Calendriers.")


def _ns_date(moment: datetime):
    from Foundation import NSDate

    return NSDate.dateWithTimeIntervalSince1970_(moment.timestamp())


def _py_date(ns) -> datetime:
    return datetime.fromtimestamp(ns.timeIntervalSince1970())


class MacCalendar:
    """Ce dont revplan.sync a besoin : lire, créer, renommer, supprimer les événements du calendrier Pirouette."""

    def __init__(self, state: dict, color: str = "#B4532A"):
        import EventKit

        self.ek = EventKit
        self.store = EventKit.EKEventStore.alloc().init()
        _granted(self.store)
        self.store = EventKit.EKEventStore.alloc().init()  # relu après l'autorisation
        self.calendar = self._calendar(state, color)

    def _calendar(self, state: dict, color: str):
        ek = self.ek
        calendars = list(self.store.calendarsForEntityType_(ek.EKEntityTypeEvent) or [])
        known = state.get("calendar_id")
        mine = next((c for c in calendars if known and c.calendarIdentifier() == known), None) \
            or next((c for c in calendars if c.title() == TITLE and c.allowsContentModifications()), None)
        if mine:
            state["calendar_id"] = mine.calendarIdentifier()
            return mine
        if known:  # le calendrier a disparu (supprimé dans Calendrier) : on repart de zéro
            state["created"], state["skipped"] = {}, []
        source = self._source()
        if source is None:
            raise CalendarError("Aucun compte de calendrier sur ce Mac (iCloud ou « Sur mon Mac »).")
        cal = ek.EKCalendar.calendarForEntityType_eventStore_(ek.EKEntityTypeEvent, self.store)
        cal.setTitle_(TITLE)
        cal.setSource_(source)
        try:
            from AppKit import NSColor

            r, g, b = (int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
            cal.setColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0))
        except Exception:  # la couleur n'est qu'un plus
            pass
        ok, error = self.store.saveCalendar_commit_error_(cal, True, None)
        if not ok:
            raise CalendarError(f"Impossible de créer le calendrier « Pirouette » : {error}")
        state["calendar_id"] = cal.calendarIdentifier()
        return cal

    def _source(self):
        ek = self.ek
        sources = list(self.store.sources() or [])
        icloud = next((s for s in sources if s.sourceType() == ek.EKSourceTypeCalDAV and "icloud" in str(s.title()).lower()), None)
        if icloud:
            return icloud
        default = self.store.defaultCalendarForNewEvents()
        if default is not None:
            return default.source()
        return next((s for s in sources if s.sourceType() == ek.EKSourceTypeLocal), None)

    def events(self, start: date, end: date) -> list[dict]:
        pred = self.store.predicateForEventsWithStartDate_endDate_calendars_(
            _ns_date(datetime.combine(start, datetime.min.time())), _ns_date(datetime.combine(end, datetime.min.time())), [self.calendar])
        out = []
        for e in self.store.eventsMatchingPredicate_(pred) or []:
            out.append({"id": e.eventIdentifier(), "key": revplan.key_in(e.notes()), "title": str(e.title() or ""),
                        "start": _py_date(e.startDate())})
        return out

    def create(self, slot: revplan.Slot) -> str:
        ek = self.ek
        event = ek.EKEvent.eventWithEventStore_(self.store)
        event.setCalendar_(self.calendar)
        event.setTitle_(slot.title)
        event.setNotes_(slot.notes)
        event.setStartDate_(_ns_date(slot.start))
        event.setEndDate_(_ns_date(slot.end))
        event.addAlarm_(ek.EKAlarm.alarmWithRelativeOffset_(0))  # une notification à l'heure prévue
        ok, error = self.store.saveEvent_span_commit_error_(event, ek.EKSpanThisEvent, True, None)
        if not ok:
            raise CalendarError(f"Impossible d'ajouter une révision au Calendrier : {error}")
        return event.eventIdentifier()

    def retitle(self, event_id: str, title: str) -> None:
        event = self.store.eventWithIdentifier_(event_id)
        if event is not None:
            event.setTitle_(title)
            self.store.saveEvent_span_commit_error_(event, self.ek.EKSpanThisEvent, True, None)

    def delete(self, event_id: str) -> None:
        event = self.store.eventWithIdentifier_(event_id)
        if event is not None:
            self.store.removeEvent_span_commit_error_(event, self.ek.EKSpanThisEvent, True, None)


def run(state: dict, color: str, work) -> dict:
    """Ouvre le calendrier et fait `work(calendrier)`, dans un bloc où les objets Objective-C sont libérés (appelé
    depuis un fil du serveur)."""
    import objc

    with objc.autorelease_pool():
        return work(MacCalendar(state, color))
