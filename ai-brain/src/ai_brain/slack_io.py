"""The brain's one window onto a human, over Slack Socket Mode.

Every topic the brain talks about becomes a plain Slack thread: a top-level
message in Filip's DM, named after the topic only in its own text, with any
follow-up posted as a reply underneath it. Outbound goes through
:class:`SlackOut`, inbound through :class:`SlackIn`, and nothing else in the
process touches Slack.

Four properties shape the code more than the API does:

* **A topic is a thread, and the mapping outlives the process.** ``sessions.json``
  next to the brain's memory holds ``{topic: {thread_ts, channel}}``, written
  atomically. A restart mid-conversation keeps replying in the same thread
  rather than opening a second one about the same thing.
* **Every topic gets its own thread, always.** Filip's own side of the
  conversation lives under the reserved ``chat`` topic -- the thread Slack's
  agent UI opens when he starts talking -- but nothing else piggybacks on it.
  An earlier version had a new topic alias itself onto whatever thread ``chat``
  last pointed at, on the reasoning that posting top-level might put a reply in
  a thread he was not looking at. In practice ``chat`` is rebound every time he
  opens a fresh assistant conversation and never expires on its own, so days
  later every unrelated topic the brain ever raised was still landing in
  whichever thread happened to be first -- untitled, undated, and impossible to
  find without Slack's global Threads view. A topic is worth a thread of its
  own.
* **A topic's first message is an ordinary top-level post, not an Agents
  session.** An earlier version also called ``agents.sessions.rename`` /
  ``agents.sessions.setStatus`` to title the thread and show a status dot in
  Slack's Agents sidebar. Those methods are not part of this workspace app's
  scope (there is no scope that grants them -- they 404 in Slack's own public
  docs) and always failed, silently falling back to the legacy
  ``assistant.threads.*`` pair. Worse, once Slack's client treats a thread as
  belonging to an Agents *session* rather than an ordinary DM thread, a push
  notification for a reply in it stops deep-linking to the message and instead
  drops Filip on the Agents History list -- so the fix was to stop trying to
  make it an Agents session at all. A topic's thread is just a normal thread;
  Slack always delivers and deep-links normal thread notifications correctly.
* **The agent cannot spam.** A sliding hour window caps posts; over it,
  ``post`` raises :class:`SlackRateCapped` and the tool turns that into an
  error the model reads. Better to refuse loudly inside the cycle than to let
  a confused loop fill a DM.
* **Slack being down is not a reason to lose a thought.** A call that fails
  three retries is written to ``outbox/slack/<ts>.json`` and ``post`` returns
  ``"queued"``; ``flush_queue`` drains it oldest-first on a later cycle, and
  stops at the first message Slack still will not take rather than reordering
  the ones behind it.
* **A pending message gets a :loading: reaction, cleared by the reply.**
  ``mark_pending`` reacts on Filip's own message the moment it becomes an
  inbox note; the topic's next successful ``post`` clears it. There is no
  cycle-status plumbing from :mod:`ai_brain.loop` for this -- a cycle can
  answer several topics' notes at once and ``loop.py`` has no notion of Slack
  at all, so "did this topic get a reply" is answered here, from the posts
  this module actually sent, not from the loop's own idea of how it went. A
  cycle that consumed the note and ended without a reply calls
  ``mark_unanswered``, which swaps the spinner for :red_circle: at once.
  ``check_watchdog`` is only the backstop for a cycle that never gets that
  far (a hang, a dead process), so ``LOADING_TIMEOUT_S`` is long: a lan:
  cycle legitimately runs half an hour, and a 15-minute watchdog used to
  paint a slow-but-successful reply red.

The outbox policy below exists because of one week (2026-11-01..08): the brain
sent Filip 13 DMs and got no replies. Nine were replies inside threads from
Sep/Oct, which never show in the DM's main view; four went out between 01:00
and 04:00; several were the brain correcting its own previous message, three
messages for one finding; and the questions among them went unanswered and
were read back as "low priority". Each rule answers one of those:

* **A reply into a stale thread is broadcast.** ``sessions.json`` also keeps
  ``last_ts``/``last_at`` (the bot's own last post in the topic) and
  ``owner_at`` (Filip's last message there). A post into a thread whose newest
  activity is older than ``stale_thread_h`` goes out with
  ``reply_broadcast=True``, so it also lands in the DM's main view instead of
  only in a thread nobody has open. A session written before these fields
  existed falls back to its ``thread_ts`` -- Slack's own epoch timestamp --
  which is exactly the Sep/Oct case.
* **Quiet hours hold, they do not drop.** ``hold`` writes a post to
  ``outbox/slack/held/`` instead of sending it (the decision of *what* to
  hold is the ``slack_post`` tool's: urgent posts and owed replies bypass).
  ``release_held`` runs on a minute tick and, the first time it sees quiet
  hours over, sends one top-level digest and then each item into its own
  thread, unbroadcast -- the digest is the notification, the thread posts are
  what keeps a topic's history and reply routing intact.
* **A correction edits, it does not stack.** ``edit_last`` rewrites the
  topic's last bot message (``✏️ Updated: ...``) if it is under a day old and
  Filip has not spoken in the thread since -- editing something he already
  answered would rewrite what he was answering.
* **A question is tracked until it is answered.** A question post carries
  Yes/No buttons and records ``asked_at``; Filip writing in the thread or
  clicking a button sets ``answered_at``. ``open_questions`` is what lets the
  brain tell "no answer yet" apart from "no".
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ai_brain.memory import OWNER_SENDER

log = logging.getLogger(__name__)

# Bolt hands listeners an ack callable; the tests call the handlers without one.
Ack = Callable[[], Awaitable[None]] | None

MAX_PER_HOUR = 20
WINDOW_S = 3600.0
BACKOFFS: tuple[float, ...] = (0.5, 1.0, 2.0)
NOTE_SENDER = OWNER_SENDER
CHAT_TOPIC = "chat"
LOADING_REACTION = "loading"
ERROR_REACTION = "red_circle"
# How long a topic may sit with an unanswered :loading: before check_watchdog
# gives up on it and marks it failed. Only a backstop -- the loop marks an
# unanswered note itself the moment its cycle ends (``mark_unanswered``). A note
# that lands mid-cycle waits out that cycle and then a whole one of its own, and
# on the lan: provider that was measured at ~33 minutes; the old 15 minutes
# painted a slow-but-fine reply red.
LOADING_TIMEOUT_S = 3600.0
LOCAL_TZ = ZoneInfo("Europe/Stockholm")
DEFAULT_STALE_THREAD_H = 72
# How old the bot's last message may be and still be edited in place by a
# correction. Past a day the original has been read (or slept on), and a
# silent edit of it is more likely to be missed than a fresh message.
EDIT_WINDOW_S = 24 * 3600
UPDATED_PREFIX = "✏️ Updated: "
# Yes/No answer buttons on a question post. Distinct from the approval
# buttons' ids (approvals.APPROVE_ACTION/REJECT_ACTION): Bolt routes by
# action_id, and an answer must never be mistaken for approving a proposal.
ANSWER_YES_ACTION = "answer_yes"
ANSWER_NO_ACTION = "answer_no"
ANSWER_ACTIONS = {ANSWER_YES_ACTION: "yes", ANSWER_NO_ACTION: "no"}
QUESTION_EXCERPT_CHARS = 200
DIGEST_EXCERPT_CHARS = 160
SUGGESTED_PROMPTS = [
    {"title": "Status", "message": "Vad jobbar du med just nu?"},
    {"title": "El", "message": "Hur mycket el producerar vi just nu?"},
]


class SlackRateCapped(Exception):
    """Raised when a post would exceed the hourly cap. Never swallowed here."""


# A trailing "-2026-09-24"-style date, stripped repeatedly (a topic is never
# chained deep enough to need more than one, but an unbounded loop costs
# nothing extra and is simpler than justifying a count).
_TRAILING_DATE = re.compile(r"(-\d{4}-\d{2}-\d{2})+$")
# A single trailing bare index, e.g. the "-2" in "pool-pump-2". Applied at
# most once and only when the slug has a segment left over afterwards -- see
# _strip_trailing_index -- so "floor-1" and "floor-2" (two segments each,
# nothing left after stripping) are never conflated into plain "floor".
_TRAILING_INDEX = re.compile(r"-\d{1,2}$")
_SEPARATORS = re.compile(r"[\s_]+")
_REPEATED_DASHES = re.compile(r"-{2,}")


def _strip_trailing_index(slug: str) -> str:
    """Remove a single trailing "-N" index, but only if a real name is left.

    "pool-pump-2" (three segments) strips to "pool-pump" -- a genuine index
    suffix on a real topic. "floor-1" (two segments) does not strip at all:
    stripping it would leave the single segment "floor", which is indistinguishable
    from a topic that was always just called "floor", and would merge "floor-1"
    and "floor-2" into the same session. The rule is the same either way: an
    index suffix only counts as one once at least two segments remain under it.
    """
    stripped = _TRAILING_INDEX.sub("", slug)
    if stripped == slug:
        return slug
    return stripped if stripped.count("-") >= 1 else slug


def normalize_topic(topic: str) -> str:
    """A topic's canonical form, for matching -- never for display or storage.

    Lowercases, turns whitespace/underscores into ``-``, strips a trailing
    date (``-2026-09-24``) suffix and (see ``_strip_trailing_index``) a
    trailing bare index, and collapses repeated dashes left behind by any of
    that. ``think-cycle-2026-09-24`` and ``Think Cycle`` both normalize to
    ``think-cycle``; ``pool-pump-bug`` normalizes to itself, since ``bug`` is
    not a date or a bare index -- the prefix match in
    ``SlackOut.resolve_topic`` is what folds that one in.
    """
    slug = _SEPARATORS.sub("-", topic.strip().lower())
    slug = _TRAILING_DATE.sub("", slug)
    slug = _strip_trailing_index(slug)
    slug = _REPEATED_DASHES.sub("-", slug).strip("-")
    return slug


# A topic must have at least this many '-'-separated segments to be eligible
# as the *shorter* side of a prefix match -- see _is_new_variant_of. Without
# this, a generic single-word topic like "house" or "energy" would silently
# swallow "house-ops-findings" or "energy-prices" the first time either was
# posted, which is worse than the sprawl this was meant to fix.
MIN_PREFIX_SEGMENTS = 2


def _is_new_variant_of(new: str, existing: str) -> bool:
    """Whether normalized ``new`` is a longer '-'-boundary variant of an
    existing, already-normalized topic ``existing`` -- e.g. ``new`` is
    "pool-pump-bug" and ``existing`` is "pool-pump".

    Deliberately one-directional: routing "pool-pump-bug" into an existing
    "pool-pump" thread is the sprawl this exists to fix, but routing a new,
    short "pool" into an existing, longer "pool-pump-bug" is not -- "pool" is
    plausibly its own subject, and folding every future short name into
    whatever long-tailed topic happened to be created first would make topics
    increasingly impossible to predict. ``existing`` also has to clear
    ``MIN_PREFIX_SEGMENTS``, so a single generic word is never a match target.
    """
    if not new or not existing or new == existing:
        return False
    if existing.count("-") + 1 < MIN_PREFIX_SEGMENTS:
        return False
    return new.startswith(existing + "-")


def quiet_until(now: datetime, quiet_hours: tuple[int, int] | None) -> datetime | None:
    """When the quiet window ``now`` falls in ends (local time), or None.

    ``quiet_hours`` is ``(start, end)`` in Stockholm hours, end exclusive; a
    window may wrap midnight (``22-07``) or not (``01-06``). The end is built
    from the local *date* rather than by adding hours to ``now``, so a DST
    change inside the window still ends it at the wall-clock hour Filip set.
    """
    if quiet_hours is None:
        return None
    start, end = quiet_hours
    local = now.astimezone(LOCAL_TZ)
    hour = local.hour
    quiet = (hour >= start or hour < end) if start > end else (start <= hour < end)
    if not quiet:
        return None
    day = local.date() if hour < end else local.date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, end, tzinfo=LOCAL_TZ)


def question_blocks(text: str, topic: str) -> list[dict]:
    """Block Kit for a question: the text, then Yes / No buttons.

    The buttons' ``value`` is the topic, so a click is routed back to the
    right thread even when the message is not the topic's thread root.
    """
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Yes"},
                    "action_id": ANSWER_YES_ACTION,
                    "value": topic,
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "No"},
                    "action_id": ANSWER_NO_ACTION,
                    "value": topic,
                },
            ],
        },
    ]


def _excerpt(text: str, limit: int) -> str:
    """The first non-empty line of ``text``, cut at ``limit`` characters."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _parse_time(raw: object) -> datetime | None:
    """An ISO timestamp from sessions.json, or None if absent or unreadable."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class SlackOut:
    """Posts into per-topic threads, and keeps the topic->thread map."""

    def __init__(
        self,
        client: Any,
        user_id: str,
        brain: Any,
        clock: Callable[[], datetime],
        max_per_hour: int = MAX_PER_HOUR,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        stale_thread_h: float = DEFAULT_STALE_THREAD_H,
        # Off unless the caller passes a window: ``start_slack`` hands in the
        # configured one (default 22-07). Off by default here so a test, or
        # any caller that has not thought about it, never sees a post held.
        quiet_hours: tuple[int, int] | None = None,
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.brain = brain
        self.clock = clock
        self.max_per_hour = max_per_hour
        self.sleep = sleep
        self.stale_thread_s = float(stale_thread_h) * 3600
        self.quiet_hours = quiet_hours
        self._channel: str | None = None
        self._recent: list[float] = []
        # topic -> (channel, message_ts, marked_at). One entry per topic: a
        # second inbound message on the same topic before the first got a
        # reply simply re-marks the same spinner rather than stacking one per
        # message, since a reply answers the topic, not a specific message.
        self._pending: dict[str, tuple[str, str, float]] = {}

    # -- paths ---------------------------------------------------------

    @property
    def sessions_path(self) -> Path:
        return self.brain.root / "sessions.json"

    @property
    def queue_dir(self) -> Path:
        return self.brain.outbox_dir / "slack"

    @property
    def held_dir(self) -> Path:
        # A subdirectory of the retry queue, which ``flush_queue`` globs
        # non-recursively -- so a held post is never mistaken for a failed one
        # and sent in the middle of the night by the retry tick.
        return self.queue_dir / "held"

    # -- the pending spinner ---------------------------------------------

    async def mark_pending(self, topic: str, channel: str, ts: str) -> None:
        """React :loading: on Filip's message while its topic is worked on.

        Called from ``SlackIn.on_message`` the moment a DM becomes an inbox
        note -- before the cycle that will answer it has even been woken.
        Reacting is best-effort: a failure here must not stop the note from
        being dropped and the brain woken, so it is only logged.
        """
        self._pending[topic] = (channel, ts, self.clock().timestamp())
        try:
            await self.client.reactions_add(channel=channel, timestamp=ts, name=LOADING_REACTION)
        except Exception as exc:  # noqa: BLE001 - the reaction is decoration
            log.info("[slack] reactions_add failed (%s)", _slack_error(exc))

    async def check_watchdog(self) -> None:
        """Swap a spinner stuck past ``LOADING_TIMEOUT_S`` for a red circle.

        Covers the case ``_send`` cannot: a cycle that errors, times out, or
        hangs before ever calling ``post`` for the topic leaves nothing here
        to clear the reaction. Each stale entry is dropped from ``_pending``
        the moment it is handled, so a topic is only ever marked failed once
        -- a later reply still lands as a normal post, it just no longer
        clears anything, since there is nothing left pending to clear.
        """
        now = self.clock().timestamp()
        stale = [
            topic
            for topic, (_, _, marked_at) in self._pending.items()
            if now - marked_at >= LOADING_TIMEOUT_S
        ]
        for topic in stale:
            await self.mark_unanswered(topic)

    async def mark_unanswered(self, topic: str) -> None:
        """Swap the topic's :loading: for :red_circle:, if one is pending.

        Called by the loop when a cycle archives Filip's note without having
        replied on its topic, and by ``check_watchdog``. A no-op for a topic
        with nothing pending -- e.g. one a queued post already cleared.
        """
        pending = self._pending.pop(topic, None)
        if pending is None:
            return
        channel, ts, _ = pending
        await self._swap_reaction(channel, ts, LOADING_REACTION, ERROR_REACTION)

    async def _clear_pending(self, topic: str) -> None:
        """Remove the topic's :loading: reaction, if one is still pending."""
        pending = self._pending.pop(topic, None)
        if pending is None:
            return
        channel, ts, _ = pending
        try:
            await self.client.reactions_remove(channel=channel, timestamp=ts, name=LOADING_REACTION)
        except Exception as exc:  # noqa: BLE001 - the reaction is decoration
            log.info("[slack] reactions_remove failed (%s)", _slack_error(exc))

    async def _swap_reaction(self, channel: str, ts: str, old: str, new: str) -> None:
        try:
            await self.client.reactions_remove(channel=channel, timestamp=ts, name=old)
        except Exception as exc:  # noqa: BLE001 - the reaction is decoration
            log.info("[slack] reactions_remove failed (%s)", _slack_error(exc))
        try:
            await self.client.reactions_add(channel=channel, timestamp=ts, name=new)
        except Exception as exc:  # noqa: BLE001 - the reaction is decoration
            log.info("[slack] reactions_add failed (%s)", _slack_error(exc))

    # -- resolving an approval message ----------------------------------

    async def mark_resolved(self, channel: str, ts: str, text: str) -> None:
        """Replace an approval message's Approve/Reject buttons with a verdict.

        Called once a click or reaction has actually resolved the proposal
        (``on_button``/``on_reaction`` in ``SlackIn`` both return the settled
        ``Proposal``, and ``text`` is ``approvals.resolved_text`` of it) --
        never speculatively, so a click that turned out to be a no-op
        (already resolved, expired) leaves the message alone rather than
        overwriting it with a stale-looking edit. ``chat_update`` replaces
        the whole message, buttons and all -- there is no way to edit only
        the blocks and leave a fallback ``text`` that no longer matches, so
        both become the same resolved text. Best-effort, like the reactions
        above -- a failed edit must not stop the proposal from having run.
        """
        try:
            await self.client.chat_update(
                channel=channel,
                ts=ts,
                text=text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            )
        except Exception as exc:  # noqa: BLE001 - best-effort, like the reactions above
            log.info("[slack] chat_update failed (%s)", _slack_error(exc))

    # -- posting -------------------------------------------------------

    async def post(
        self,
        topic: str,
        text: str,
        blocks: list[dict] | None = None,
        *,
        question: bool = False,
    ) -> str:
        """Post ``text`` under ``topic``. Returns the ts, or ``"queued"``.

        The cap is *checked* before the send and *charged* after it succeeds.
        Charging up front means a Slack outage burns the hour's whole budget on
        posts that only reached the retry queue, and the flush that finally
        delivers them is then refused for being over cap -- the outage would
        silence the brain for an hour after Slack came back.

        ``blocks`` is Block Kit content (e.g. approval buttons); ``text`` is
        still sent as the fallback string Slack shows in notifications and to
        clients that do not render blocks. ``topic`` is resolved against
        existing sessions first (see ``resolve_topic``), so ``pool-pump-bug``
        lands in an existing ``pool-pump`` thread instead of opening its own.

        ``question`` adds Yes/No buttons (``question_blocks``) and starts
        tracking the topic as an open question -- see ``open_questions``.
        """
        topic = self.resolve_topic(topic)
        self._check_cap()
        ts = await self._send(topic, text, blocks, question=question)
        if ts != "queued":
            self._charge_rate()
        return ts

    async def edit_last(self, topic: str, text: str, *, question: bool = False) -> str | None:
        """Rewrite the topic's last bot message as a correction, if allowed.

        Returns the edited message's ts, or None when the caller should post
        normally instead: no bot message recorded for the topic, the last one
        older than ``EDIT_WINDOW_S``, Filip having written in the thread since
        (he may be answering the very thing this would rewrite), or Slack
        refusing the edit. The three-messages-for-one-finding pattern this
        replaces was each a fresh notification for what was really one
        thought; an edit is not a notification at all, so it is not charged
        against the hourly cap either.

        ``chat_update`` replaces the whole message, so ``blocks`` are always
        sent -- leaving them out would keep an old question's buttons under a
        text that no longer asks anything.
        """
        topic = self.resolve_topic(topic)
        sessions = self._read_sessions()
        session = sessions.get(topic)
        if not session or not session.get("last_ts"):
            return None
        now = self.clock()
        last_at = _parse_time(session.get("last_at"))
        if last_at is None or (now - last_at).total_seconds() >= EDIT_WINDOW_S:
            return None
        owner_at = _parse_time(session.get("owner_at"))
        if owner_at is not None and owner_at >= last_at:
            return None
        ts = str(session["last_ts"])
        channel = str(session.get("channel") or "")
        new_text = UPDATED_PREFIX + text
        blocks = (
            question_blocks(new_text, topic)
            if question
            else [{"type": "section", "text": {"type": "mrkdwn", "text": new_text}}]
        )
        try:
            await self.client.chat_update(channel=channel, ts=ts, text=new_text, blocks=blocks)
        except Exception as exc:  # noqa: BLE001 - falling back to a normal post loses nothing
            log.info("[slack] chat_update for %s failed (%s), posting instead", topic, _slack_error(exc))
            return None
        if question:
            self._record_question(session, ts, text, now)
        elif session.get("question_ts") == ts:
            # The buttons are gone from that message now; it no longer asks.
            for key in ("asked_at", "question_ts", "question_text"):
                session.pop(key, None)
        sessions[topic] = session
        self._write_sessions(sessions)
        await self._clear_pending(topic)
        return ts

    async def _send(
        self,
        topic: str,
        text: str,
        blocks: list[dict] | None = None,
        *,
        question: bool = False,
        allow_broadcast: bool = True,
    ) -> str:
        """Post without touching the rate cap. Returns the ts, or ``"queued"``.

        Every topic other than ``chat`` gets its own thread, always -- even
        while an assistant conversation is open. Piggybacking a new topic onto
        whatever thread ``chat`` last pointed at used to be the rule (to avoid
        opening a thread Filip was not looking at), but ``chat`` is rebound
        every time he opens a fresh assistant conversation and never expires
        on its own: days later, every unrelated topic the brain raised was
        still landing in that first stale thread, untitled and impossible to
        find without digging into Slack's global Threads view. A topic is
        worth a thread of its own -- an ordinary top-level post, not an Agents
        session; see the module docstring for why the session chrome that used
        to follow this post (naming and status-dotting it) is gone.

        A reply into a thread with no activity for ``stale_thread_s`` is sent
        with ``reply_broadcast`` -- see the module docstring. Decided here, at
        send time, rather than when the post was first asked for, so a post
        that sat in the retry queue is judged on the thread as it is now.
        ``allow_broadcast=False`` is for ``release_held``, whose digest has
        already put the news in the main view once.
        """
        sessions = self._read_sessions()
        session = sessions.get(topic)
        now = self.clock()
        if question and blocks is None:
            blocks = question_blocks(text, topic)
        try:
            # A session records the channel it lives in; the assistant thread
            # is not always in the DM we would open ourselves.
            channel = str((session or {}).get("channel") or "") or await self._open_dm()
            thread_ts = session["thread_ts"] if session else None
            kwargs = {"channel": channel, "text": text, "thread_ts": thread_ts}
            if blocks is not None:
                kwargs["blocks"] = blocks
            if allow_broadcast and session is not None and self._is_stale(session, now):
                kwargs["reply_broadcast"] = True
            response = await self._retry(self.client.chat_postMessage, **kwargs)
        except Exception:
            log.warning("[slack] post to %s failed, queueing", topic, exc_info=True)
            self._enqueue(topic, text, blocks, question=question)
            return "queued"

        ts = str(response["ts"])
        if session is None:
            # A brand new topic: this post is the thread's own top-level
            # message, so the thread is recognisable (and its notification
            # deep-links correctly) without any further Slack call.
            session = {"thread_ts": ts, "channel": channel}
        session["last_ts"] = ts
        session["last_at"] = _iso(now)
        if question:
            self._record_question(session, ts, text, now)
        sessions[topic] = session
        self._write_sessions(sessions)
        # A real reply landed for this topic: whatever spinner was left on
        # Filip's message is answered now, watchdog or not.
        await self._clear_pending(topic)
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
                blocks = item.get("blocks")
                question = bool(item.get("question"))
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

            if await self._send(topic, text, blocks, question=question) == "queued":
                # ``_send`` wrote a fresh copy on its way out; drop that and
                # keep the original, which still holds this message's place.
                self._drop_newest_queued()
                log.info("[slack] flush stopped at %s, still queued", path.name)
                break
            self._charge_rate()
            path.unlink()
            sent += 1
        return sent

    # -- quiet hours -----------------------------------------------------

    def quiet_until(self) -> datetime | None:
        """When the current quiet window ends (Stockholm time), or None."""
        return quiet_until(self.clock(), self.quiet_hours)

    def hold(
        self,
        topic: str,
        text: str,
        *,
        question: bool = False,
        replaces_last: bool = False,
    ) -> dict[str, Any]:
        """Keep a post back until quiet hours end. Returns what happened.

        Charged against the hourly cap exactly as a sent post would be: the
        cap is there to stop a confused loop, and a loop that fills the held
        directory overnight would otherwise all arrive at 07:00 as one huge
        digest. ``release_held`` does not charge again.

        A ``replaces_last`` correction of something that is itself still held
        just replaces that held text -- the overnight version of editing
        rather than stacking. Otherwise the flag is kept and applied on
        release (``edit_last`` against whatever the topic's last sent message
        is by then).
        """
        topic = self.resolve_topic(topic)
        self._check_cap()
        self._charge_rate()
        until = self.quiet_until()
        if replaces_last:
            for path in reversed(sorted(self.held_dir.glob("*.json"))):
                item = self._read_item(path)
                if item is None or item.get("topic") != topic:
                    continue
                item["text"] = text
                item["question"] = bool(item.get("question")) or question
                _atomic_write(path, json.dumps(item))
                return {"topic": topic, "replaced_held": True, "until": until}
        self._write_item(
            self.held_dir,
            {
                "topic": topic,
                "text": text,
                "question": question,
                "replaces_last": replaces_last,
                "held_at": _iso(self.clock()),
            },
        )
        return {"topic": topic, "replaced_held": False, "until": until}

    def held_count(self) -> int:
        if not self.held_dir.exists():
            return 0
        return len(list(self.held_dir.glob("*.json")))

    async def release_held(self) -> int:
        """Once quiet hours are over, send what was held. Returns the count.

        One top-level digest first -- a single notification at the end of the
        night instead of one per thought, and visible in the DM's main view
        where a reply inside an old thread is not. Then each item goes into
        its own topic thread, unbroadcast: the digest already announced it,
        and the thread post is what keeps that topic's history whole and
        makes Filip's reply there route back to the right topic.

        A digest Slack refuses leaves everything held for the next tick. A
        thread post that fails falls into the ordinary retry queue via
        ``_send``, so the held copy is dropped either way. A process that dies
        between the digest and the thread posts sends the digest again next
        time -- a duplicate summary is a better failure than a lost message.
        """
        if self.quiet_until() is not None or not self.held_dir.exists():
            return 0
        items: list[tuple[Path, dict]] = []
        for path in sorted(self.held_dir.glob("*.json")):
            item = self._read_item(path)
            if item is None or "topic" not in item or "text" not in item:
                log.warning("[slack] discarding unreadable held post %s", path.name)
                path.unlink(missing_ok=True)
                continue
            items.append((path, item))
        if not items:
            return 0

        try:
            channel = await self._open_dm()
            await self._retry(
                self.client.chat_postMessage, channel=channel, text=self._digest_text(items)
            )
        except Exception:
            log.warning("[slack] digest of %d held posts failed, keeping them", len(items), exc_info=True)
            return 0

        for path, item in items:
            topic, text = str(item["topic"]), str(item["text"])
            question = bool(item.get("question"))
            edited = None
            if item.get("replaces_last"):
                edited = await self.edit_last(topic, text, question=question)
            if edited is None:
                await self._send(topic, text, question=question, allow_broadcast=False)
            path.unlink(missing_ok=True)
        return len(items)

    @staticmethod
    def _digest_text(items: list[tuple[Path, dict]]) -> str:
        """One bullet per held item, grouped under its topic, in held order."""
        grouped: dict[str, list[dict]] = {}
        for _, item in items:
            grouped.setdefault(str(item["topic"]), []).append(item)
        noun = "update" if len(items) == 1 else "updates"
        lines = [f"☀️ *Overnight: {len(items)} {noun}* -- details in each topic's thread."]
        for topic, entries in grouped.items():
            lines.append(f"*{topic}*")
            for entry in entries:
                mark = "❓ " if entry.get("question") else ""
                lines.append(f"• {mark}{_excerpt(str(entry['text']), DIGEST_EXCERPT_CHARS)}")
        return "\n".join(lines)

    # -- question state ----------------------------------------------------

    def note_owner_message(self, topic: str) -> None:
        """Filip wrote in ``topic``'s thread: record it, and answer any question.

        ``owner_at`` is what ``_is_stale`` and ``edit_last`` read; a message
        in the thread answers whatever question the topic has open, whatever
        it says -- the brain reads the words, this only tracks that he spoke.
        """
        sessions = self._read_sessions()
        session = sessions.get(topic)
        if not isinstance(session, dict):
            return
        now = _iso(self.clock())
        session["owner_at"] = now
        if session.get("asked_at") and not session.get("answered_at"):
            session["answered_at"] = now
        self._write_sessions(sessions)

    def mark_answered(self, topic: str, message_ts: str, answer: str) -> bool:
        """Record a button answer. False if this message was already answered.

        Keyed on the message, not the topic: a thread reply sets
        ``answered_at`` too, and the button under the question must still
        count after that -- only a second click on the same message (before
        ``chat_update`` has removed the buttons) is a duplicate.
        """
        sessions = self._read_sessions()
        session = sessions.get(topic)
        if not isinstance(session, dict):
            return True
        if session.get("button_answered_ts") == message_ts:
            return False
        now = _iso(self.clock())
        session["button_answered_ts"] = message_ts
        session["answer"] = answer
        session["owner_at"] = now
        session["answered_at"] = now
        self._write_sessions(sessions)
        return True

    def open_questions(self) -> list[dict[str, Any]]:
        """Questions asked and not yet answered, oldest first."""
        now = self.clock()
        open_: list[dict[str, Any]] = []
        for topic, session in self._read_sessions().items():
            if not isinstance(session, dict) or session.get("answered_at"):
                continue
            asked = _parse_time(session.get("asked_at"))
            if asked is None:
                continue
            open_.append(
                {
                    "topic": topic,
                    "asked_at": session["asked_at"],
                    "age_days": round((now - asked).total_seconds() / 86400, 1),
                    "text": str(session.get("question_text") or ""),
                }
            )
        open_.sort(key=lambda q: q["asked_at"])
        return open_

    @staticmethod
    def _record_question(session: dict, ts: str, text: str, now: datetime) -> None:
        session["asked_at"] = _iso(now)
        session["question_ts"] = ts
        session["question_text"] = _excerpt(text, QUESTION_EXCERPT_CHARS)
        for key in ("answered_at", "answer", "button_answered_ts"):
            session.pop(key, None)

    def _is_stale(self, session: dict, now: datetime) -> bool:
        """Whether a thread's newest activity is older than ``stale_thread_s``.

        Newest of: the bot's last post, Filip's last message, and the thread
        root's own ts (Slack's epoch seconds) -- the last being all a session
        written before this policy has, which is what makes those old Sep/Oct
        threads count as stale straight away rather than after a fresh post
        has reset them.
        """
        moments = [_parse_time(session.get(k)) for k in ("last_at", "owner_at")]
        latest = max((m.timestamp() for m in moments if m is not None), default=None)
        try:
            root = float(session.get("thread_ts") or 0)
        except (TypeError, ValueError):
            root = 0.0
        latest = max(latest or 0.0, root)
        return latest > 0 and now.timestamp() - latest > self.stale_thread_s

    # -- topic routing ---------------------------------------------------

    def resolve_topic(self, topic: str) -> str:
        """The existing session topic ``topic`` should post into, or itself.

        Two tries, in order: an exact match on ``normalize_topic`` (the common
        case -- ``Think Cycle`` finds ``think-cycle``, and covers a literal
        exact match too since a topic normalizes to itself); and a
        '-'-boundary prefix match, one direction only -- a new, longer
        ``pool-pump-bug`` folds into an existing, shorter ``pool-pump``, never
        the reverse (see ``_is_new_variant_of``: a new short topic must not be
        swallowed by whatever long-tailed existing topic it happens to prefix,
        and the existing side must have at least ``MIN_PREFIX_SEGMENTS``
        segments, so a single generic word like "house" is never a match
        target). No match creates a new session under ``topic`` unchanged,
        exactly like before this existed. ``CHAT_TOPIC`` is never a match
        target or a candidate: it is not a subject, it is Filip's own
        reserved thread.
        """
        if topic == CHAT_TOPIC:
            return topic
        sessions = self._read_sessions()
        if topic in sessions:
            return topic
        candidates = [t for t in sessions if t != CHAT_TOPIC]
        if not candidates:
            return topic
        normalized = normalize_topic(topic)
        exact = [t for t in candidates if normalize_topic(t) == normalized]
        if exact:
            return min(exact, key=len)
        variant_of = [
            t for t in candidates if _is_new_variant_of(normalized, normalize_topic(t))
        ]
        if variant_of:
            return min(variant_of, key=len)
        return topic

    def recent_topics(self, limit: int = 15) -> list[str]:
        """The most recently active session topics, newest first.

        For the ``slack_post``/``propose`` tool descriptions: naming the
        topics that already exist is what lets the model reuse one instead of
        inventing a near-duplicate, which is the whole reason
        ``resolve_topic`` exists in the first place -- prevention alongside
        the cure. Ordered by ``thread_ts``, Slack's own message timestamp, so
        this is "most recently created", not "most recently posted to" (the
        session map does not record the latter); close enough to steer a
        model away from spawning a new thread for an active subject.
        """
        sessions = self._read_sessions()
        topics = [t for t in sessions if t != CHAT_TOPIC]
        topics.sort(key=lambda t: sessions[t].get("thread_ts", ""), reverse=True)
        return topics[: max(limit, 0)]

    # -- reading -------------------------------------------------------

    def sessions(self) -> dict[str, dict[str, str]]:
        """The stored ``{topic: {thread_ts, channel}}`` map."""
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
        sessions[CHAT_TOPIC] = {"thread_ts": str(thread_ts), "channel": str(channel)}
        self._write_sessions(sessions)

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

    def _enqueue(
        self,
        topic: str,
        text: str,
        blocks: list[dict] | None = None,
        *,
        question: bool = False,
    ) -> None:
        item: dict = {"topic": topic, "text": text}
        if blocks is not None:
            item["blocks"] = blocks
        if question:
            # So the flush that finally delivers it still opens the question.
            item["question"] = True
        self._write_item(self.queue_dir, item)

    def _write_item(self, directory: Path, item: dict) -> None:
        """Write one queued/held post under a name that cannot collide.

        The clock alone is not unique: it can be frozen or coarse, and two posts
        in the same second (or a re-queue during a flush) would otherwise
        overwrite each other and silently drop a message. The counter suffix
        keeps the timestamp ordering that ``flush_queue`` sorts on.
        """
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.clock().strftime("%Y%m%dT%H%M%S%f")
        counter = 0
        while True:
            path = directory / f"{stamp}-{counter}.json"
            if not path.exists():
                break
            counter += 1
        _atomic_write(path, json.dumps(item))

    @staticmethod
    def _read_item(path: Path) -> dict | None:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return item if isinstance(item, dict) else None

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
        # Deferred: ``approvals`` imports ``CHAT_TOPIC`` from this module, so a
        # top-level import back here would be circular.
        from ai_brain.approvals import APPROVE_ACTION, REJECT_ACTION

        self.app.event("message")(self.on_message)
        self.app.event("reaction_added")(self.on_reaction)
        self.app.action(APPROVE_ACTION)(self.on_button)
        self.app.action(REJECT_ACTION)(self.on_button)
        for action_id in ANSWER_ACTIONS:
            self.app.action(action_id)(self.on_button)
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
            # Filip spoke in this thread: it is not stale, an edit of the
            # bot's last message would now rewrite what he is replying to,
            # and any question the topic had open has its answer.
            self.out.note_owner_message(topic)
        log.info("[slack] note from filip (%d chars, topic=%s)", len(body), topic)
        if topic is not None:
            body = f"topic: {topic}\n{body}"
        message_ts = str(event.get("ts") or "")
        channel = str(event.get("channel") or "")
        if message_ts and channel:
            # A bare top-level DM outside any tracked thread has no topic yet
            # (topic is None) -- the reply that answers it binds ``chat``
            # itself, so mark the spinner against that same topic name.
            await self.out.mark_pending(topic or CHAT_TOPIC, channel, message_ts)
        self.brain.drop_note(NOTE_SENDER, body)
        self.wake("brain")

    async def on_reaction(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        if event.get("user") != self.user_id:
            return
        item = event.get("item") or {}
        message_ts = str(item.get("ts", ""))
        proposal = await self.approvals.on_reaction(message_ts, str(event.get("reaction", "")))
        # A reaction resolves a proposal and drops a note about it. Without a
        # wake the brain reads that note whenever its heartbeat next comes
        # round -- up to half an hour after Filip approved something.
        if proposal is not None:
            await self._resolve_message(str(item.get("channel", "")), message_ts, proposal)
            self.wake("brain")

    async def on_button(self, body: dict, ack: Ack = None) -> None:
        """An Approve/Reject button click, or a Yes/No answer to a question.

        Bolt hands ``action`` handlers the whole interaction payload, not an
        ``event`` -- the clicked button (and its ``value``, the proposal id)
        is under ``actions[0]``, the user under ``user``, and the message it
        lives in under ``message`` (``ts`` there is the same id a reaction on
        this message would report) with the channel as its own top-level
        ``channel``. ``approvals.on_button`` is keyed on that message ts
        exactly like ``on_reaction`` is; the button's ``value`` is not used
        for lookup, only Slack's own record of who clicked and where.
        """
        await _ack(ack)
        if body.get("user", {}).get("id") != self.user_id:
            return
        actions = body.get("actions") or []
        if not actions:
            return
        action_id = str(actions[0].get("action_id", ""))
        message_ts = str(body.get("message", {}).get("ts", ""))
        if action_id in ANSWER_ACTIONS:
            await self._on_answer(body, actions[0], ANSWER_ACTIONS[action_id])
            return
        proposal = await self.approvals.on_button(message_ts, action_id)
        if proposal is not None:
            channel = str(body.get("channel", {}).get("id", ""))
            await self._resolve_message(channel, message_ts, proposal)
            self.wake("brain")

    async def _on_answer(self, body: dict, action: dict, answer: str) -> None:
        """A Yes/No click: the same note a typed reply in the thread would be.

        The note is shaped exactly like ``on_message``'s for a threaded
        message -- ``topic: <name>`` then the body -- so ``owed_replies`` owes
        the topic a reply and the brain reads it like anything else Filip
        says. The topic comes from the button's ``value`` (``question_blocks``
        sets it), falling back to the thread the message sits in.

        Note and wake first, the message edit after: the edit is decoration,
        and a Slack hiccup on it must not cost the brain the answer.
        """
        message = body.get("message") or {}
        message_ts = str(message.get("ts") or "")
        channel = str((body.get("channel") or {}).get("id") or "")
        topic = str(action.get("value") or "")
        if topic not in self.out.sessions():
            topic = (
                self.out.topic_for_thread(str(message.get("thread_ts") or message_ts))
                or CHAT_TOPIC
            )
        if not self.out.mark_answered(topic, message_ts, answer):
            log.info("[slack] duplicate %s answer on %s ignored", answer, topic)
            return
        log.info("[slack] button answer from filip (%s, topic=%s)", answer, topic)
        if message_ts and channel:
            await self.out.mark_pending(topic, channel, message_ts)
        self.brain.drop_note(NOTE_SENDER, f"topic: {topic}\nAnswer: {answer}")
        self.wake("brain")
        if message_ts and channel:
            original = str(message.get("text") or "").strip()
            verdict = "✅ yes" if answer == "yes" else "❌ no"
            text = f"{original}\n\n*Answer:* {verdict}" if original else f"*Answer:* {verdict}"
            await self.out.mark_resolved(channel, message_ts, text)

    async def on_session_stopped(self, event: dict, ack: Ack = None) -> None:
        await _ack(ack)
        thread_ts = event.get("thread_ts") or event.get("session", {}).get("thread_ts") or ""
        topic = self.out.topic_for_thread(str(thread_ts))
        if topic is None:
            log.info("[slack] session stopped for unknown thread %r", thread_ts)
            return
        self.brain.drop_note(NOTE_SENDER, f"Filip stopped {topic}")
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

    async def _resolve_message(self, channel: str, ts: str, proposal: Any) -> None:
        """Swap an approval message's buttons for its verdict, if reachable.

        ``channel``/``ts`` come from whichever Bolt payload triggered the
        resolution (a button click or a ✅/❌ reaction) -- both name the
        message the same way a post's own ``ts`` would. Missing either means
        there is nothing to edit (a malformed or partial event), not an error
        worth logging on top of ``mark_resolved``'s own best-effort logging.
        """
        if not channel or not ts:
            return
        from ai_brain.approvals import resolved_text

        await self.out.mark_resolved(channel, ts, resolved_text(proposal))


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
    out = SlackOut(
        app.client,
        settings.slack_user_id,
        brain,
        now_utc,
        stale_thread_h=settings.slack_stale_thread_h,
        quiet_hours=settings.slack_quiet_hours,
    )
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
