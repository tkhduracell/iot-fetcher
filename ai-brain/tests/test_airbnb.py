import json
from datetime import date, datetime

import httpx

from ai_brain.airbnb import (
    TZ,
    Changeover,
    changeovers,
    due_reminders,
    parse_ics,
    reminder_watcher,
)
from ai_brain.config import load_settings
from ai_brain.tools import ToolRegistry
from ai_brain.tools.airbnb_tools import register_airbnb_tools


def _event(start: str, end: str, summary: str) -> str:
    return (
        "BEGIN:VEVENT\r\n"
        f"DTSTART;VALUE=DATE:{start}\r\n"
        f"DTEND;VALUE=DATE:{end}\r\n"
        f"SUMMARY:{summary}\r\n"
        "DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/de\r\n"
        " tails/HM123\r\n"
        "END:VEVENT\r\n"
    )


ICS = (
    "BEGIN:VCALENDAR\r\nX-WR-CALNAME:Uthyrningar\r\n"
    + _event("20261002", "20261004", "🏡 Bokning · 2 nätter · fre–sön")
    + _event("20261004", "20261007", "🏡 Bokning · 3 nätter · sön–ons")
    + _event("20261004", "20261005", "📎⛔ Block · 1 natt · sön–mån")
    + _event("20261001", "20261004", "📎 Bokning · 3 nätter · tor–sön")
    + _event("20261010", "20261012", "📎 Bokning · 2 nätter · lör–mån")
    + "END:VCALENDAR\r\n"
)


def test_parse_reads_listing_kind_and_dates():
    stays = parse_ics(ICS)
    assert len(stays) == 5
    first = stays[0]
    assert (first.listing, first.kind, first.start, first.end, first.nights) == (
        "annex",
        "booking",
        date(2026, 10, 1),
        date(2026, 10, 4),
        3,
    )
    assert [s.kind for s in stays if s.listing == "annex"].count("block") == 1


def test_back_to_back_is_same_listing_bookings_only():
    found = changeovers(parse_ics(ICS))
    # House 10-02..04 then 10-04..07 is back to back. The annex's 10-04 checkout
    # is followed by a block, not a booking, so it is not a changeover.
    assert found == [Changeover("house", date(2026, 10, 4), 2, 3)]


def test_due_reminders_tomorrow_after_hour_and_today_anytime():
    c = Changeover("house", date(2026, 10, 4), 2, 3)
    before = datetime(2026, 10, 3, 17, 0, tzinfo=TZ)
    after = datetime(2026, 10, 3, 18, 0, tzinfo=TZ)
    same_day = datetime(2026, 10, 4, 7, 0, tzinfo=TZ)
    assert due_reminders([c], before, set(), 18) == []
    assert due_reminders([c], after, set(), 18) == [c]
    assert due_reminders([c], same_day, set(), 18) == [c]
    assert due_reminders([c], after, {"house:2026-10-04"}, 18) == []


async def test_watcher_posts_once_and_remembers(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=ICS))
    posted: list[tuple[str, str]] = []

    async def post(topic: str, text: str) -> None:
        posted.append((topic, text))

    state = tmp_path / "sent.json"
    async with httpx.AsyncClient(transport=transport) as http:
        check = reminder_watcher(
            http,
            "https://example.test/cal.ics",
            state,
            post,
            18,
            clock=lambda: datetime(2026, 10, 3, 19, 0, tzinfo=TZ),
        )
        await check()
        await check()
    assert len(posted) == 1
    assert "huset" in posted[0][1] and "imorgon" in posted[0][1]
    assert json.loads(state.read_text()) == ["house:2026-10-04"]


async def test_watcher_survives_a_failed_fetch_without_leaking_the_url(tmp_path, caplog):
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as http:
        check = reminder_watcher(
            http, "https://example.test/private-SECRET/basic.ics", tmp_path / "s.json", None, 18
        )
        await check()
    assert "SECRET" not in caplog.text


def test_tool_hidden_without_url_and_scoped_to_experts():
    reg = ToolRegistry()
    register_airbnb_tools(reg)
    without = load_settings({})
    with_url = load_settings({"AIRBNB_ICAL_URL": "https://example.test/cal.ics"})
    names = lambda loop, s: {spec.name for spec in reg.specs_for(loop, s)}  # noqa: E731
    assert "airbnb_calendar" not in names("researcher", without)
    assert "airbnb_calendar" in names("researcher", with_url)
    assert "airbnb_calendar" in names("house-ops", with_url)
    assert "airbnb_calendar" not in names("brain", with_url)
