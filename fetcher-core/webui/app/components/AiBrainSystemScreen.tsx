'use client';

import React, { useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  type AgentSummary,
  type Proposal,
  type SlackSession,
  type Status,
  activeLedgerKeys,
  createdSeconds,
  executorName,
  fetchAgents,
  fetchProposals,
  fetchSlackSessions,
  fetchStatus,
  formatAgo,
  formatUptime,
  groupSlackSessions,
  isExecuted,
  isPending,
  isRejected,
  modelAvailability,
  proposalSentence,
  shortModel,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import {
  CloseButton,
  EmptyState,
  MONO,
  MachineLine,
  Pill,
  SANS,
  SERIF,
  Section,
  SlackMirrorButtons,
  WALL,
  WallShell,
  useServerClock,
} from './AiBrainWallTheme';
import { LedgerKeyRow, LedgerLegend } from './AiBrainSystemLedger';

/** `/ai-brain/system` — the machine behind the wall.
 *
 *  Everything the old blue-card `AiBrainSupervisor` held: paused state and the
 *  pause file, uptime, Slack, the day's ledger keys with their quota, the
 *  proposal counts, and the settings it hid behind a "Detaljer" disclosure —
 *  model chain, experts, heartbeats, call timeout, rpm/tpm/rpd and the memory
 *  root. Nothing is behind a disclosure here except the keys that did nothing
 *  and the raw payload of a proposal; this is the screen you open when you
 *  want to know, so it tells you.
 *
 *  Read density: scrolls, dense, every identifier in mono. */

const POLL_MS = 10_000;
const SLOW_POLL_MS = 30_000;

/** One `term / value` pair. `<dt>`/`<dd>` because that is what this is. */
const Field: React.FC<{
  term: string;
  children: React.ReactNode;
  className?: string;
  title?: string;
}> = ({ term, children, className = '', title }) => (
  <div className={`flex flex-col gap-[2px] min-w-0 ${className}`}>
    <dt
      className="text-[11px] uppercase tracking-[0.18em]"
      style={{ fontFamily: SANS, color: WALL.inkFaint }}
      title={title}
    >
      {term}
    </dt>
    <dd
      className="text-[14px] m-0 break-words"
      style={{ fontFamily: MONO, color: WALL.ink }}
    >
      {children}
    </dd>
  </div>
);

/** Groups shown before "visa alla" — a screenful, not the whole 42-session
 *  list the deployed system page dumped in one long flat list. */
const COLLAPSED_GROUPS = 6;

/** One normalised topic and every raw session folded into it.
 *
 *  Channel IDs and thread timestamps are real identifiers, not something a
 *  reader wants at a glance — they are the reason 42 sessions read as 42
 *  distinct things instead of a handful of topics asked about on different
 *  days. `showRaw` is the escape hatch for when they are actually needed
 *  (debugging a specific thread), off by default. */
const SlackSessionGroupRow: React.FC<{
  group: ReturnType<typeof groupSlackSessions>[number];
  showRaw: boolean;
}> = ({ group, showRaw }) => (
  <li className="flex flex-col gap-1 min-w-0">
    <div className="flex items-baseline gap-3 flex-wrap min-w-0">
      <Pill tone={group.open ? 'busy' : 'idle'}>{group.open ? 'öppen' : 'stängd'}</Pill>
      <span className="text-[14px] break-words" style={{ fontFamily: SANS, color: WALL.ink }}>
        {group.topic}
      </span>
      {group.sessions.length > 1 && (
        <span
          className="text-[12px] tabular-nums"
          style={{ fontFamily: MONO, color: WALL.inkFaint }}
        >
          {group.sessions.length}× tråd
        </span>
      )}
    </div>
    {showRaw && (
      <ul className="flex flex-col gap-[2px] list-none m-0 pl-4">
        {group.sessions.map((s) => (
          <li
            key={`${s.channel}-${s.thread_ts}`}
            className="text-[12px] tabular-nums"
            style={{ fontFamily: MONO, color: WALL.inkFaint }}
          >
            {s.status} · {s.channel} · {s.thread_ts}
          </li>
        ))}
      </ul>
    )}
  </li>
);

const proposalTone = (p: Proposal) =>
  isPending(p) ? 'warn' : isExecuted(p) ? 'ok' : isRejected(p) ? 'error' : 'idle';

/** One raw proposal: what it says, who would run it, why, and the payload
 *  itself behind a toggle — the JSON is the point on this screen. */
const ProposalRow: React.FC<{ proposal: Proposal; now: number }> = ({ proposal, now }) => (
  <li className="flex flex-col gap-1 min-w-0 list-none">
    <div className="flex items-baseline gap-2 flex-wrap min-w-0">
      <Pill tone={proposalTone(proposal)}>{proposal.status || 'okänd'}</Pill>
      <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.amber }}>
        {executorName(proposal.kind)}
      </span>
      <span className="text-[12px] tabular-nums" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
        {formatAgo(createdSeconds(proposal.created), now)}
        {proposal.topic ? ` · ${proposal.topic}` : ''}
      </span>
    </div>
    <p
      className="text-[18px] leading-[1.4] m-0 break-words"
      style={{ fontFamily: SERIF, color: WALL.ink }}
    >
      {proposalSentence(proposal)}
    </p>
    {proposal.reason && (
      <p className="text-[13px] m-0 break-words" style={{ fontFamily: SANS, color: WALL.inkDim }}>
        {proposal.reason}
      </p>
    )}
    <details>
      <summary
        className="cursor-pointer select-none text-[12px]"
        style={{ fontFamily: MONO, color: WALL.inkFaint }}
      >
        rå payload · {proposal.id}
      </summary>
      <pre
        className="text-[12px] leading-[1.5] whitespace-pre-wrap break-words m-0 mt-1 rounded px-3 py-2"
        style={{ fontFamily: MONO, color: WALL.ink, background: WALL.raised }}
      >
        {JSON.stringify(proposal.payload ?? {}, null, 2)}
      </pre>
      {proposal.result && (
        <p className="text-[12px] m-0 mt-1" style={{ fontFamily: MONO, color: WALL.inkDim }}>
          resultat: {proposal.result}
        </p>
      )}
    </details>
  </li>
);

