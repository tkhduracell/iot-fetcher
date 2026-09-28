"""airbnb_calendar: the house's and annex's bookings and blocked dates.

Read-only, and it never returns the calendar URL (a credential) or the
per-booking Airbnb links -- only listing, kind and dates.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from ai_brain.airbnb import TZ, changeovers, fetch_stays
from ai_brain.config import Settings
from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok
from ai_brain.tools.http import client_for

AIRBNB_LOOPS = frozenset({"researcher", "energy", "house-ops"})
MAX_DAYS = 400


def _configured(settings: Settings) -> bool:
    return bool(settings.airbnb_ical_url)


async def _airbnb_calendar(ctx: ToolContext, args: dict) -> str:
    try:
        days = max(1, min(int(args.get("days", 60)), MAX_DAYS))
    except (TypeError, ValueError):
        return err("airbnb_calendar: days must be a number")
    try:
        async with client_for(ctx) as client:
            stays = await fetch_stays(client, ctx.settings.airbnb_ical_url)
    except (httpx.HTTPError, ValueError) as exc:
        return err(f"airbnb_calendar: fetch failed ({type(exc).__name__})")
    today = datetime.now(TZ).date()
    horizon = today + timedelta(days=days)
    window = [s for s in stays if s.end >= today and s.start <= horizon]
    occupied = {s.listing for s in window if s.kind == "booking" and s.start <= today < s.end}
    return ok(
        {
            "today": today.isoformat(),
            "occupied_now": sorted(occupied),
            "stays": [s.as_dict() for s in window],
            "back_to_back": [
                c.as_dict() for c in changeovers(stays) if today <= c.day <= horizon
            ],
            "note": "listing house = 🏡 main house, annex = 📎 separate unit. "
            "end is the checkout day. The calendar holds current and future "
            "stays only; past revenue lives in the Checklistor sheet.",
        }
    )


def register_airbnb_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="airbnb_calendar",
                description=(
                    "Airbnb bookings and blocked dates for the house (🏡) and the annex (📎) "
                    "from today up to `days` ahead (default 60): listing, kind (booking/block), "
                    "check-in, checkout, nights, who is occupied now, and back-to-back "
                    "changeovers (checkout and next check-in on the same day)."
                ),
                parameters={
                    "type": "object",
                    "properties": {"days": {"type": "integer", "minimum": 1, "maximum": MAX_DAYS}},
                },
            ),
            fn=_airbnb_calendar,
            loops=AIRBNB_LOOPS,
            available=_configured,
        )
    )
