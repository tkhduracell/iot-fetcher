"""The Airbnb calendar: parsing, back-to-back changeovers and cleaning reminders.

The source is a Google Calendar ("Uthyrningar") synced from Airbnb, read
through its secret iCal address (``AIRBNB_ICAL_URL``). That URL is a
credential -- anyone holding it can read the calendar -- so it lives in the
environment only, never in memory, a fact or a tool result.

Event titles carry everything: ``🏡 Bokning · 2 nätter · fre–sön`` or
``📎⛔ Block · 1 natt · sön–mån``. 🏡 is the house, 📎 the annex (the separate
unit with its own entrance); "Bokning" is a guest stay, "⛔ Block" is dates
held closed. Dates are all-day (DTSTART/DTEND as YYYYMMDD, DTEND exclusive),
so a stay's end date is its checkout day.

The reminder is deterministic on purpose: it runs from the supervisor, not
from an agent cycle, because a cleaning reminder that depends on an LLM's
quota -- the brain runs about once a day -- would arrive late or not at all.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Stockholm")
LISTINGS = {"🏡": "house", "📎": "annex"}
SLACK_TOPIC = "airbnb-cleaning"
FETCH_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Stay:
    listing: str  # "house" | "annex" | "unknown"
    kind: str  # "booking" | "block"
    start: date  # check-in day
    end: date  # checkout day (DTEND is exclusive for all-day events)

    @property
    def nights(self) -> int:
        return (self.end - self.start).days

    def as_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        d["nights"] = self.nights
        return d


@dataclass(frozen=True)
class Changeover:
    """A checkout and the next check-in on the same listing on the same day."""

    listing: str
    day: date
    out_nights: int
    in_nights: int

    def as_dict(self) -> dict:
        return {
            "listing": self.listing,
            "day": self.day.isoformat(),
            "checkout_stay_nights": self.out_nights,
            "checkin_stay_nights": self.in_nights,
        }


def _date(value: str) -> date | None:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", value)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def parse_ics(text: str) -> list[Stay]:
    """Stays from an iCal body, sorted by start. Unparseable events are skipped."""
    text = text.replace("\r\n ", "").replace("\n ", "")  # unfold continuation lines
    stays: list[Stay] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        fields: dict[str, str] = {}
        for line in block.strip().splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key.split(";")[0].strip()] = value.strip()
        start = _date(fields.get("DTSTART", ""))
        end = _date(fields.get("DTEND", ""))
        if start is None or end is None or end <= start:
            continue
        summary = fields.get("SUMMARY", "")
        listing = next((name for icon, name in LISTINGS.items() if icon in summary), "unknown")
        kind = "block" if ("⛔" in summary or "Block" in summary) else "booking"
        stays.append(Stay(listing, kind, start, end))
    return sorted(stays, key=lambda s: (s.start, s.listing))


def changeovers(stays: list[Stay]) -> list[Changeover]:
    """Back-to-back bookings: one guest checks out the day the next checks in."""
    bookings = [s for s in stays if s.kind == "booking"]
    out: list[Changeover] = []
    for left in bookings:
        for right in bookings:
            if right is not left and right.listing == left.listing and right.start == left.end:
                out.append(Changeover(left.listing, left.end, left.nights, right.nights))
    return sorted(out, key=lambda c: (c.day, c.listing))


async def fetch_stays(http: httpx.AsyncClient, url: str) -> list[Stay]:
    response = await http.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
    response.raise_for_status()
    return parse_ics(response.text)


def _listing_label(listing: str) -> str:
    return {"house": "🏡 huset", "annex": "📎 annexet"}.get(listing, listing)


def reminder_text(c: Changeover, today: date) -> str:
    when = "idag" if c.day == today else "imorgon"
    return (
        f"🧹 Städpåminnelse: back-to-back i {_listing_label(c.listing)} {when} "
        f"({c.day.isoformat()}). Utcheckning efter {c.out_nights} "
        f"{'natt' if c.out_nights == 1 else 'nätter'}, ny incheckning samma dag "
        f"({c.in_nights} {'natt' if c.in_nights == 1 else 'nätter'}). "
        "Boka/bekräfta städningen."
    )


def due_reminders(
    items: list[Changeover], now: datetime, sent: set[str], hour: int
) -> list[Changeover]:
    """Changeovers to remind about now: tomorrow's once ``hour`` has passed,
    and today's at any time (a restart must not swallow a same-day reminder)."""
    today = now.date()
    tomorrow = today + timedelta(days=1)
    due = []
    for c in items:
        key = f"{c.listing}:{c.day.isoformat()}"
        if key in sent:
            continue
        if c.day == today or (c.day == tomorrow and now.hour >= hour):
            due.append(c)
    return due


def reminder_watcher(
    http: httpx.AsyncClient,
    url: str,
    state_path: Path,
    post: Callable[[str, str], Awaitable[object]],
    hour: int,
    clock: Callable[[], datetime] = lambda: datetime.now(TZ),
) -> Callable[[], Awaitable[None]]:
    """A periodic job that posts one Slack reminder per back-to-back changeover."""

    async def check() -> None:
        try:
            stays = await fetch_stays(http, url)
        except (httpx.HTTPError, ValueError) as exc:
            # Never log the URL: it is the calendar's credential.
            log.warning("[airbnb] calendar fetch failed: %s", type(exc).__name__)
            return
        now = clock()
        try:
            sent = set(json.loads(state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            sent = set()
        for c in due_reminders(changeovers(stays), now, sent, hour):
            await post(SLACK_TOPIC, reminder_text(c, now.date()))
            sent.add(f"{c.listing}:{c.day.isoformat()}")
            log.info("[airbnb] reminded about %s changeover on %s", c.listing, c.day)
        # Keep only recent keys so the file never grows without bound.
        cutoff = (now.date() - timedelta(days=30)).isoformat()
        sent = {k for k in sent if k.split(":", 1)[1] >= cutoff}
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(sent)), encoding="utf-8")
        os.replace(tmp, state_path)

    return check
