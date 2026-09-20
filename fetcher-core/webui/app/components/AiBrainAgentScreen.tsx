'use client';

import React, { useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  type AgentDetailPlus,
  type AgentSummary,
  type Status,
  fetchAgentPlus,
  fetchAgents,
  fetchStatus,
  formatAgo,
  formatIn,
  shortModel,
  statusTone,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import {
  BackLink,
  EmptyState,
  MONO,
  MachineLine,
  Pill,
  SANS,
  WALL,
  WallShell,
  toneColor,
  useServerClock,
} from './AiBrainWallTheme';
import AiBrainAgentTrace from './AiBrainAgentTrace';
import { AgentCharterDocs, AgentFacts, AgentInbox, AgentJournal } from './AiBrainAgentPanels';

/** `/ai-brain/agent/[name]` — one loop, read up close.
 *
 *  This is the screen the old blue-card `AiBrainDetail` + `AiBrainAgentCard`
 *  pair became. The card's state — status, last cycle, next wake, cycle counts,
 *  fact and note counts, the compaction flag — is the header here; the card's
 *  five tabs are the five tabs below it.
 *
 *  `density="read"`: scrolling is expected and detail is the point. The wall's
 *  one-screen promise does not apply, but its language does. */

const POLL_MS = 10_000;
const DETAIL_POLL_MS = 30_000;

type Tab = 'trace' | 'journal' | 'charter' | 'facts' | 'inbox';

const TABS: { id: Tab; label: string }[] = [
  { id: 'trace', label: 'Spår' },
  { id: 'journal', label: 'Journal' },
  { id: 'charter', label: 'Mål & identitet' },
  { id: 'facts', label: 'Fakta' },
  { id: 'inbox', label: 'Inkorg' },
];

const TabButton: React.FC<{
  tab: { id: Tab; label: string };
  active: boolean;
  badge?: number;
  onSelect: (id: Tab) => void;
}> = ({ tab, active, badge, onSelect }) => (
  <button
    type="button"
    aria-pressed={active}
    onClick={() => onSelect(tab.id)}
    className="px-1 pb-2 text-[13px] uppercase tracking-[0.18em] cursor-pointer bg-transparent border-0 whitespace-nowrap"
    style={{
      fontFamily: SANS,
      color: active ? WALL.ink : WALL.inkFaint,
      borderBottom: `2px solid ${active ? WALL.amber : 'transparent'}`,
    }}
  >
    {tab.label}
    {badge !== undefined && badge > 0 && (
      <span className="ml-2 align-middle" style={{ fontFamily: MONO, color: WALL.clay }}>
        {badge}
      </span>
    )}
  </button>
);

/** The card's state line, now the screen's header: what the loop is doing,
 *  when it last ran, when it wakes next, and what it is carrying. */
const AgentHeader: React.FC<{ summary: AgentSummary; now: number }> = ({ summary, now }) => {
  const tone = summary.in_progress ? 'busy' : statusTone(summary.last_cycle?.status);
  const state = summary.in_progress ? 'kör' : (summary.last_cycle?.status ?? 'okänd');
  const counts = Object.entries(summary.cycle_counts ?? {});

  // One "·"-joined run so a loop that has never run leaves no dangling
  // separator behind.
  const line = [
    summary.last_cycle?.model ? shortModel(summary.last_cycle.model) : 'ingen modell',
    summary.last_cycle ? `${summary.last_cycle.rounds} rundor` : null,
    summary.last_cycle_at ? `senast ${formatAgo(summary.last_cycle_at, now)}` : 'aldrig kört',
    summary.in_progress ? null : `nästa ${formatIn(summary.next_wake_at, now)}`,
    `hjärtslag ${summary.heartbeat_s} s`,
  ].filter(Boolean);

  return (
    <div className="flex flex-col gap-2 min-w-0">
      <div className="flex items-baseline gap-3 flex-wrap min-w-0">
        <h2
          className="text-[30px] m-0 leading-none break-words"
          style={{ fontFamily: MONO, color: WALL.ink }}
        >
          {summary.name}
        </h2>
        <span
          className="text-[12px] uppercase tracking-[0.18em]"
          style={{ fontFamily: SANS, color: WALL.inkFaint }}
        >
          {summary.priority === 'brain' ? 'hjärna' : 'expert'}
        </span>
        <Pill tone={tone}>{state}</Pill>
        {summary.needs_compaction && (
          <Pill tone="warn" title="Minnet har vuxit förbi tröskeln — loopen vill kompaktera">
            vill kompaktera
          </Pill>
        )}
      </div>

      <p className="text-[12px] m-0 tabular-nums" style={{ fontFamily: MONO, color: WALL.inkDim }}>
        {line.join(' · ')}
      </p>

      <div className="flex items-center gap-2 flex-wrap">
        {counts.map(([key, count]) => (
          <Pill key={key} tone={statusTone(key)} title={`${count} cykler med status ${key}`}>
            {key} {count}
          </Pill>
        ))}
        <Pill>{summary.facts} fakta</Pill>
        {summary.unread_notes > 0 && (
          <Pill color={WALL.clay}>{summary.unread_notes} olästa</Pill>
        )}
      </div>
    </div>
  );
};

/** A name that is not a loop. Real state, not a crash — and the way out is the
 *  list of names that do exist. */
const UnknownAgent: React.FC<{ name: string; agents: AgentSummary[] }> = ({ name, agents }) => (
  <div className="flex flex-col gap-4 min-w-0">
    <EmptyState why={`"${name}" finns inte bland de loopar ai-brain kör`}>
      Ingen sådan loop.
    </EmptyState>
    {agents.length > 0 && (
      <ul className="flex flex-wrap gap-3 list-none m-0 p-0">
        {agents.map((a) => (
          <li key={a.name}>
            <Link
              href={`/ai-brain/agent/${encodeURIComponent(a.name)}`}
              className="text-[14px] no-underline hover:underline"
              style={{ fontFamily: MONO, color: WALL.amber }}
            >
              {a.name}
            </Link>
          </li>
        ))}
      </ul>
    )}
  </div>
);

const AiBrainAgentScreen: React.FC<{ name: string }> = ({ name }) => {
  const [tab, setTab] = useState<Tab>('trace');

  const statusFetcher = useCallback((signal: AbortSignal) => fetchStatus(signal), []);
  const agentsFetcher = useCallback((signal: AbortSignal) => fetchAgents(signal), []);
  const detailFetcher = useCallback(
    (signal: AbortSignal) => fetchAgentPlus(name, signal),
    [name],
  );

  const status = useAiBrain<Status>(statusFetcher, [], POLL_MS);
  const agents = useAiBrain<{ agents: AgentSummary[] }>(agentsFetcher, [], DETAIL_POLL_MS);
  const detail = useAiBrain<AgentDetailPlus>(detailFetcher, [name], DETAIL_POLL_MS);

  // ai-brain's clock, not the tablet's — the countdown has to agree with the
  // brain's own idea of when the loop wakes.
  const now = useServerClock(status.data?.now);

  const agentList = useMemo(() => agents.data?.agents ?? [], [agents.data]);
  const summary: AgentSummary | null =
    detail.data ?? agentList.find((a) => a.name === name) ?? null;

  // A name is only "unknown" once the loop list has actually been read. Before
  // that it is simply not known yet, and a 404-looking screen would be a lie.
  const unknown =
    Boolean(agents.data) && !agentList.some((a) => a.name === name) && !detail.data;

  const others = agentList.filter((a) => a.name !== name);

  const machineBits: string[] = [`loop ${name}`];
  if (detail.data) {
    machineBits.push(`fakta ${detail.data.facts}`);
    machineBits.push(`journal ${detail.data.journal_days?.length ?? 0} d`);
    machineBits.push(`lappar ${detail.data.notes?.length ?? 0}`);
  }
  if (summary?.next_wake_at) machineBits.push(`nästa vakning ${formatIn(summary.next_wake_at, now)}`);
  if (status.data?.paused) machineBits.push('hjärnan pausad');
  if (detail.error && detail.data) machineBits.push('senast kända värden');

  const body = () => {
    if (unknown) return <UnknownAgent name={name} agents={agentList} />;
    if (!detail.data) {
      if (detail.initialLoading) {
        return <EmptyState why={`hämtar /api/agents/${name}`}>Läser loopen…</EmptyState>;
      }
      return (
        <EmptyState why={detail.error?.message ?? 'ai-brain svarade inte'}>
          Kunde inte hämta loopen.
        </EmptyState>
      );
    }

    const d = detail.data;
    switch (tab) {
      case 'trace':
        return <AiBrainAgentTrace agent={name} seed={d.trace ?? null} now={now} />;
      case 'journal':
        return <AgentJournal agent={name} knownDays={d.journal_days ?? []} />;
      case 'charter':
        return <AgentCharterDocs detail={d} />;
      case 'facts':
        return (
          <AgentFacts agent={name} factNames={d.fact_names ?? []} detail={d} now={now} />
        );
      case 'inbox':
        return <AgentInbox notes={d.notes ?? []} unread={d.unread_notes ?? 0} />;
    }
  };

  return (
    <WallShell
      density="read"
      title="Loop"
      back={<BackLink />}
      headerRight={
        others.length > 0 ? (
          <nav
            aria-label="Andra loopar"
            className="hidden sm:flex items-baseline gap-3 min-w-0 shrink-0"
          >
            {others.map((a) => (
              <Link
                key={a.name}
                href={`/ai-brain/agent/${encodeURIComponent(a.name)}`}
                className="text-[12px] no-underline hover:underline whitespace-nowrap"
                style={{
                  fontFamily: MONO,
                  color: a.in_progress ? toneColor('busy') : WALL.inkFaint,
                }}
              >
                {a.name}
              </Link>
            ))}
          </nav>
        ) : undefined
      }
      footer={<MachineLine bits={machineBits} />}
    >
      {summary && !unknown && <AgentHeader summary={summary} now={now} />}

      {!unknown && (
        <div
          role="group"
          aria-label="Vy"
          className="flex items-end gap-5 overflow-x-auto"
          style={{ borderBottom: `1px solid ${WALL.rule}` }}
        >
          {TABS.map((t) => (
            <TabButton
              key={t.id}
              tab={t}
              active={t.id === tab}
              badge={
                t.id === 'inbox'
                  ? (detail.data?.unread_notes ?? 0)
                  : t.id === 'facts'
                    ? (detail.data?.facts ?? 0)
                    : undefined
              }
              onSelect={setTab}
            />
          ))}
        </div>
      )}

      <div className="min-w-0">{body()}</div>
    </WallShell>
  );
};

export default AiBrainAgentScreen;
