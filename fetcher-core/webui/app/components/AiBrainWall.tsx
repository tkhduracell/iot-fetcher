'use client';

import React, { useCallback, useMemo } from 'react';
import {
  type AgentSummary,
  type Loop,
  type Proposal,
  type Status,
  activeLedgerKeys,
  conditions,
  fetchAgents,
  fetchLoops,
  fetchProposals,
  fetchStatus,
  fetchTrace,
  formatIn,
  heroSentence,
  isExecuted,
  isPending,
  naggingLoops,
  shortModel,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import AiBrainWallBeliefs from './AiBrainWallBeliefs';
import { ExecutedProposals, NaggingLoops, PendingProposals } from './AiBrainWallActions';
import { LedgerChipRow } from './AiBrainSystemLedger';
import {
  CloseButton,
  ConditionStrip,
  EmptyState,
  MONO,
  MachineLine,
  MoreLink,
  SERIF,
  Section,
  SlackMirrorButtons,
  WALL,
  WallShell,
  useServerClock,
} from './AiBrainWallTheme';

/** The always-on wall tablet at `/ai-brain`: what the house's brain currently
 *  understands.
 *
 *  Read from across a room, so everything is big, Swedish and sentence-shaped,
 *  and all the machine telemetry is squeezed into one 12px line at the bottom.
 *
 *  The hard constraint is the tablet it runs on: 1024×768, glanced at, never
 *  scrolled. `WallShell` is one viewport tall with its overflow hidden at that
 *  width, and every list here is capped so the page cannot quietly grow a
 *  second screenful — depth lives behind the "fler →" links instead. When a
 *  number here disagrees with the count on a deeper screen, this one is the
 *  summary and that one is the truth. */

const POLL_MS = 10_000;
/** `/api/loops` aggregates the whole approval ledger; once a minute is plenty. */
const LOOPS_POLL_MS = 60_000;

/** One screenful. Raising any of these is a decision to push something below
 *  the fold at 1024×768 — measure before you do. */
const MAX_COLUMNS = 3;
const MAX_PENDING = 3;
const MAX_EXECUTED = 3;
const MAX_LOOPS = 4;

const AiBrainWall: React.FC = () => {
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
  // Resolves to null (not an error) when /api/loops is unreachable.
  const loops = useAiBrain<{ loops: Loop[] } | null>(loopsFetcher, [], LOOPS_POLL_MS);

  // ai-brain's clock, not the tablet's — see `useServerClock`.
  const now = useServerClock(status.data?.now);

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
  const pendingAll = proposalList.filter(isPending);
  const executedAll = proposalList.filter(isExecuted);
  const pending = pendingAll.slice(0, MAX_PENDING);
  // Grouped inside ExecutedProposals, so the cap is applied to the raw list a
  // little wide: three groups is the target, and repeats collapse into them.
  const executed = executedAll.slice(0, MAX_EXECUTED * 4);
  // laps >= 2 only: a topic proposed once is not something anybody is nagged
  // about, and the deployed wall listing "1× roborock" claimed otherwise.
  const nagging = useMemo(() => naggingLoops(loops.data?.loops, 2, MAX_LOOPS), [loops.data]);

  // Three columns, the agents with the most written down.
  const columns = useMemo(
    () =>
      [...agentList]
        .sort((a, b) => (b.facts ?? 0) - (a.facts ?? 0))
        .slice(0, MAX_COLUMNS)
        .map((a) => ({ name: a.name, facts: a.facts ?? 0 })),
    [agentList],
  );

  // A cycle status is never the hero — see `isStatusSummary`. "tom budget" is a
  // condition of the machine and renders as one, beside the machine line.
  const hero = heroSentence(
    trace.data?.trace?.summary,
    agentList,
    proposals.data?.pending ?? pendingAll.length,
  );

  const settings = status.data?.settings;
  const ledger = status.data?.ledger;
  const nextWake = agentList
    .map((a) => a.next_wake_at)
    .filter((t): t is number => typeof t === 'number' && Number.isFinite(t))
    .sort((a, b) => a - b)[0];

  const anyData = Boolean(status.data || agents.data || proposals.data);
  const offline = Boolean((status.error || agents.error) && !anyData);
  const stale = Boolean((status.error || agents.error) && anyData);

  /** The single machine line. Everything here is deliberately unreadable from
   *  across the room: it is for the person standing at the tablet. */
  const machineBits: string[] = [];
  // Only keys that have actually been called today, plus a count of the
  // silent ones. The deployed line printed two never-called keys at
  // `0/1000000` each and wrapped to three lines because of them. The active
  // keys themselves render as chips below, not as text in this line.
  const { active: activeLedger, silent: silentLedger } = activeLedgerKeys(ledger?.keys);
  if (ledger) {
    machineBits.push(
      `ledger ${ledger.day}${silentLedger > 0 ? ` · +${silentLedger} tysta` : ''}`,
    );
  }
  if (settings?.llm_chain?.length) {
    machineBits.push(`kedja ${settings.llm_chain.map(shortModel).join(' → ')}`);
  }
  // No fact cap is exposed by /api/status today; the honest equivalent is the
  // number actually held.
  const factTotal = agentList.reduce((sum, a) => sum + (a.facts ?? 0), 0);
  if (agentList.length) machineBits.push(`fakta ${factTotal}`);
  if (typeof nextWake === 'number') machineBits.push(`nästa vakning ${formatIn(nextWake, now)}`);
  if (stale) machineBits.push('senast kända värden');

  const state = status.data?.paused
    ? 'pausad'
    : brain?.in_progress
      ? 'tänker'
      : offline
        ? 'ingen kontakt'
        : 'vaken';

  return (
    <WallShell
      density="wall"
      current="/ai-brain"
      close={<CloseButton href="/" label="Till startsidan" />}
      headerRight={
        <div className="flex items-baseline gap-4 shrink-0">
          <span className="text-[13px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
            {state}
            {brain?.last_cycle?.model ? ` · ${shortModel(brain.last_cycle.model)}` : ''}
          </span>
        </div>
      }
      footer={
        <>
          <ConditionStrip conditions={conditions(status.data, agentList, offline)} />
          <LedgerChipRow keys={activeLedger} />
          <MachineLine bits={machineBits} />
        </>
      }
    >
      {/* Hero: one sentence about understanding, clamped to two lines so a
          long summary cannot push the columns off the screen. */}
      <p
        className="text-[28px] lg:text-[32px] leading-[1.2] max-w-[42ch] m-0 shrink-0"
        style={{
          fontFamily: SERIF,
          color: WALL.ink,
          fontWeight: 400,
          display: '-webkit-box',
          WebkitBoxOrient: 'vertical',
          WebkitLineClamp: 2,
          overflow: 'hidden',
        }}
      >
        {hero}
      </p>

      {/* Band one: what it knows. Takes whatever height is left and clips
          rather than scrolls — the cap is four facts a column. */}
      <Section
        title="Vet om huset"
        show={columns.length > 0}
        className="flex-1 min-h-0 flex flex-col"
        action={<MoreLink href="/ai-brain/knowledge">fler fakta</MoreLink>}
        empty={
          <EmptyState why={offline ? 'ingen kontakt med ai-brain' : 'inga loopar körs'}>
            Ingenting nedskrivet om huset ännu.
          </EmptyState>
        }
      >
        <AiBrainWallBeliefs agents={columns} now={now} />
      </Section>

      {/* Band two: what it wants, what it did, what it keeps repeating.
          Fixed height by construction — three items each, one line apiece. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4 shrink-0">
        <Section
          title="Väntar på ✅"
          accent={WALL.amber}
          show={pending.length > 0}
          action={
            <div className="flex items-center gap-3">
              {pendingAll.length > pending.length && (
                <MoreLink href="/ai-brain/loops">{`+${pendingAll.length - pending.length}`}</MoreLink>
              )}
              {/* One inert pair for the whole column — approval is a ✅ in
                  Slack, and saying so once is as honest as saying it thrice. */}
              <SlackMirrorButtons compact />
            </div>
          }
          empty={<EmptyState why="godkännanden sker med ✅ i Slack">Inget väntar.</EmptyState>}
        >
          <PendingProposals proposals={pending} now={now} buttons={false} />
        </Section>

        <Section
          title="Verkställt"
          accent={WALL.sage}
          show={executed.length > 0}
          action={<MoreLink href="/ai-brain/loops">historik</MoreLink>}
          empty={<EmptyState why="inget förslag har körts än">Ingenting utfört.</EmptyState>}
        >
          <ExecutedProposals proposals={executed} now={now} limit={MAX_EXECUTED} />
        </Section>

        {/* Hidden entirely when nothing has two laps — the section only means
            something when there is something to nag about. */}
        <Section
          title="Tjatar om"
          accent={WALL.rose}
          show={nagging.length > 0}
          action={<MoreLink href="/ai-brain/loops">slingor</MoreLink>}
        >
          <NaggingLoops loops={nagging} />
        </Section>
      </div>
    </WallShell>
  );
};

export default AiBrainWall;
