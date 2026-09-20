'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  type AgentSummary,
  type Loop,
  type Proposal,
  type Status,
  fetchAgents,
  fetchLoops,
  fetchProposals,
  fetchStatus,
  fetchTrace,
  formatIn,
  quotaCounts,
  shortModel,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import AiBrainWallBeliefs from './AiBrainWallBeliefs';
import {
  ExecutedProposals,
  NaggingLoops,
  PendingProposals,
  isExecuted,
  isPending,
} from './AiBrainWallActions';
import { MONO, SANS, SERIF, Section, WALL } from './AiBrainWallTheme';

/** The always-on wall tablet: what the house's brain currently understands.
 *
 *  Read from across a room, so everything is big, Swedish and sentence-shaped,
 *  and all the machine telemetry is squeezed into one 12px line at the bottom.
 *
 *  Several of the sections here are fed by ai-brain endpoints that are still
 *  landing (`/api/loops`, `fact_stats`, `gaps`). The rule throughout: absent
 *  data hides its own section and nothing else. Nothing on this page may throw
 *  on a missing field — a wall tablet has nobody to press reload. */

const HOUSE = 'Irisgatan';

const POLL_MS = 10_000;
/** `/api/loops` aggregates the whole approval ledger; once a minute is plenty. */
const LOOPS_POLL_MS = 60_000;

const MAX_COLUMNS = 3;
const MAX_PENDING = 4;
const MAX_EXECUTED = 4;
const MAX_LOOPS = 5;

/** The hero sentence.
 *
 *  SEAM: what belongs here is a sentence the brain writes about its own current
 *  understanding — there is no endpoint that generates one today. The best
 *  honest source available is the brain's last cycle summary, which is the
 *  model's own words about what it just did; when that is empty we fall back to
 *  counting what is actually on disk. Neither is invented here: when a real
 *  "understanding" field lands, point `heroSentence` at it and delete the
 *  fallback. */
export function heroSentence(
  summary: string | undefined,
  agents: AgentSummary[],
  pending: number,
): string {
  const trimmed = (summary ?? '').trim();
  if (trimmed) return trimmed;

  const facts = agents.reduce((sum, a) => sum + (a.facts ?? 0), 0);
  if (agents.length === 0) return 'Hjärnan har inte sagt något ännu.';

  const parts = [
    `Hjärnan håller ${facts} fakta om huset över ${agents.length} ${
      agents.length === 1 ? 'loop' : 'loopar'
    }`,
  ];
  if (pending > 0) {
    // "förslag" is the same in singular and plural, so no branch is needed.
    parts.push(`${pending} förslag väntar på ditt ✅`);
  }
  return `${parts.join(', ')}.`;
}

