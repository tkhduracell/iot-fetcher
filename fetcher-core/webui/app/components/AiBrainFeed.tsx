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
  CloseButton,
  EmptyState,
  MONO,
  Pill,
  WALL,
  WallShell,
} from './AiBrainWallTheme';
import {
  Pre,
  ToolCallLine,
  useTypewriter,
} from './AiBrainAgentTrace';

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
  /** Stable React key for the row's whole life. Never derived from list
   *  position -- a prepended round would shift every index and remount every
   *  row, replaying each one's animations -- and kept when a pending entry
   *  resolves, whose `round.at` changes. */
  id: string;
  loop: string;
  round: RoundTrace;
  inProgress: boolean;
  /** Set on a `round_started` entry with no round body yet — the "tänker…"
   *  placeholder a `round_complete` for the same loop replaces in place. */
  pending?: boolean;
  /** Arrived after first paint (live event or reconnect snapshot) -- only
   *  these fade in; the initial snapshot renders still. */
  fresh?: boolean;
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
/** Round ids that have already had their entrance. Module-level so it
 *  outlives any one mounted row: React may remount a row (its box regroups,
 *  a key changes) and that must not replay the typewriter or fade. */
const animated = new Set<string>();

/** Whether this mount should animate -- decided once, on first render, and
 *  recorded so no later mount of the same id animates again. */
const useEntrance = (id: string, fresh: boolean | undefined): boolean => {
  const [animate] = useState(() => {
    const first = !!fresh && !animated.has(id);
    animated.add(id);
    return first;
  });
  return animate;
};

/** A round's thinking promoted to its main text, for rounds that thought but
 *  said nothing else -- otherwise the only words in the row would be the
 *  dimmed aside. */
/** A round's words, always in full -- no clamp, no "mer". */
const FullText: React.FC<{ children: string }> = ({ children }) => (
  <pre
    className="text-[12px] leading-[1.55] whitespace-pre-wrap break-words m-0 w-full"
    style={{ fontFamily: MONO, color: WALL.ink }}
  >
    {children}
  </pre>
);

const ThoughtAsText: React.FC<{ text: string; animate: boolean }> = ({ text, animate }) => {
  const shown = useTypewriter(text, animate);
  return <FullText>{shown.length < text.length ? `${shown}▌` : shown}</FullText>;
};

const FeedEntryImpl: React.FC<{
  entry: FeedRound;
  first: boolean;
  emoji: string;
  /** This loop's cycle is still running -- shown once, on its newest box. */
  running?: boolean;
}> = ({ entry, first, emoji, running = false }) => {
  const { loop, round } = entry;
  const animate = useEntrance(entry.id, entry.fresh);
  const calls = round.tool_calls ?? [];
  const results = round.tool_results ?? [];
  const color = loopColor(loop);

  return (
    <div
      className={`flex flex-col gap-1 min-w-0 ${first ? '' : 'pt-2 border-t'} ${
        animate ? 'animate-fade-in' : ''
      }`}
      style={first ? undefined : { borderColor: WALL.rule }}
    >
      <div className="flex items-center gap-2 flex-wrap text-[12px]" style={{ fontFamily: MONO }}>
        {first && (
          <span style={{ color, fontWeight: 600 }}>
            {emoji && <span className="mr-1">{emoji}</span>}
            {loop}
          </span>
        )}
        {running && (
          <Pill tone="busy" title="cykeln är aktiv – fler rundor kan komma">
            aktiv
          </Pill>
        )}
        {/* Who answered and when, pinned right so the left edge stays the
            agent's name and state. */}
        <span className="ml-auto flex items-center gap-2" style={{ color: WALL.inkFaint }}>
          {round.model && <span title={round.model}>{round.model}</span>}
          {/* The call's duration, with the clock time on hover; an older
              ai-brain with no duration falls back to the clock time. */}
          <span
            className="tabular-nums"
            title={
              round.duration_s
                ? `${formatClock(round.at)} · modellanropets tid, inklusive kö`
                : undefined
            }
          >
            {round.duration_s ? formatDuration(round.duration_s) : formatClock(round.at)}
          </span>
        </span>
      </div>
      {entry.pending ? (
        <Pre className="opacity-60">väntar på modellen…</Pre>
      ) : (
        <>
          {/* Thinking is hidden when the round has its own text -- the text is
              the round's point, the thinking mostly restates it at length.
              A round that only thought shows that thinking as its text. */}
          {round.text ? (
            <FullText>{round.text}</FullText>
          ) : (
            round.thinking && <ThoughtAsText text={round.thinking} animate={animate} />
          )}
          {calls.map((call, j) => (
            // Same lockstep pairing as AiBrainAgentTrace's RoundView -- one
            // result per call, same order, same round.
            <ToolCallLine key={`${call.name}-${j}`} call={call} result={results[j]} />
          ))}
          {!round.text && !round.thinking && calls.length === 0 && results.length === 0 && (
            <Pre className="opacity-60">…</Pre>
          )}
        </>
      )}
    </div>
  );
};

