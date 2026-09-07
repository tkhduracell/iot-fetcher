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
* **The assistant thread wins.** This is an agent app, so Filip talks inside a
  thread Slack owns, tracked under the reserved ``chat`` topic. A new topic
  raised while that thread is open replies *into it*, labelled ``*topic*``, and
  aliases itself onto the same thread. Posting top-level and renaming a session
  instead would put every reply in a thread he is not looking at -- the History
  tab fills with ghosts while the conversation on screen stays silent. Only with
  no assistant thread at all does a topic open one of its own.
* **The agent cannot spam.** A sliding hour window caps posts; over it,
  ``post`` raises :class:`SlackRateCapped` and the tool turns that into an
  error the model reads. Better to refuse loudly inside the cycle than to let
  a confused loop fill a DM.
* **Slack being down is not a reason to lose a thought.** A call that fails
  three retries is written to ``outbox/slack/<ts>.json`` and ``post`` returns
  ``"queued"``; ``flush_queue`` drains it oldest-first on a later cycle, and
  stops at the first message Slack still will not take rather than reordering
  the ones behind it.

Status transitions are deliberately best-effort: ``set_status`` logs and
swallows, and falls back from ``agents.sessions.*`` to the older
``assistant.threads.*`` calls, which is all this workspace's app is scoped for.
A wrong dot in the sidebar must never take down a cycle that otherwise worked.
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
CHAT_TOPIC = "chat"
SUGGESTED_PROMPTS = [
    {"title": "Status", "message": "Vad jobbar du med just nu?"},
    {"title": "El", "message": "Hur mycket el producerar vi just nu?"},
]
# The legacy assistant.threads.* surface only knows two dots: thinking, or none.
_LEGACY_STATUS = {"processing": "is thinking\u2026", "active": "", "closed": ""}


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
        """Post ``text`` under ``topic``. Returns the ts, or ``"queued"``.

        The cap is *checked* before the send and *charged* after it succeeds.
        Charging up front means a Slack outage burns the hour's whole budget on
        posts that only reached the retry queue, and the flush that finally
        delivers them is then refused for being over cap -- the outage would
        silence the brain for an hour after Slack came back.
        """
        self._check_cap()
        ts = await self._send(topic, text)
        if ts != "queued":
            self._charge_rate()
        return ts

    async def _send(self, topic: str, text: str) -> str:
        """Post without touching the rate cap. Returns the ts, or ``"queued"``."""
        sessions = self._read_sessions()
        session = sessions.get(topic)
        # No thread of its own yet, but Filip has an assistant thread open:
        # speak there rather than opening a ghost thread he never sees.
        chat = sessions.get(CHAT_TOPIC) if session is None else None
        chat_thread_ts = chat.get("thread_ts") if isinstance(chat, dict) else None
        if not chat_thread_ts:
            chat, chat_thread_ts = None, None
        if chat is not None and topic != CHAT_TOPIC:
            text = f"*{topic}* {text}"
        try:
            # A session records the channel it lives in; the assistant thread
            # is not always in the DM we would open ourselves.
            channel = str((chat or session or {}).get("channel") or "") or await self._open_dm()
            if chat is not None:
                thread_ts: str | None = str(chat_thread_ts)
            else:
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

        if chat is not None:
            # The reply went into the assistant thread Filip is looking at.
            # Remember the topic as an alias onto that same thread so later
            # posts about it land in the same conversation -- and leave the
            # thread's own title and dot alone, since Slack owns those.
            sessions[topic] = {
                "thread_ts": str(chat_thread_ts),
                "channel": channel,
                "status": "active",
            }
            self._write_sessions(sessions)
            return ts

        # A brand new topic: name the session after it and light the dot, so
        # the thread is recognisable in the sidebar before the reply lands.
        sessions[topic] = {"thread_ts": ts, "channel": channel, "status": "processing"}
        self._write_sessions(sessions)
        await self.rename_session(channel, ts, topic)
        await self.set_session_status(channel, ts, "processing")
        return ts

    async def flush_queue(self) -> int:
        """Re-send everything the outbox holds, oldest first. Returns the count.

        Send first, delete after. Deleting first and letting a failing send
        re-queue would keep the message but lose its place in the queue, so an
        outage silently reorders a conversation. Leaving the file untouched
        until Slack has taken it keeps the order, and keeps the message even if
        the process dies between the two. A failure stops the flush: everything
        behind it is newer, and sending it now would reorder just as badly.
        """
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

            # Out of budget means leave this and everything after it for the
            # next cycle, rather than burning through the queue.
            try:
                self._check_cap()
            except SlackRateCapped:
                log.info("[slack] rate cap reached while flushing, %s left queued", path.name)
                break

            if await self._send(topic, text) == "queued":
                # ``_send`` wrote a fresh copy on its way out; drop that and
                # keep the original, which still holds this message's place.
                self._drop_newest_queued()
                log.info("[slack] flush stopped at %s, still queued", path.name)
                break
            self._charge_rate()
            path.unlink()
            sent += 1
        return sent

    # -- reading -------------------------------------------------------

    def sessions(self) -> dict[str, dict[str, str]]:
        """The stored ``{topic: {thread_ts, channel, status}}`` map."""
        return self._read_sessions()

    def queued_count(self) -> int:
        """How many posts Slack has not taken yet.

        A non-zero count that never falls is the visible symptom of a Slack
        outage the brain is otherwise silent about -- ``post`` returns
        ``"queued"`` and the cycle carries on as if the thought was delivered.
        """
        if not self.queue_dir.exists():
            return 0
        return len(list(self.queue_dir.glob("*.json")))

    # -- status --------------------------------------------------------

    async def set_status(self, topic: str, status: Status) -> None:
        if status not in STATUSES:
            log.warning("[slack] ignoring unknown status %r for %s", status, topic)
            return
        sessions = self._read_sessions()
        session = sessions.get(topic)
        if not isinstance(session, dict):
            log.info("[slack] no session for topic %r, not setting status", topic)
            return
        # sessions.json is on a bind mount and has been hand-edited before; a
        # half-written entry must not raise out of a best-effort status call.
        channel = session.get("channel")
        thread_ts = session.get("thread_ts")
        if not channel or not thread_ts:
            log.warning("[slack] session for topic %r is malformed: %r", topic, session)
            return
        await self.set_session_status(str(channel), str(thread_ts), status)
        session["status"] = status
        self._write_sessions(sessions)

    async def close(self, topic: str) -> None:
        await self.set_status(topic, "closed")

    # -- the assistant thread -------------------------------------------

    def bind_chat(self, channel: str, thread_ts: str) -> None:
        """Point the ``chat`` session at the assistant thread Filip is in.

        Overwritten every time, so the newest thread is always where the
        conversation goes; an older one stays readable but stops receiving.
        """
        if not channel or not thread_ts:
            return
        sessions = self._read_sessions()
        current = sessions.get(CHAT_TOPIC)
        if isinstance(current, dict) and current.get("thread_ts") == thread_ts:
            return
        sessions[CHAT_TOPIC] = {
            "thread_ts": str(thread_ts),
            "channel": str(channel),
            "status": "active",
        }
        self._write_sessions(sessions)

    # -- session chrome ---------------------------------------------------

    async def rename_session(self, channel: str, thread_ts: str, title: str) -> None:
        """Title a thread, falling back to the pre-agents API."""
        await self._chrome_call(
            "agents.sessions.rename",
            {"channel_id": channel, "thread_ts": thread_ts, "title": title},
            "assistant.threads.setTitle",
            {"channel_id": channel, "thread_ts": thread_ts, "title": title},
        )

    async def set_session_status(self, channel: str, thread_ts: str, status: Status) -> None:
        await self._chrome_call(
            "agents.sessions.setStatus",
            {"channel_id": channel, "thread_ts": thread_ts, "status": status},
            "assistant.threads.setStatus",
            {
                "channel_id": channel,
                "thread_ts": thread_ts,
                "status": _LEGACY_STATUS.get(status, ""),
            },
        )

    # -- internals -----------------------------------------------------

    def _check_cap(self) -> None:
        """Raise if another post would exceed the cap. Charges nothing."""
        now = self.clock().timestamp()
        self._recent = [t for t in self._recent if now - t < WINDOW_S]
        if len(self._recent) >= self.max_per_hour:
            raise SlackRateCapped(
                f"slack post cap reached ({self.max_per_hour}/h); try again next cycle"
            )

    def _charge_rate(self) -> None:
        """Record one delivered post against the hour's budget."""
        self._recent.append(self.clock().timestamp())

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

    async def _chrome_call(
        self,
        method: str,
        payload: dict[str, Any],
        fallback: str,
        fallback_payload: dict[str, Any],
    ) -> None:
        """Title/status calls are cosmetic; a failure must not break a post.

        This workspace's app has no ``agents.sessions.*`` scope, so the modern
        call fails on every post. The pre-agents ``assistant.threads.*`` pair
        does the same job, and neither is worth a traceback: the Slack error
        string is the whole diagnosis, and a stack per post buries the log.
        """
        try:
            await self.client.api_call(method, json=payload)
            return
        except Exception as exc:  # noqa: BLE001 - cosmetic; any failure must leave the post alone
            log.info("[slack] %s failed (%s), trying %s", method, _slack_error(exc), fallback)
        try:
            await self.client.api_call(fallback, json=fallback_payload)
        except Exception as exc:  # noqa: BLE001 - cosmetic; any failure must leave the post alone
            log.info("[slack] %s failed (%s)", fallback, _slack_error(exc))

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

    def _drop_newest_queued(self) -> None:
        """Remove the copy ``_send`` just enqueued, keeping the original."""
        if not self.queue_dir.exists():
            return
        files = sorted(self.queue_dir.glob("*.json"))
        if files:
            files[-1].unlink(missing_ok=True)

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
        # Slack's Agents pane sends these when Filip opens or re-points it.
        self.app.event("assistant_thread_started")(self.on_thread_started)
        self.app.event("assistant_thread_context_changed")(self.on_ignored)

    async def on_message(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        # Every drop is logged: a DM that silently goes nowhere is
        # indistinguishable from a dead socket without this. The text itself is
        # never logged -- only the routing fields that decided the drop.
        if (
            event.get("channel_type") != "im"
            or event.get("user") != self.user_id
            # Our own posts come back as messages; so do edits and joins.
            or event.get("bot_id")
            or event.get("subtype")
        ):
            log.info(
                "[slack] dropped message: channel_type=%s user=%s expected_user=%s "
                "subtype=%s bot=%s",
                event.get("channel_type"),
                event.get("user"),
                self.user_id,
                event.get("subtype"),
                event.get("bot_id"),
            )
            return
        body = str(event.get("text") or "").strip()
        if not body:
            log.info(
                "[slack] dropped message: channel_type=%s user=%s expected_user=%s "
                "subtype=%s bot=%s (empty text)",
                event.get("channel_type"),
                event.get("user"),
                self.user_id,
                event.get("subtype"),
                event.get("bot_id"),
            )
            return

        thread_ts = str(event.get("thread_ts") or "")
        topic = self.out.topic_for_thread(thread_ts)
        if thread_ts:
            # In the agent UI every message Filip types is a reply inside his
            # assistant thread. Binding here -- not only on the started event --
            # is what keeps replies landing in the thread he is looking at
            # after a restart, when the started event is long gone.
            self.out.bind_chat(channel=str(event.get("channel") or ""), thread_ts=thread_ts)
            if topic is None:
                topic = CHAT_TOPIC
        log.info("[slack] note from filip (%d chars, topic=%s)", len(body), topic)
        if topic is not None:
            body = f"topic: {topic}\n{body}"
        self.brain.drop_note(NOTE_SENDER, body)
        self.wake("brain")

    async def on_reaction(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        if event.get("user") != self.user_id:
            return
        item = event.get("item")
        proposal = await self.approvals.on_reaction(
            str((item or {}).get("ts", "")), str(event.get("reaction", ""))
        )
        # A reaction resolves a proposal and drops a note about it. Without a
        # wake the brain reads that note whenever its heartbeat next comes
        # round -- up to half an hour after Filip approved something.
        if proposal is not None:
            self.wake("brain")

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

    async def on_thread_started(self, event: dict, ack: Ack = None) -> None:
        """Filip opened a new assistant thread: that is where we now talk."""
        await _ack(ack)
        thread = event.get("assistant_thread") or {}
        if thread.get("user_id") != self.user_id:
            log.info("[slack] assistant thread for another user, ignoring")
            return
        channel = str(thread.get("channel_id") or "")
        thread_ts = str(thread.get("thread_ts") or "")
        self.out.bind_chat(channel=channel, thread_ts=thread_ts)
        # Prompt chips are pure decoration; a workspace without the scope must
        # not lose the binding above over them.
        try:
            await self.out.client.api_call(
                "assistant.threads.setSuggestedPrompts",
                json={
                    "channel_id": channel,
                    "thread_ts": thread_ts,
                    "prompts": SUGGESTED_PROMPTS,
                },
            )
        except Exception as exc:  # noqa: BLE001 - chips are decoration; keep the binding
            log.info("[slack] setSuggestedPrompts failed (%s)", _slack_error(exc))

    async def on_ignored(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        log.debug("[slack] ignored event: %s", event.get("type"))


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


def _slack_error(exc: Exception) -> str:
    """The Slack ``error`` string if this is a SlackApiError, else the message.

    ``SlackApiError`` carries the useful part in ``response["error"]``; its
    ``str()`` is a paragraph of the whole HTTP body.
    """
    response = getattr(exc, "response", None)
    try:
        error = response["error"] if response is not None else None
    except (TypeError, KeyError):
        error = None
    return str(error or exc)


def _atomic_write(path: Path, body: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
