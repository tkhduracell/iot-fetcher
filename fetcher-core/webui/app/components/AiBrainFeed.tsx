'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  type AgentSummary,
  type RoundTrace,
  fetchAgents,
  fetchTrace,
  formatClock,
} from '../lib/aiBrain';
import {
  BackLink,
  EmptyState,
  MONO,
  Pill,
  WALL,
  WallShell,
} from './AiBrainWallTheme';
import { CallView, Pre, ResultView } from './AiBrainAgentTrace';

/** `/ai-brain/feed` — every loop's rounds, merged into one live stream.
 *
 *  Each loop's own screen (`/ai-brain/agent/[name]`) reads one cycle at a
 *  time; this answers a different question, "what is the house thinking about
 *  right now, across all of it, in the order it happened" — a single
 *  chronological read rather than six tabs to click between.
 *
 *  Two sources, used for two different jobs. On mount, one snapshot fetch
 *  (`/api/agents` + each loop's `/trace`) gets the backlog — rounds already on
 *  disk before this tab opened, which a stream alone can never replay. After
 *  that, `GET /api/feed` (Server-Sent Events, `ai_brain/api.py`) delivers
 *  every round from here on the instant it happens: `round_started` the
 *  moment a loop sends its request to the model, `round_complete` once the
 *  reply (and any tool results) lands, `cycle_ended` when nothing more is
 *  coming for a loop this cycle. A loop can only ever have one round in
 *  flight, so `round_complete`/`cycle_ended` resolve the *current* pending
 *  entry for that loop's name — the two events do not share a timestamp to
 *  join on (each is its own `self.clock()` read in `loop.py`), and do not
 *  need one. */

const MAX_ROUNDS = 60;
const FEED_URL = '/api/ai-brain/api/feed';

type FeedRound = {
  loop: string;
  round: RoundTrace;
  inProgress: boolean;
  /** Set on a `round_started` entry with no round body yet — the "tänker…"
   *  placeholder a `round_complete` for the same loop replaces in place. */
  pending?: boolean;
};

type FeedEvent =
  | { type: 'round_started'; loop: string; at: number; dropped?: number }
  | {
      type: 'round_complete';
      loop: string;
      round: RoundTrace;
      dropped?: number;
    }
  | { type: 'cycle_ended'; loop: string; status: string; dropped?: number };

/** Speaker colour is stable per loop name, not per render, so the same loop
 *  reads as the same colour as new rounds arrive and old ones fall off. */