// Entries are replaced, never mutated, so reference equality is enough to
// skip re-rendering every untouched row on each update.
const FeedEntry = React.memo(FeedEntryImpl);

/** Consecutive rounds from the same loop, newest first -- one box per run, so
 *  a loop working through a cycle reads as one flow and a box only breaks
 *  when a different loop takes a turn. */
type FeedGroup = { key: string; loop: string; entries: FeedRound[] };

const groupRuns = (rounds: FeedRound[]): FeedGroup[] => {
  const groups: FeedGroup[] = [];
  for (const entry of rounds) {
    const last = groups[groups.length - 1];
    if (last && last.loop === entry.loop) last.entries.push(entry);
    else groups.push({ key: '', loop: entry.loop, entries: [entry] });
  }
  // Keyed by the run's *oldest* entry: new rounds arrive at the top, so the
  // bottom of a run is what stays put while it grows.
  for (const g of groups) g.key = g.entries[g.entries.length - 1].id;
  return groups;
};

/** "4 s", "2 min 5 s", "1 h 3 min" -- read at a glance, not parsed. */
const formatDuration = (seconds: number) => {
  const s = Math.round(seconds);
  if (s < 60) return `${Math.max(1, s)} s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h) return m ? `${h} h ${m} min` : `${h} h`;
  return rest ? `${m} min ${rest} s` : `${m} min`;
};

const roundId = (loop: string, at: number) => `${loop}-${at}`;

const AiBrainFeed: React.FC = () => {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [rounds, setRounds] = useState<FeedRound[]>([]);
  // Loops whose cycle has not ended: set by a snapshot's in_progress and by
  // round_started, cleared by cycle_ended. A property of the cycle, not of
  // any one (already finished) round.
  const [running, setRunning] = useState<Set<string>>(new Set());
  const setLoopRunning = useCallback((loop: string, on: boolean) => {
    setRunning((prev) => {
      if (prev.has(loop) === on) return prev;
      const next = new Set(prev);
      if (on) next.add(loop);
      else next.delete(loop);
      return next;
    });
  }, []);
  const [error, setError] = useState<Error | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);

  // Every loop's own last-seen round count. `loadTraces` uses it so a
  // reconnect's snapshot only appends rounds the stream missed rather than
  // re-flattening the whole backlog; the SSE handlers below only write to it
  // as a best-effort hint, since `loadTraces` always overwrites it with the
  // server's true count on its next run.
  const seenRef = useRef<Map<string, number>>(new Map());
  // Flips after the first snapshot lands; later rounds are the "new" ones.
  const paintedRef = useRef(false);

  const loadAgents = useCallback(async (signal: AbortSignal) => {
    const { agents: list } = await fetchAgents(signal);
    setAgents(list);
    setError(null);
    return list;
  }, []);

  const loadTraces = useCallback(
    async (list: AgentSummary[], signal: AbortSignal) => {
    const results = await Promise.allSettled(
      list.map((a) => fetchTrace(a.name, signal)),
    );

    const fresh: FeedRound[] = [];
    for (const res of results) {
      if (res.status !== 'fulfilled') continue;
      const { agent, trace } = res.value;
      if (!trace) continue;
      setLoopRunning(agent, trace.in_progress);
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
        fresh.push({
          id: roundId(agent, round.at),
          loop: agent,
          round,
          inProgress: trace.in_progress && isLast,
          fresh: paintedRef.current,
        });
      });
    }

    paintedRef.current = true;
    if (fresh.length === 0) return;
    setRounds((prev) => {
      const have = new Set(prev.map((e) => e.id));
      return [...prev, ...fresh.filter((e) => !have.has(e.id))]
        .sort((a, b) => b.round.at - a.round.at)
        .slice(0, MAX_ROUNDS);
    });
    },
    [setLoopRunning],
  );

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
          setLoopRunning(raw.loop, true);
          setRounds((prev) =>
            [
              {
                id: `${roundId(raw.loop, raw.at)}-live`,
                loop: raw.loop,
                round: { at: raw.at, text: '', tool_calls: [], tool_results: [] },
                inProgress: true,
                pending: true,
                fresh: true,
              },
              ...prev,
            ].slice(0, MAX_ROUNDS),
          );
          break;
        case 'round_complete':
          seenRef.current.set(raw.loop, (seenRef.current.get(raw.loop) ?? 0) + 1);
          resolvePending(raw.loop, (prev) => ({
            id: prev.id,
            loop: raw.loop,
            round: raw.round,
            inProgress: false,
            fresh: true,
          }));
          break;
        case 'cycle_ended':
          // Nothing more is coming for this loop this cycle. A pending entry
          // with no round_complete behind it means the call itself failed
          // (timeout, no budget, raised) -- resolved here rather than left
          // showing "tänker…" forever.
          resolvePending(raw.loop, () => null);
          setLoopRunning(raw.loop, false);
          break;
      }
    },
    [resolvePending, setLoopRunning],
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

  const thinking = rounds.filter((e) => e.pending);
  const done = rounds.filter((e) => !e.pending);
  const emojiFor = (loop: string) => agents?.find((a) => a.name === loop)?.emoji ?? '';

  return (
    <WallShell
      density="read"
      current="/ai-brain/feed"
      close={<CloseButton />}
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
          {thinking.length > 0 && (
            // Loops waiting on the model are a status, not content: one inline
            // row of badges rather than a box each saying the same thing.
            <div className="flex flex-wrap gap-2">
              {thinking.map((entry) => (
                <Pill key={entry.id} tone="busy" className="animate-fade-in">
                  <span className="mr-1">{emojiFor(entry.loop)}</span>
                  <span style={{ color: loopColor(entry.loop), fontWeight: 600 }}>
                    {entry.loop}
                  </span>
                  <span className="ml-1">tänker…</span>
                </Pill>
              ))}
            </div>
          )}
          {groupRuns(done).map((group, gi, groups) => (
            <div
              key={group.key}
              className="flex flex-col gap-2 min-w-0 rounded px-3 py-2"
              style={{ background: WALL.raised }}
            >
              {group.entries.map((entry, i) => (
                <FeedEntry
                  key={entry.id}
                  entry={entry}
                  first={i === 0}
                  emoji={emojiFor(entry.loop)}
                  running={
                    i === 0 &&
                    running.has(group.loop) &&
                    !groups.slice(0, gi).some((g) => g.loop === group.loop)
                  }
                />
              ))}
            </div>
          ))}
        </div>
      )}
    </WallShell>
  );
};

export default AiBrainFeed;
