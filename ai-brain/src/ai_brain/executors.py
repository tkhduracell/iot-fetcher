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
* **Allowlists.** ``ha_service`` is the one executor whose target is named by
  the model rather than by configuration, so both the service and the data
  keys it may carry are checked against a fixed set first. ``docker_restart``
  is not allowlisted the same way: its whole target space is "a container
  name", plainly visible in the Slack message a human approves, unlike
  ``ha_service``'s combination of service + entity + arbitrary data.

* **The brain's own identity.** ``rewrite_identity`` is the one executor that
  changes the brain rather than the house. It writes only if ``identity.md`` is
  still the file the proposal was based on, so an approval can never overwrite a
  newer text Filip put there in the meantime.

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

from ai_brain import identity
from ai_brain.config import Settings
from ai_brain.docker_api import container_url
from ai_brain.memory import MemoryDir

log = logging.getLogger(__name__)

DEFAULT_TZ = "Europe/Stockholm"
QUIET_FROM_HOUR = 22
QUIET_TO_HOUR = 7
TIMEOUT_S = 20

# The Home Assistant services an approved proposal may call. An allowlist, not
# a filter: ``ha_service`` takes a service name from a language model, and the
# set of things HA can be asked to do includes deleting backups and restarting
# the host. Anything not named here is refused before a request is built.
HA_ALLOWED_SERVICES = frozenset(
    {
        "light.turn_on",
        "light.turn_off",
        "light.toggle",
        "switch.turn_on",
        "switch.turn_off",
        "switch.toggle",
        "scene.turn_on",
        "script.turn_on",
        "climate.set_temperature",
        "cover.open_cover",
        "cover.close_cover",
        "fan.turn_on",
        "fan.turn_off",
        "media_player.media_pause",
        "media_player.volume_set",
    }
)

# Service data the model may pass through. Free-form data would let a caller
# reach fields the allowlist is meant to bound (``entity_id`` above all, which
# is supplied separately and never from here).
HA_ALLOWED_DATA_KEYS = frozenset(
    {
        "brightness_pct",
        "color_temp_kelvin",
        "transition",
        "temperature",
        "hvac_mode",
        "position",
        "percentage",
        "volume_level",
    }
)


class QuietHours(Exception):
    """Raised instead of making noise between 22:00 and 07:00 local time."""


class StaleBase(Exception):
    """Raised when the file an approval was reasoned about is no longer the file.

    Its message is what the proposal's ``failed`` result says, and so what the
    brain reads in its inbox: it names what happened and what to do next.
    """


def in_quiet_hours(now: datetime, tz: str = DEFAULT_TZ) -> bool:
    local = now.astimezone(ZoneInfo(tz))
    return local.hour >= QUIET_FROM_HOUR or local.hour < QUIET_TO_HOUR


class Executors:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        clock: Callable[[], datetime],
        brain: MemoryDir | None = None,
    ) -> None:
        self.settings = settings
        self.http = http
        self.clock = clock
        # Only ``rewrite_identity`` touches it; without one that kind fails.
        self.brain = brain

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

    async def ha_service(self, service: str, entity_id: str, data: dict | None = None) -> str:
        if service not in HA_ALLOWED_SERVICES:
            raise ValueError(f"service not allowed: {service}")
        domain, _, name = service.partition(".")
        extra = dict(data or {})
        unknown = sorted(set(extra) - HA_ALLOWED_DATA_KEYS)
        if unknown:
            raise ValueError(f"{service} data has unsupported keys: {', '.join(unknown)}")
        if self.settings.dry_run:
            log.info("[dry-run] ha_service %s on %s data=%s", service, entity_id, extra)
            return "dry-run"
        url = f"{self.settings.ha_url.rstrip('/')}/api/services/{domain}/{name}"
        response = await self.http.post(
            url,
            json={"entity_id": entity_id, **extra},
            headers={"Authorization": f"Bearer {self.settings.ha_token}"},
            timeout=TIMEOUT_S,
        )
        _raise_for_status(response, "ha_service")
        return f"called {service} on {entity_id}"

    async def docker_restart(self, container: str) -> str:
        # Before the dry-run shortcut, like the quiet-hours check: a rehearsal
        # must report the refusal a live run would. Also covers a proposal that
        # was stored before propose validated the name.
        url = container_url(self.settings.docker_proxy_url, container, "restart")
        if self.settings.dry_run:
            log.info("[dry-run] docker_restart %s", container)
            return "dry-run"
        response = await self.http.post(url, timeout=TIMEOUT_S)
        _raise_for_status(response, "docker_restart")
        return f"restarted {container}"

    async def rewrite_identity(self, body: str, base_hash: str) -> str:
        if self.brain is None:
            raise RuntimeError("rewrite_identity: no brain memory to write to")
        # Before the dry-run shortcut, like the other refusals: a rehearsal must
        # report what a live run would. The rules are the ones ``propose`` held
        # the text to, applied again to what is about to be written.
        problem = identity.problem(body)
        if problem is not None:
            raise ValueError(problem)
        current = self.brain.persona_digest()
        if current != base_hash:
            raise StaleBase(
                f"identity.md changed after this was proposed (it was {identity.short(base_hash)}, "
                f"it is {identity.short(current)}), so nothing was written -- the newer text is "
                "untouched. Read your identity again and propose against it if the change is "
                "still wanted."
            )
        if self.settings.dry_run:
            log.info("[dry-run] rewrite_identity %d chars", len(body))
            return "dry-run"
        # No await between the check and the write, so nothing in this process can
        # land in between; the revision the brain keeps makes the old text recoverable.
        self.brain.rewrite_identity(body)
        return f"identity rewritten ({len(body.strip())} chars); the old text is in its history"

    async def run(self, kind: str, payload: dict) -> str:
        if kind == "sonos_say":
            return await self.sonos_say(_require(payload, "text", kind))
        if kind == "ha_todo_add":
            return await self.ha_todo_add(_require(payload, "item", kind))
        if kind == "ha_service":
            data = payload.get("data")
            if data is not None and not isinstance(data, dict):
                raise ValueError("ha_service data must be an object")
            return await self.ha_service(
                _require(payload, "service", kind),
                _require(payload, "entity_id", kind),
                data,
            )
        if kind == "docker_restart":
            return await self.docker_restart(_require(payload, "container", kind))
        if kind == identity.KIND:
            return await self.rewrite_identity(
                _require(payload, "body", kind), _require(payload, "base_hash", kind)
            )
        if kind == "idea":
            # Nothing to execute: the approval itself is the outcome, and it
            # comes back to the brain as an inbox note like any other.
            return f"Filip wants this: {_require(payload, 'title', kind)}"
        raise ValueError(f"unknown kind: {kind}")


def _require(payload: dict, key: str, kind: str) -> str:
    if key not in payload:
        raise ValueError(f"{kind} payload is missing {key!r}")
    return str(payload[key])


def _raise_for_status(response: httpx.Response, label: str) -> None:
    if not response.is_success:
        raise RuntimeError(f"{label}: backend returned HTTP {response.status_code}")
