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

/** `/ai-brain/feed` — every loop's rounds, merged into one stream.
 *
 *  Each loop's own screen (`/ai-brain/agent/[name]`) reads one cycle at a
 *  time; this answers a different question, "what is the house thinking about
 *  right now, across all of it, in the order it happened" — a single
 *  chronological read rather than six tabs to click between.
 *
 *  Built entirely from what `/api/agents` and each loop's `/trace` already
 *  serve: one round is `{loop, round}`, the merge is a sort by `round.at`, and
 *  nothing here is new backend surface. */

const AGENTS_POLL_MS = 30_000;
const TRACE_POLL_MS = 10_000;
const MAX_ROUNDS = 60;

type FeedRound = { loop: string; round: RoundTrace; inProgress: boolean };

/** Speaker colour is stable per loop name, not per render, so the same loop
 *  reads as the same colour as new rounds arrive and old ones fall off. */
function loopColor(loop: string): string {
  const palette = [WALL.amber, WALL.sage, WALL.clay, WALL.rose, '#8FA8C8', '#C79FD0'];
  let hash = 0;
  for (let i = 0; i < loop.length; i++) hash = (hash * 31 + loop.charCodeAt(i)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

/** One entry in the merged stream: which loop said it, when, and the same
 *  round body `/ai-brain/agent/[name]` renders — text, calls, results. */
const FeedEntry: React.FC<{ entry: FeedRound }> = ({ entry }) => {
  const { loop, round, inProgress } = entry;
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
        {inProgress && <Pill tone="busy">pågår</Pill>}
      </div>
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
    </div>
  );
};

const AiBrainFeed: React.FC = () => {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [rounds, setRounds] = useState<FeedRound[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);

  // Every loop's own last-seen round count, so a poll only appends what is
  // actually new instead of re-flattening and re-sorting everything each
  // tick — the list is append-mostly, not replace-every-10s.
  const seenRef = useRef<Map<string, number>>(new Map());
  // The traces interval reads the latest agent list without re-subscribing:
  // a state dep here would restart both intervals every 30s, on every
  // agents poll, since a fresh array is a new reference each time.
  const agentsRef = useRef<AgentSummary[] | null>(null);

  const loadAgents = useCallback(async (signal: AbortSignal) => {
    const { agents: list } = await fetchAgents(signal);
    agentsRef.current = list;
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

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const tick = async () => {
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

    tick();
    const agentsId = setInterval(tick, AGENTS_POLL_MS);
    const tracesId = setInterval(() => {
      const list = agentsRef.current;
      if (list) loadTraces(list, controller.signal).catch(() => {});
    }, TRACE_POLL_MS);

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(agentsId);
      clearInterval(tracesId);
    };
  }, [loadAgents, loadTraces]);

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