function loopColor(loop: string): string {
  const palette = [WALL.amber, WALL.sage, WALL.clay, WALL.rose, '#8FA8C8', '#C79FD0'];
  let hash = 0;
  for (let i = 0; i < loop.length; i++) hash = (hash * 31 + loop.charCodeAt(i)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

/** One entry in the merged stream: which loop said it, when, and the same
 *  round body `/ai-brain/agent/[name]` renders — text, calls, results. A
 *  `pending` entry (a `round_started` with no round body yet) renders as a
 *  live "tänker…" line instead — real content, not a skeleton, since it is
 *  telling the truth about a loop that is genuinely waiting on the model
 *  right now. */
const FeedEntry: React.FC<{ entry: FeedRound }> = ({ entry }) => {
  const { loop, round, inProgress, pending } = entry;
  const calls = round.tool_calls ?? [];
  const results = round.tool_results ?? [];
  const color = loopColor(loop);

  return (
    <div className="flex flex-col gap-2 min-w-0 rounded px-3 py-2" style={{ background: WALL.raised }}>
      <div className="flex items-center gap-2 flex-wrap text-[12px]" style={{ fontFamily: MONO }}>
        <span style={{ color, fontWeight: 600 }}>{loop}</span>
        <span style={{ color: WALL.inkFaint }} className="tabular-nums">
          {formatClock(round.at)}
        </span>
        {pending ? (
          <Pill tone="busy">tänker…</Pill>
        ) : (
          inProgress && <Pill tone="busy">pågår</Pill>
        )}
      </div>
      {pending ? (
        <Pre className="opacity-60">väntar på modellen…</Pre>
      ) : (
        <>
          {round.text && <Pre className="opacity-90">{round.text}</Pre>}
          {calls.map((call, j) => (
            <CallView key={`${call.name}-${j}`} call={call} />
          ))}
          {results.map((res, j) => (
            <ResultView key={`${res.name}-${j}`} result={res} />
          ))}
          {!round.text && calls.length === 0 && results.length === 0 && (
            <Pre className="opacity-60">…</Pre>
          )}
        </>
      )}
    </div>
  );
};

const AiBrainFeed: React.FC = () => {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [rounds, setRounds] = useState<FeedRound[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);

  // Every loop's own last-seen round count. `loadTraces` uses it so a
  // reconnect's snapshot only appends rounds the stream missed rather than
  // re-flattening the whole backlog; the SSE handlers below only write to it
  // as a best-effort hint, since `loadTraces` always overwrites it with the
  // server's true count on its next run.
  const seenRef = useRef<Map<string, number>>(new Map());

  const loadAgents = useCallback(async (signal: AbortSignal) => {
    const { agents: list } = await fetchAgents(signal);
    setAgents(list);
    setError(null);
    return list;
  }, []);

  const loadTraces = useCallback(async (list: AgentSummary[], signal: AbortSignal) => {
    const results = await Promise.allSettled(
      list.map((a) => fetchTrace(a.name, signal)),
    );

    const fresh: FeedRound[] = [];
    for (const res of results) {
      if (res.status !== 'fulfilled') continue;
      const { agent, trace } = res.value;
      if (!trace) continue;
      const allRounds = trace.rounds ?? [];
      const already = seenRef.current.get(agent) ?? 0;
      // A compaction or restart can shrink a loop's round count under what
      // was already seen; treat that as "everything is new" rather than a
      // negative slice, which `Array.prototype.slice` would silently accept.
      const newRounds = allRounds.length >= already ? allRounds.slice(already) : allRounds;
      seenRef.current.set(agent, allRounds.length);
      const lastIndex = allRounds.length - 1;
      newRounds.forEach((round, j) => {
        const isLast = already + j === lastIndex;
        fresh.push({ loop: agent, round, inProgress: trace.in_progress && isLast });
      });
    }

    if (fresh.length === 0) return;
    setRounds((prev) =>
      [...prev, ...fresh]
        .sort((a, b) => b.round.at - a.round.at)
        .slice(0, MAX_ROUNDS),
    );
  }, []);

  // A loop can only ever have one round in flight, so the pending "tänker…"
  // entry a round_started creates is found again by loop name alone — there
  // is at most one to find, and round_started/round_complete do not share a
  // timestamp to join on (each is its own clock read in loop.py).
  const resolvePending = useCallback(
    (loop: string, resolve: (entry: FeedRound) => FeedRound | null) => {
      setRounds((prev) => {
        const i = prev.findIndex((e) => e.loop === loop && e.pending);
        if (i === -1) return prev;
        const resolved = resolve(prev[i]);
        if (resolved === null) return prev.filter((_, j) => j !== i);
        const next = [...prev];
        next[i] = resolved;
        return next;
      });
    },
    [],
  );

  const onFeedEvent = useCallback(
    (raw: FeedEvent) => {
      switch (raw.type) {
        case 'round_started':
          setRounds((prev) =>
            [
              {
                loop: raw.loop,
                round: { at: raw.at, text: '', tool_calls: [], tool_results: [] },
                inProgress: true,
                pending: true,
              },
              ...prev,
            ].slice(0, MAX_ROUNDS),
          );
          break;
        case 'round_complete':
          seenRef.current.set(raw.loop, (seenRef.current.get(raw.loop) ?? 0) + 1);
          resolvePending(raw.loop, () => ({ loop: raw.loop, round: raw.round, inProgress: false }));
          break;
        case 'cycle_ended':
          // Nothing more is coming for this loop this cycle. A pending entry
          // with no round_complete behind it means the call itself failed
          // (timeout, no budget, raised) -- resolved here rather than left
          // showing "tänker…" forever.
          resolvePending(raw.loop, () => null);
          break;
      }
    },
    [resolvePending],
  );

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    let source: EventSource | null = null;

    // The stream only ever delivers events from the moment it connects
    // forward; the backlog already on disk when the tab opens has to come
    // from a snapshot first. Re-run after any reconnect too, since a plain
    // `EventSource` has no "resume from here" and a network blip can drop
    // events in between -- the same `seenRef`-based slice that seeds the
    // first paint also patches a resumed one without duplicating rounds.
    const snapshot = async () => {
      try {
        const list = await loadAgents(controller.signal);
        if (cancelled) return;
        await loadTraces(list, controller.signal);
      } catch (e) {
        if (cancelled || controller.signal.aborted) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    };

    (async () => {
      await snapshot();
      if (cancelled) return;

      source = new EventSource(FEED_URL);
      source.onmessage = (ev) => {
        try {
          onFeedEvent(JSON.parse(ev.data) as FeedEvent);
          setError(null);
        } catch {
          // A malformed line is one event lost, not a reason to drop the
          // connection the rest of the stream is still healthy on.
        }
      };
      source.onerror = () => {
        if (cancelled) return;
        // `EventSource` retries on its own; the gap this reconnect might
        // have left is what the snapshot below is for.
        setError(new Error('live-flödet bröts, återansluter…'));
        snapshot().catch(() => {});
      };
      source.onopen = () => setError(null);
    })();

    return () => {
      cancelled = true;
      controller.abort();
      source?.close();
    };
  }, [loadAgents, loadTraces, onFeedEvent]);

  return (
    <WallShell
      density="read"
      title="Flöde"
      current="/ai-brain/feed"
      back={<BackLink />}
      headerRight={
        error ? (
          <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.rose }}>
            {error.message}
          </span>
        ) : (
          agents && (
            <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
              {agents.length} {agents.length === 1 ? 'loop' : 'loopar'}
            </span>
          )
        )
      }
    >
      {initialLoading && rounds.length === 0 ? (
        <EmptyState why="hämtar /api/agents och varje loops /trace">Läser flödet…</EmptyState>
      ) : rounds.length === 0 ? (
        <EmptyState why="ingen loop har producerat en runda sedan hjärnan startade">
          Inget att visa än.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-2 min-w-0 overflow-y-auto">
          {rounds.map((entry, i) => (
            <FeedEntry key={`${entry.loop}-${entry.round.at}-${i}`} entry={entry} />
          ))}
        </div>
      )}
    </WallShell>
  );
};

export default AiBrainFeed;