const AiBrainWall: React.FC = () => {
  // Same server-clock discipline as /ai-brain: every timestamp comes from
  // ai-brain, so the ticker runs on ai-brain's clock offset by whatever the
  // last /api/status reported. A wall tablet's own clock drifts for weeks.
  const [now, setNow] = useState(() => Date.now() / 1000);
  const offsetRef = useRef(0);

  const statusFetcher = useCallback((signal: AbortSignal) => fetchStatus(signal), []);
  const agentsFetcher = useCallback((signal: AbortSignal) => fetchAgents(signal), []);
  const proposalsFetcher = useCallback((signal: AbortSignal) => fetchProposals(signal), []);
  const loopsFetcher = useCallback((signal: AbortSignal) => fetchLoops(signal), []);

  const status = useAiBrain<Status>(statusFetcher, [], POLL_MS);
  const agents = useAiBrain<{ agents: AgentSummary[] }>(agentsFetcher, [], POLL_MS);
  const proposals = useAiBrain<{ proposals: Proposal[]; pending: number; total: number }>(
    proposalsFetcher,
    [],
    POLL_MS,
  );
  // Resolves to null (not an error) while /api/loops is still being built.
  const loops = useAiBrain<{ loops: Loop[] } | null>(loopsFetcher, [], LOOPS_POLL_MS);

  const serverNow = status.data?.now;
  useEffect(() => {
    if (typeof serverNow !== 'number' || !Number.isFinite(serverNow)) return;
    offsetRef.current = serverNow - Date.now() / 1000;
    setNow(Date.now() / 1000 + offsetRef.current);
  }, [serverNow]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000 + offsetRef.current), 1000);
    return () => clearInterval(id);
  }, []);

  // Every payload is cast, not validated — default each list so a partial
  // response degrades to an empty section instead of a crashed render.
  const agentList = agents.data?.agents ?? [];
  const brain = agentList.find((a) => a.priority === 'brain') ?? agentList[0];

  const traceFetcher = useCallback(
    (signal: AbortSignal) => fetchTrace(brain?.name ?? '', signal),
    [brain?.name],
  );
  const trace = useAiBrain(traceFetcher, [brain?.name], POLL_MS, Boolean(brain?.name));

  const proposalList = proposals.data?.proposals ?? [];
  const pending = proposalList.filter(isPending).slice(0, MAX_PENDING);
  const executed = proposalList.filter(isExecuted).slice(0, MAX_EXECUTED);
  const loopList = (loops.data?.loops ?? []).slice(0, MAX_LOOPS);

  // Three columns, the agents with the most written down.
  const columns = useMemo(
    () =>
      [...agentList]
        .sort((a, b) => (b.facts ?? 0) - (a.facts ?? 0))
        .slice(0, MAX_COLUMNS)
        .map((a) => ({ name: a.name, facts: a.facts ?? 0 })),
    [agentList],
  );

  const hero = heroSentence(
    trace.data?.trace?.summary,
    agentList,
    proposals.data?.pending ?? pending.length,
  );

  const settings = status.data?.settings;
  const ledger = status.data?.ledger;
  const nextWake = agentList
    .map((a) => a.next_wake_at)
    .filter((t): t is number => typeof t === 'number' && Number.isFinite(t))
    .sort((a, b) => a - b)[0];

  const anyData = Boolean(status.data || agents.data || proposals.data);
  const offline = Boolean((status.error || agents.error) && !anyData);

  const clock = new Date(now * 1000).toLocaleTimeString('sv-SE', {
    hour: '2-digit',
    minute: '2-digit',
  });

  /** The single machine line. Everything here is deliberately unreadable from
   *  across the room: it is for the person standing at the tablet. */
  const machineBits: string[] = [];
  if (ledger) {
    const keys = (ledger.keys ?? [])
      .map((k) => `${shortModel(k.key)} ${quotaCounts(k.requests_day, k.requests_limit)}`)
      .join('  ');
    machineBits.push(`ledger ${ledger.day}${keys ? ` · ${keys}` : ''}`);
  }
  if (settings?.llm_chain?.length) {
    machineBits.push(`kedja ${settings.llm_chain.map(shortModel).join(' → ')}`);
  }
  // No fact cap is exposed by /api/status today; the honest equivalent is the
  // number actually held, plus whichever agent has asked to be compacted.
  const factTotal = agentList.reduce((sum, a) => sum + (a.facts ?? 0), 0);
  if (agentList.length) {
    const compacting = agentList.filter((a) => a.needs_compaction).map((a) => a.name);
    machineBits.push(
      `fakta ${factTotal}${compacting.length ? ` · kompaktering: ${compacting.join(', ')}` : ''}`,
    );
  }
  if (typeof nextWake === 'number') machineBits.push(`nästa vakning ${formatIn(nextWake, now)}`);
  if (status.data?.paused) machineBits.push('PAUSAD');
  if (status.error || agents.error) machineBits.push('senast kända värden');

  return (
    <main
      className="min-h-screen w-full px-10 py-8 flex flex-col gap-10"
      style={{ background: WALL.ground, color: WALL.ink, fontFamily: SANS }}
    >
      {/* Header: house, time, one muted line of machine state. */}
      <header className="flex items-baseline justify-between gap-6">
        <h1
          className="text-[22px] tracking-[0.28em] uppercase m-0"
          style={{ fontFamily: SANS, color: WALL.inkDim, fontWeight: 500 }}
        >
          {HOUSE}
        </h1>
        <div className="flex items-baseline gap-5">
          <span className="text-[13px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            {status.data?.paused
              ? 'pausad'
              : brain?.in_progress
                ? 'tänker'
                : offline
                  ? 'ingen kontakt'
                  : 'vaken'}
            {brain?.last_cycle?.model ? ` · ${shortModel(brain.last_cycle.model)}` : ''}
          </span>
          <span className="text-[30px] tabular-nums" style={{ fontFamily: MONO }}>
            {clock}
          </span>
        </div>
      </header>

      {/* Hero: one sentence, large serif. */}
      <p
        className="text-[42px] leading-[1.18] max-w-[24ch] sm:max-w-[34ch] m-0"
        style={{ fontFamily: SERIF, color: WALL.ink, fontWeight: 400 }}
      >
        {hero}
      </p>

      <Section title="Vet om huset" show={columns.length > 0} className="grow">
        <AiBrainWallBeliefs agents={columns} now={now} />
      </Section>

      <Section title="Väntar på ✅" accent={WALL.amber} show={pending.length > 0}>
        <PendingProposals proposals={pending} now={now} />
      </Section>

      <Section title="Verkställt" accent={WALL.sage} show={executed.length > 0}>
        <ExecutedProposals proposals={executed} now={now} />
      </Section>

      {/* Dark until /api/loops lands — and after that, dark on a quiet week. */}
      <Section title="Tjatar om" accent={WALL.rose} show={loopList.length > 0}>
        <NaggingLoops loops={loopList} />
      </Section>

      {offline && (
        <p className="text-[20px]" style={{ fontFamily: SERIF, color: WALL.inkDim }}>
          Ingen kontakt med hjärnan just nu.
        </p>
      )}

      <footer className="mt-auto pt-6" style={{ borderTop: `1px solid ${WALL.rule}` }}>
        <p
          className="text-[12px] leading-[1.6] m-0"
          style={{ fontFamily: MONO, color: WALL.inkFaint }}
        >
          {machineBits.join('  ·  ') || 'ai-brain: inga data'}
        </p>
      </footer>
    </main>
  );
};

export default AiBrainWall;
