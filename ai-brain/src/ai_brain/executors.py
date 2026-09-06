"""The only code in this system that changes something in the house.

Every tool an agent can reach is a read. Anything that speaks out loud or
writes to a list lands here instead, behind two gates that the agent cannot
argue its way past:

* **Quiet hours.** A speaker at 03:00 wakes people up, so ``sonos_say`` raises
  ``QuietHours`` rather than making a sound. The check happens before the
  dry-run shortcut, so a rehearsal reports the same refusal a live run would.
* **DRY_RUN.** Set it and nothing leaves the process; the call is logged and
  returns ``"dry-run"``. Tests assert on an untouched respx router, which is
  the only honest way to prove a speaker was never dialled.

Executors are deliberately dumb: no retries, no queueing, no state. A failure
raises, and ``Approvals`` is what decides that a raised exception means a
proposal is ``failed`` rather than a cycle is over.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from ai_brain.config import Settings

log = logging.getLogger(__name__)

DEFAULT_TZ = "Europe/Stockholm"
QUIET_FROM_HOUR = 22
QUIET_TO_HOUR = 7
TIMEOUT_S = 20


class QuietHours(Exception):
    """Raised instead of making noise between 22:00 and 07:00 local time."""


def in_quiet_hours(now: datetime, tz: str = DEFAULT_TZ) -> bool:
    local = now.astimezone(ZoneInfo(tz))
    return local.hour >= QUIET_FROM_HOUR or local.hour < QUIET_TO_HOUR


class Executors:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime],
    ) -> None:
        self.settings = settings
        self.http = http
        self.clock = clock

    async def sonos_say(self, text: str) -> str:
        if in_quiet_hours(self.clock()):
            raise QuietHours(f"quiet hours ({QUIET_FROM_HOUR}:00-0{QUIET_TO_HOUR}:00 local)")
        if self.settings.dry_run:
            log.info("[dry-run] sonos_say %r to %s", text, self.settings.sonos_room)
            return "dry-run"
        base = self.settings.sonos_url.rstrip("/")
        room = quote(self.settings.sonos_room, safe="")
        url = f"{base}/{room}/say/{quote(text, safe='')}"
        response = await self.http.get(url, timeout=TIMEOUT_S)
        _raise_for_status(response, "sonos_say")
        return response.text

    async def ha_todo_add(self, item: str) -> str:
        if self.settings.dry_run:
            log.info("[dry-run] ha_todo_add %r to %s", item, self.settings.ha_todo_list)
            return "dry-run"
        url = f"{self.settings.ha_url.rstrip('/')}/api/services/todo/add_item"
        response = await self.http.post(
            url,
            json={"entity_id": self.settings.ha_todo_list, "item": item},
            headers={"Authorization": f"Bearer {self.settings.ha_token}"},
            timeout=TIMEOUT_S,
        )
        _raise_for_status(response, "ha_todo_add")
        return f"added: {item}"

    async def run(self, kind: str, payload: dict) -> str:
        if kind == "sonos_say":
            return await self.sonos_say(_require(payload, "text", kind))
        if kind == "ha_todo_add":
            return await self.ha_todo_add(_require(payload, "item", kind))
        raise ValueError(f"unknown kind: {kind}")


def _require(payload: dict, key: str, kind: str) -> str:
    if key not in payload:
        raise ValueError(f"{kind} payload is missing {key!r}")
    return str(payload[key])


def _raise_for_status(response: httpx.Response, label: str) -> None:
    if not response.is_success:
        raise RuntimeError(f"{label}: backend returned HTTP {response.status_code}")