const AiBrainSystemScreen: React.FC = () => {
  const [showAllSessions, setShowAllSessions] = useState(false);
  const [showRawSessions, setShowRawSessions] = useState(false);

  const statusFetcher = useCallback((signal: AbortSignal) => fetchStatus(signal), []);
  const agentsFetcher = useCallback((signal: AbortSignal) => fetchAgents(signal), []);
  const proposalsFetcher = useCallback((signal: AbortSignal) => fetchProposals(signal), []);
  const slackFetcher = useCallback((signal: AbortSignal) => fetchSlackSessions(signal), []);

  const status = useAiBrain<Status>(statusFetcher, [], POLL_MS);
  const agents = useAiBrain<{ agents: AgentSummary[] }>(agentsFetcher, [], SLOW_POLL_MS);
  const proposals = useAiBrain<{ proposals: Proposal[]; pending: number; total: number }>(
    proposalsFetcher,
    [],
    SLOW_POLL_MS,
  );
  const slackSessions = useAiBrain<{
    configured: boolean;
    queued: number;
    sessions: SlackSession[];
  }>(slackFetcher, [], SLOW_POLL_MS);

  const now = useServerClock(status.data?.now);

  // Every payload is cast, not validated — default each nested object so a
  // partial response degrades to dashes rather than blanking the screen.
  const s = status.data;
  const settings = s?.settings;
  const ledger = s?.ledger ?? { day: '–', keys: [] };
  const slack = s?.slack ?? { configured: false, connected: false, queued: 0, sessions: 0 };
  const counts = s?.proposals ?? { pending: 0, total: 0 };
  const { active, silent } = activeLedgerKeys(ledger.keys);
  const silentKeys = (ledger.keys ?? []).filter((k) => !active.includes(k));
  const agentList = agents.data?.agents ?? [];
  const proposalList = proposals.data?.proposals ?? [];
  const sessions = slackSessions.data?.sessions ?? [];
  const sessionGroups = useMemo(() => groupSlackSessions(sessions), [sessions]);
  const shownGroups = showAllSessions ? sessionGroups : sessionGroups.slice(0, COLLAPSED_GROUPS);

  const offline = !s && Boolean(status.error);

  const machineBits: string[] = [];
  if (s) {
    machineBits.push(`uppe ${formatUptime(s.uptime_s)}`);
    machineBits.push(`dygn ${ledger.day}`);
    machineBits.push(`nycklar ${active.length} aktiva${silent > 0 ? ` +${silent} tysta` : ''}`);
    machineBits.push(`förslag ${counts.pending}/${counts.total}`);
  }
  if (status.error && s) machineBits.push('senast kända värden');
  if (offline) machineBits.push('ingen kontakt med ai-brain');

  return (
    <WallShell
      density="read"
      current="/ai-brain/system"
      close={<CloseButton />}
      headerRight={
        <div className="flex items-center gap-2 shrink-0">
          {s?.paused ? <Pill tone="warn">pausad</Pill> : <Pill tone={offline ? 'error' : 'ok'}>
            {offline ? 'ingen kontakt' : 'aktiv'}
          </Pill>}
          {s && (
            <span className="text-[12px] tabular-nums" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
              uppe {formatUptime(s.uptime_s)}
            </span>
          )}
        </div>
      }
      footer={<MachineLine bits={machineBits} />}
    >
      {!s && (
        <EmptyState why={status.error?.message ?? 'hämtar /api/status'}>
          {status.initialLoading ? 'Läser systemläget…' : 'Kunde inte läsa systemläget.'}
        </EmptyState>
      )}

      {/* --------------------------------------------------------- ledger */}
      <Section
        title={`Huvudbok · ${ledger.day}`}
        accent={WALL.amber}
        show={active.length > 0}
        action={<LedgerLegend />}
        empty={
          <EmptyState
            why={
              (ledger.keys ?? []).length === 0
                ? 'ai-brain har inga nycklar konfigurerade'
                : `${silent} ${silent === 1 ? 'nyckel' : 'nycklar'} finns men ingen har anropats i dag`
            }
          >
            Ingen modell har kostat något i dag.
          </EmptyState>
        }
      >
        <ul className="flex flex-col gap-4 list-none m-0 p-0">
          {active.map((k) => (
            <LedgerKeyRow key={k.key} entry={k} now={now} />
          ))}
        </ul>
      </Section>

      {/* Keys with no traffic and no problem say nothing — but they must stay
          reachable, so they are a disclosure rather than a deletion. */}
      {silentKeys.length > 0 && (
        <details>
          <summary
            className="cursor-pointer select-none text-[12px]"
            style={{ fontFamily: MONO, color: WALL.inkFaint }}
          >
            {silentKeys.length} {silentKeys.length === 1 ? 'tyst nyckel' : 'tysta nycklar'} — inget
            anrop i dag
          </summary>
          <ul className="flex flex-col gap-2 list-none m-0 mt-2 p-0">
            {silentKeys.map((k) => (
              <li
                key={k.key}
                className="text-[12px] tabular-nums break-words"
                style={{ fontFamily: MONO, color: WALL.inkFaint }}
              >
                {shortModel(k.key)} · 0 anrop
                {k.key.startsWith('lan:') ? '' : ` av ${k.requests_limit || '–'}`}
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* ------------------------------------------------------ model chain */}
      <Section
        title="Modeller"
        accent={WALL.sage}
        show={(settings?.llm_chain?.length ?? 0) > 0}
        empty={
          <EmptyState why="llm_chain är tom i ai-brains inställningar">
            Ingen modellkedja konfigurerad.
          </EmptyState>
        }
      >
        {/* One row: the fallback order as a chain of badges, each toned by
            live availability -- not the model's position, which never
            changes and so never needs its own colour. Arrows read the same
            direction the chain is actually tried in. */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-2 min-w-0">
          {(settings?.llm_chain ?? []).map((model, i) => {
            // "Available" here means nothing this page knows of blocks the
            // entry -- a lan: model still needs the host to actually answer
            // once called, and a blocked cloud key can still be tried again
            // after its cooldown. See modelAvailability's own docstring.
            const availability = modelAvailability(model, ledger.keys, s?.lan_host, now);
            const tone = availability === 'available' ? 'ok' : 'error';
            const reason =
              availability === 'no_lan_host'
                ? 'ingen LAN-värd hittad'
                : availability === 'blocked'
                  ? 'otillgänglig'
                  : 'tillgänglig';
            return (
              <React.Fragment key={`${model}-${i}`}>
                {i > 0 && (
                  <span
                    aria-hidden
                    className="text-[13px] shrink-0"
                    style={{ fontFamily: MONO, color: WALL.inkFaint }}
                  >
                    →
                  </span>
                )}
                <Pill tone={tone} title={`${model} · ${reason}`}>
                  {shortModel(model)}
                </Pill>
              </React.Fragment>
            );
          })}
        </div>
      </Section>

      {/* ---------------------------------------------------------- loops */}
      <Section
        title="Loopar & hjärtslag"
        show={Boolean(settings)}
        empty={<EmptyState why="inställningarna kunde inte läsas">Okänd uppsättning.</EmptyState>}
      >
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-3 m-0">
          <Field term="Hjärta">{settings?.brain_heartbeat_s ?? '–'} s</Field>
          <Field term="Expert">{settings?.expert_heartbeat_s ?? '–'} s</Field>
          <Field term="Anropstimeout">{settings?.call_timeout_s ?? '–'} s</Field>
          <Field term="Torrkörning">{settings?.dry_run ? 'ja' : 'nej'}</Field>
          <Field term="Experter" className="col-span-2 sm:col-span-4">
            {(settings?.experts ?? []).length === 0 ? (
              '–'
            ) : (
              <span className="flex flex-wrap gap-x-3 gap-y-1">
                {(settings?.experts ?? []).map((name) => (
                  <Link
                    key={name}
                    href={`/ai-brain/agent/${encodeURIComponent(name)}`}
                    className="no-underline hover:underline"
                    style={{ color: WALL.amber }}
                  >
                    {name}
                  </Link>
                ))}
              </span>
            )}
          </Field>
        </dl>

        {agentList.length > 0 && (
          <ul className="flex flex-wrap gap-x-4 gap-y-1 list-none m-0 mt-3 p-0">
            {agentList.map((a) => (
              <li key={a.name}>
                <Link
                  href={`/ai-brain/agent/${encodeURIComponent(a.name)}`}
                  className="text-[13px] no-underline hover:underline"
                  style={{ fontFamily: MONO, color: WALL.inkDim }}
                >
                  {a.emoji ? `${a.emoji} ` : ''}
                  {a.name} · {a.in_progress ? 'kör' : (a.last_cycle?.status ?? 'okänd')}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* --------------------------------------------------------- limits */}
      <Section
        title="Gränser & rot"
        show={Boolean(settings)}
        empty={<EmptyState why="inställningarna kunde inte läsas">Okända gränser.</EmptyState>}
      >
        <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-3 m-0">
          <Field term="Anrop / min" title="rpm">
            {settings?.rpm ?? '–'}
          </Field>
          <Field term="Tokens / min" title="tpm">
            {settings?.tpm ?? '–'}
          </Field>
          <Field term="Anrop / dygn" title="rpd">
            {settings?.rpd ?? '–'}
          </Field>
          <Field term="Minnesrot" className="col-span-2 sm:col-span-3">
            {settings?.memory_root || '–'}
          </Field>
          <Field term="Pausfil" className="col-span-2 sm:col-span-3">
            {s?.pause_file || '–'}
            {s?.paused ? ' · finns' : ' · saknas (hjärnan går)'}
          </Field>
          {/* Whether a LAN model is reachable is already a pill in "Modeller"
              above; this is only the address, for a model that was found. */}
          {s?.lan_host?.enabled && (s.lan_host.hosts ?? []).some((h) => h.host) && (
            <Field term="LAN-värdar" className="col-span-2 sm:col-span-3">
              {(s.lan_host.hosts ?? [])
                .filter((h) => h.host)
                .map((h) => `${shortModel(h.model)} → ${h.host}`)
                .join(' · ')}
            </Field>
          )}
        </dl>
      </Section>

      {/* ---------------------------------------------------------- slack */}
      <Section
        title="Slack"
        accent={WALL.clay}
        show={slack.configured}
        empty={
          <EmptyState why="ingen Slack-token i ai-brains miljö — godkännanden kan inte ske">
            Slack är inte konfigurerat.
          </EmptyState>
        }
      >
        <div className="flex flex-col gap-3 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Pill tone={slack.connected ? 'ok' : 'error'}>
              {slack.connected ? 'ansluten' : 'nere'}
            </Pill>
            <Pill>{slack.sessions} sessioner</Pill>
            {slack.queued > 0 && <Pill tone="warn">{slack.queued} i kö</Pill>}
          </div>

          {sessions.length === 0 ? (
            <EmptyState why="ingen öppen tråd — en session skapas när hjärnan frågar något">
              Inga aktiva trådar.
            </EmptyState>
          ) : (
            <div className="flex flex-col gap-2 min-w-0">
              <div className="flex items-baseline justify-between gap-3 flex-wrap">
                <span
                  className="text-[12px]"
                  style={{ fontFamily: MONO, color: WALL.inkFaint }}
                >
                  {sessionGroups.length} {sessionGroups.length === 1 ? 'ämne' : 'ämnen'} ·{' '}
                  {sessions.length} {sessions.length === 1 ? 'tråd' : 'trådar'} totalt
                </span>
                <button
                  type="button"
                  onClick={() => setShowRawSessions((v) => !v)}
                  aria-pressed={showRawSessions}
                  className="text-[12px] underline cursor-pointer bg-transparent p-0"
                  style={{ fontFamily: SANS, color: WALL.inkDim, border: 'none' }}
                >
                  {showRawSessions ? 'dölj kanal-id och tidsstämplar' : 'visa kanal-id och tidsstämplar'}
                </button>
              </div>

              <ul className="flex flex-col gap-2 list-none m-0 p-0">
                {shownGroups.map((group) => (
                  <SlackSessionGroupRow key={group.topic} group={group} showRaw={showRawSessions} />
                ))}
              </ul>

              {sessionGroups.length > COLLAPSED_GROUPS && (
                <button
                  type="button"
                  onClick={() => setShowAllSessions((v) => !v)}
                  className="self-start text-[12px] underline cursor-pointer bg-transparent p-0"
                  style={{ fontFamily: SANS, color: WALL.inkDim, border: 'none' }}
                >
                  {showAllSessions
                    ? 'visa färre ämnen'
                    : `visa alla ${sessionGroups.length} ämnen`}
                </button>
              )}
            </div>
          )}
        </div>
      </Section>

      {/* ------------------------------------------------------- proposals */}
      <Section
        title={`Råa förslag · ${counts.pending} av ${counts.total} väntar`}
        accent={WALL.rose}
        show={proposalList.length > 0}
        action={<SlackMirrorButtons compact />}
        empty={
          <EmptyState
            why={
              proposals.error
                ? proposals.error.message
                : 'ingen loop har föreslagit något — förslag skrivs av propose_action'
            }
          >
            Huvudboken över förslag är tom.
          </EmptyState>
        }
      >
        <ul className="flex flex-col gap-4 list-none m-0 p-0">
          {proposalList.map((p) => (
            <ProposalRow key={p.id} proposal={p} now={now} />
          ))}
        </ul>
      </Section>
    </WallShell>
  );
};

export default AiBrainSystemScreen;
