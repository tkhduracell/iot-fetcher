"""The brain's one window onto a human, over Slack Socket Mode.

Every topic the brain talks about becomes an *agent session*: one threaded
conversation in Filip's DM, titled after the topic, carrying a status dot he
can see from the sidebar. Outbound goes through :class:`SlackOut`, inbound
through :class:`SlackIn`, and nothing else in the process touches Slack.

Three properties shape the code more than the API does:

* **A topic is a thread, and the mapping outlives the process.** ``sessions.json``
  next to the brain's memory holds ``{topic: {thread_ts, channel, status}}``,
  written atomically. A restart mid-conversation keeps replying in the same
  thread rather than opening a second one about the same thing.
* **The agent cannot spam.** A sliding hour window caps posts; over it,
  ``post`` raises :class:`SlackRateCapped` and the tool turns that into an
  error the model reads. Better to refuse loudly inside the cycle than to let
  a confused loop fill a DM.
* **Slack being down is not a reason to lose a thought.** A call that fails
  three retries is written to ``outbox/slack/<ts>.json`` and ``post`` returns
  ``"queued"``; ``flush_queue`` drains it oldest-first on a later cycle.

Status transitions are deliberately best-effort: ``set_status`` logs and
swallows. A wrong dot in the sidebar must never take down a cycle that
otherwise worked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

Status = Literal["processing", "active", "closed"]
# Bolt hands listeners an ack callable; the tests call the handlers without one.
Ack = Callable[[], Awaitable[None]] | None
STATUSES: frozenset[str] = frozenset({"processing", "active", "closed"})

MAX_PER_HOUR = 20
WINDOW_S = 3600.0
BACKOFFS: tuple[float, ...] = (0.5, 1.0, 2.0)
NOTE_SENDER = "filip"


class SlackRateCapped(Exception):
    """Raised when a post would exceed the hourly cap. Never swallowed here."""


class SlackOut:
    """Posts into per-topic agent sessions, and keeps the topic->thread map."""

    def __init__(
        self,
        client: Any,
        user_id: str,
        brain: Any,
        clock: Callable[[], datetime],
        max_per_hour: int = MAX_PER_HOUR,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.brain = brain
        self.clock = clock
        self.max_per_hour = max_per_hour
        self.sleep = sleep
        self._channel: str | None = None
        self._recent: list[float] = []

    # -- paths ---------------------------------------------------------

    @property
    def sessions_path(self) -> Path:
        return self.brain.root / "sessions.json"

    @property
    def queue_dir(self) -> Path:
        return self.brain.outbox_dir / "slack"

    # -- posting -------------------------------------------------------

    async def post(self, topic: str, text: str) -> str:
        """Post ``text`` under ``topic``. Returns the ts, or ``"queued"``."""
        self._charge_rate()
        return await self._post_charged(topic, text)

    async def _post_charged(self, topic: str, text: str) -> str:
        """The body of ``post``, for callers that already paid the rate cap."""
        sessions = self._read_sessions()
        session = sessions.get(topic)
        try:
            channel = await self._open_dm()
            thread_ts = session["thread_ts"] if session else None
            response = await self._retry(
                self.client.chat_postMessage,
                channel=channel,
                text=text,
                thread_ts=thread_ts,
            )
        except Exception:
            log.warning("[slack] post to %s failed, queueing", topic, exc_info=True)
            self._enqueue(topic, text)
            return "queued"

        ts = str(response["ts"])
        if session is not None:
            return ts

        # A brand new topic: name the session after it and light the dot, so
        # the thread is recognisable in the sidebar before the reply lands.
        sessions[topic] = {"thread_ts": ts, "channel": channel, "status": "processing"}
        self._write_sessions(sessions)
        await self._session_call(
            "agents.sessions.rename", channel_id=channel, thread_ts=ts, title=topic
        )
        await self._session_call(
            "agents.sessions.setStatus", channel_id=channel, thread_ts=ts, status="processing"
        )
        return ts

    async def flush_queue(self) -> int:
        """Re-send everything the outbox holds, oldest first. Returns the count."""
        if not self.queue_dir.exists():
            return 0
        sent = 0
        for path in sorted(self.queue_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                topic, text = str(item["topic"]), str(item["text"])
            except (ValueError, KeyError, TypeError):
                log.warning("[slack] discarding unreadable queued post %s", path.name)
                path.unlink()
                continue

            # The rate cap is checked before the file is touched: out of budget
            # means leave this and everything after it for the next cycle,
            # rather than burning through the queue.
            try:
                self._charge_rate()
            except SlackRateCapped:
                log.info("[slack] rate cap reached while flushing, %s left queued", path.name)
                break

            # Now the original can go: a failing post() writes its own fresh
            # copy, so removing it first is what keeps this from being either
            # duplicated or -- if post() rewrote this very name -- deleted.
            path.unlink()
            if await self._post_charged(topic, text) != "queued":
                sent += 1
        return sent

    # -- status --------------------------------------------------------

    async def set_status(self, topic: str, status: Status) -> None:
        if status not in STATUSES:
            log.warning("[slack] ignoring unknown status %r for %s", status, topic)
            return
        sessions = self._read_sessions()
        session = sessions.get(topic)
        if session is None:
            log.info("[slack] no session for topic %r, not setting status", topic)
            return
        await self._session_call(
            "agents.sessions.setStatus",
            channel_id=session["channel"],
            thread_ts=session["thread_ts"],
            status=status,
        )
        session["status"] = status
        self._write_sessions(sessions)

    async def close(self, topic: str) -> None:
        await self.set_status(topic, "closed")

    # -- internals -----------------------------------------------------

    def _charge_rate(self) -> None:
        now = self.clock().timestamp()
        self._recent = [t for t in self._recent if now - t < WINDOW_S]
        if len(self._recent) >= self.max_per_hour:
            raise SlackRateCapped(
                f"slack post cap reached ({self.max_per_hour}/h); try again next cycle"
            )
        self._recent.append(now)

    async def _open_dm(self) -> str:
        if self._channel is None:
            response = await self._retry(self.client.conversations_open, users=self.user_id)
            self._channel = str(response["channel"]["id"])
        return self._channel

    async def _retry(self, fn: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
        last: Exception | None = None
        for attempt, backoff in enumerate(BACKOFFS):
            try:
                return await fn(**kwargs)
            except Exception as exc:
                last = exc
                if attempt == len(BACKOFFS) - 1:
                    break
                log.info("[slack] attempt %d failed (%s), retrying", attempt + 1, exc)
                await self.sleep(backoff)
        raise last  # type: ignore[misc]

    async def _session_call(self, method: str, **payload: Any) -> None:
        """Agent-session calls are cosmetic; a failure must not break a post."""
        try:
            await self.client.api_call(method, json=payload)
        except Exception:
            log.warning("[slack] %s failed", method, exc_info=True)

    def _enqueue(self, topic: str, text: str) -> None:
        """Write one queued post under a name that cannot collide.

        The clock alone is not unique: it can be frozen or coarse, and two posts
        in the same second (or a re-queue during a flush) would otherwise
        overwrite each other and silently drop a message. The counter suffix
        keeps the timestamp ordering that ``flush_queue`` sorts on.
        """
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.clock().strftime("%Y%m%dT%H%M%S%f")
        counter = 0
        while True:
            path = self.queue_dir / f"{stamp}-{counter}.json"
            if not path.exists():
                break
            counter += 1
        _atomic_write(path, json.dumps({"topic": topic, "text": text}))

    def _read_sessions(self) -> dict[str, dict[str, str]]:
        if not self.sessions_path.exists():
            return {}
        try:
            data = json.loads(self.sessions_path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("[slack] sessions.json is unreadable, starting fresh")
            return {}
        return data if isinstance(data, dict) else {}

    def _write_sessions(self, sessions: dict[str, dict[str, str]]) -> None:
        self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.sessions_path, json.dumps(sessions, indent=2))

    def topic_for_thread(self, thread_ts: str) -> str | None:
        if not thread_ts:
            return None
        for topic, session in self._read_sessions().items():
            if session.get("thread_ts") == thread_ts:
                return topic
        return None


class SlackIn:
    """Turns the four events we subscribe to into brain inbox notes."""

    def __init__(
        self,
        app: Any,
        brain: Any,
        approvals: Any,
        out: SlackOut,
        wake: Callable[[str], None],
        user_id: str,
    ) -> None:
        self.app = app
        self.brain = brain
        self.approvals = approvals
        self.out = out
        self.wake = wake
        self.user_id = user_id

    def register(self) -> None:
        self.app.event("message")(self.on_message)
        self.app.event("reaction_added")(self.on_reaction)
        self.app.event("agent_session_stopped")(self.on_session_stopped)
        # Subscribed to because Slack sends them; handled only so Bolt stops
        # logging "unhandled request" for events we deliberately ignore.
        self.app.event("app_home_opened")(self.on_ignored)
        self.app.event("agent_session_title_changed")(self.on_ignored)

    async def on_message(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        if event.get("channel_type") != "im" or event.get("user") != self.user_id:
            return
        # Our own posts come back as messages; so do edits and joins.
        if event.get("bot_id") or event.get("subtype"):
            return
        body = str(event.get("text") or "").strip()
        if not body:
            return

        topic = self.out.topic_for_thread(str(event.get("thread_ts") or ""))
        if topic is not None:
            body = f"topic: {topic}\n{body}"
        self.brain.drop_note(NOTE_SENDER, body)
        self.wake("brain")

    async def on_reaction(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        if event.get("user") != self.user_id:
            return
        await self.approvals.on_reaction(
            str(event.get("item", {}).get("ts", "")), str(event.get("reaction", ""))
        )

    async def on_session_stopped(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        thread_ts = event.get("thread_ts") or event.get("session", {}).get("thread_ts") or ""
        topic = self.out.topic_for_thread(str(thread_ts))
        if topic is None:
            log.info("[slack] session stopped for unknown thread %r", thread_ts)
            return
        self.brain.drop_note(NOTE_SENDER, f"Filip stopped {topic}")
        await self.out.close(topic)
        self.wake("brain")

    async def on_ignored(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)


async def start_slack(
    settings: Any,
    brain: Any,
    approvals: Any,
    wake: Callable[[str], None],
) -> tuple[SlackOut, Any]:
    """Build the Slack adapter. The caller connects the returned handler."""
    # Imported as modules, not names, so the attributes resolve at call time.
    import slack_bolt.adapter.socket_mode.aiohttp as bolt_socket
    import slack_bolt.async_app as bolt_app

    from ai_brain.memory import now_utc

    # Socket Mode carries no signed HTTP requests, so the signature-verifying
    # middleware has nothing to verify -- and demands a signing secret we have
    # no reason to hold.
    app = bolt_app.AsyncApp(token=settings.slack_bot_token, request_verification_enabled=False)
    out = SlackOut(app.client, settings.slack_user_id, brain, now_utc)
    SlackIn(app, brain, approvals, out, wake, settings.slack_user_id).register()
    approvals.on_message = out.post
    return out, bolt_socket.AsyncSocketModeHandler(app, settings.slack_app_token)


async def _ack(ack: Ack) -> None:
    if ack is not None:
        await ack()


def _atomic_write(path: Path, body: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
