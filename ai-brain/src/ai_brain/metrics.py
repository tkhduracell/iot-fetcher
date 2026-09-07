"""Influx line protocol for the brain's own vital signs.

Nothing here is allowed to matter. ``render`` reads counters that the loops
keep anyway, and ``MetricsWriter.write`` swallows every failure it meets: a
VictoriaMetrics outage must never be the reason the brain stops thinking.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ai_brain.ledger import Ledger

log = logging.getLogger(__name__)

WRITE_TIMEOUT_S = 10.0


def _tag(value: str) -> str:
    """Escape a tag value per line protocol: spaces, commas and equals signs."""
    return (
        str(value).replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")
    )


def render(loops: dict[str, Any], ledger: Ledger, now: float) -> list[str]:
    lines: list[str] = []
    for name, loop in loops.items():
        tag = _tag(name)
        for status, count in loop.cycle_counts.items():
            lines.append(f"ai_brain_cycle_total,loop={tag},status={_tag(status)} value={count}i")
        # A loop that has never finished a cycle has no age to report; a zero
        # would read as "just ran", which is the opposite of the truth.
        if loop.last_cycle_at > 0:
            lines.append(
                f"ai_brain_loop_last_cycle_seconds,loop={tag} value={now - loop.last_cycle_at}"
            )

    for key in ledger.keys():  # noqa: SIM118 - Ledger.keys() is a method, not a mapping
        requests_left, tokens_left = ledger.remaining_fraction(key)
        model = _tag(key)
        lines.append(f"ai_brain_ledger_remaining,model={model},kind=requests value={requests_left}")
        lines.append(f"ai_brain_ledger_remaining,model={model},kind=tokens value={tokens_left}")

    return lines


class MetricsWriter:
    def __init__(self, vm_url: str, token: str, http: httpx.AsyncClient) -> None:
        self.vm_url = vm_url.rstrip("/")
        self.token = token
        self.http = http
        self._warned_unconfigured = False

    async def write(self, lines: list[str]) -> None:
        if not lines:
            return
        if not self.vm_url or not self.token:
            # Unconfigured is a permanent state, and this runs every 60s: say
            # it once rather than a thousand identical lines a day.
            if not self._warned_unconfigured:
                self._warned_unconfigured = True
                log.warning("metrics disabled: VM_URL or INFLUX_TOKEN is empty")
            return
        try:
            response = await self.http.post(
                f"{self.vm_url}/api/v2/write",
                content="\n".join(lines).encode("utf-8"),
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=WRITE_TIMEOUT_S,
            )
            if response.status_code >= 400:
                log.warning("metrics write rejected: HTTP %d", response.status_code)
        except Exception:  # metrics must never break the caller
            log.warning("metrics write failed", exc_info=True)
