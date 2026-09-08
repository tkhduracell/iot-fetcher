'use client';

import React, { useEffect, useState } from 'react';
import {
  type LedgerKey,
  type Status,
  compactTokens,
  formatUptime,
  quotaCounts,
  quotaTone,
  shortModel,
} from '../lib/aiBrain';

/** Card shell, matching EnergyPriceBar's Wrapper. */
export const Card: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <div
    className={`p-2 sm:p-3 rounded-md bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 ring-blue-200 dark:ring-blue-800 flex flex-col gap-2 ${className}`}
  >
    {children}
  </div>
);

export const Pill: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <span
    className={`px-1.5 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap ${className}`}
  >
    {children}
  </span>
);

const NEUTRAL_PILL = 'bg-blue-200 dark:bg-blue-800 text-gray-900 dark:text-gray-100';

const QUOTA_COLOURS = {
  ok: 'bg-green-500 dark:bg-green-600',
  warn: 'bg-yellow-500 dark:bg-yellow-600',
  error: 'bg-red-500 dark:bg-red-600',
} as const;

const DETAILS_KEY = 'ai-brain.details';

/** One line per ledger key: name, a single remaining-requests bar, the counts
 *  behind it and the token spend as muted text. Tokens lose their own bar --
 *  on a phone two bars per key is what pushed the agents below the fold, and
 *  requests are what actually runs out first on the free tier. */
const KeyRow: React.FC<{ entry: LedgerKey; now: number }> = ({ entry, now }) => {
  const blocked = entry.blocked_until !== null && entry.blocked_until > now;
  const disabled = entry.disabled_until !== null && entry.disabled_until > now;
  const remaining = entry.requests_remaining;
  const frac = Number.isFinite(remaining) ? Math.max(0, Math.min(1, remaining)) : 0;

  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <span
        className="text-[11px] font-semibold text-gray-900 dark:text-gray-100 truncate shrink-0 max-w-[7.5rem]"
        title={entry.key}
      >
        {shortModel(entry.key)}
      </span>

      <div className="h-1.5 grow min-w-[2rem] rounded-full bg-gray-300 dark:bg-gray-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${QUOTA_COLOURS[quotaTone(remaining)]}`}
          style={{ width: `${frac * 100}%` }}
        />
      </div>

      <span className="text-[11px] tabular-nums text-gray-700 dark:text-gray-300 shrink-0">
        {quotaCounts(entry.requests_day, entry.requests_limit)}
      </span>
      <span className="text-[10px] tabular-nums text-gray-600 dark:text-gray-400 shrink-0">
        {compactTokens(entry.tokens_day)} tok
      </span>

      {blocked && <Pill className="bg-red-600 text-white shrink-0">Blockerad</Pill>}
      {disabled && <Pill className="bg-gray-600 text-white shrink-0">Avstängd</Pill>}
      {entry.consecutive_429 > 0 && (
        <Pill className="bg-yellow-600 text-white shrink-0">{entry.consecutive_429}× 429</Pill>
      )}
    </div>
  );
};

const Detail: React.FC<{ term: string; children: React.ReactNode; className?: string }> = ({
  term,
  children,
  className = '',
}) => (
  <div className={`flex flex-col ${className}`}>
    <dt className="text-[10px] uppercase tracking-wide text-gray-600 dark:text-gray-400">{term}</dt>
    <dd className="text-xs text-gray-900 dark:text-gray-100 break-words">{children}</dd>
  </div>
);

const AiBrainSupervisor: React.FC<{ status: Status }> = ({ status }) => {
  // The API payload is cast, not validated, so default every nested object:
  // a partial response should degrade to dashes, not blank the whole page.
  const settings = status.settings ?? ({} as Status['settings']);
  const ledger = status.ledger ?? { day: '–', keys: [] };
  const slack = status.slack ?? { configured: false, connected: false, queued: 0, sessions: 0 };
  const proposals = status.proposals ?? { pending: 0, total: 0 };
  const keys = ledger.keys ?? [];

  // Read once on mount rather than during render: the server pass has no
  // localStorage, and a differing first client render would hydrate-mismatch.
  const [detailsOpen, setDetailsOpen] = useState(false);
  useEffect(() => {
    try {
      setDetailsOpen(window.localStorage.getItem(DETAILS_KEY) === '1');
    } catch {
      // Private mode or blocked site data — the disclosure just starts closed.
    }
  }, []);

  const rememberDetails = (open: boolean) => {
    setDetailsOpen(open);
    try {
      window.localStorage.setItem(DETAILS_KEY, open ? '1' : '0');
    } catch {
      // Nothing to do — the state is still correct for this session.
    }
  };

  return (
    <Card>
      <div className="flex items-center gap-1 flex-wrap">
        <Pill
          className={
            status.paused
              ? 'bg-yellow-500 dark:bg-yellow-600 text-white'
              : 'bg-green-600 dark:bg-green-700 text-white'
          }
        >
          {status.paused ? 'PAUSAD' : 'Aktiv'}
        </Pill>
        <Pill className={NEUTRAL_PILL}>uppe {formatUptime(status.uptime_s)}</Pill>
        <Pill
          className={
            !slack.configured
              ? 'bg-gray-500 dark:bg-gray-600 text-white'
              : slack.connected
                ? 'bg-green-600 dark:bg-green-700 text-white'
                : 'bg-red-600 dark:bg-red-700 text-white'
          }
        >
          {!slack.configured ? 'Slack av' : slack.connected ? 'Slack ✓' : 'Slack ✗'}
        </Pill>
        {slack.configured && (
          <Pill className={NEUTRAL_PILL}>{slack.sessions} sessioner</Pill>
        )}
        {slack.configured && slack.queued > 0 && (
          <Pill className={NEUTRAL_PILL}>{slack.queued} i kö</Pill>
        )}
        {proposals.pending > 0 && (
          <Pill className="bg-yellow-500 dark:bg-yellow-600 text-white">
            {proposals.pending} förslag väntar
          </Pill>
        )}
        {settings.dry_run && <Pill className="bg-purple-600 text-white">Torrkörning</Pill>}
      </div>

      <div className="flex flex-col gap-1">
        {keys.length === 0 ? (
          <p className="text-xs text-gray-700 dark:text-gray-300">Inga nycklar i huvudboken.</p>
        ) : (
          keys.map((k) => <KeyRow key={k.key} entry={k} now={status.now} />)
        )}
      </div>

      <details
        open={detailsOpen}
        onToggle={(e) => rememberDetails((e.currentTarget as HTMLDetailsElement).open)}
        className="text-xs"
      >
        <summary className="cursor-pointer text-[11px] font-semibold text-blue-800 dark:text-blue-300 select-none">
          Detaljer
        </summary>

        <dl className="mt-1.5 grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-1.5">
          <Detail term="Modellkedja" className="col-span-2 sm:col-span-3">
            {(settings.llm_chain ?? []).length === 0 ? (
              '–'
            ) : (
              <ol className="flex flex-col gap-0.5 list-decimal list-inside">
                {(settings.llm_chain ?? []).map((m, i) => (
                  <li key={`${m}-${i}`} className="break-words">
                    {shortModel(m)}
                  </li>
                ))}
              </ol>
            )}
          </Detail>
          <Detail term="Experter">{(settings.experts ?? []).join(', ') || '–'}</Detail>
          <Detail term="Hjärtslag hjärna">{settings.brain_heartbeat_s} s</Detail>
          <Detail term="Hjärtslag expert">{settings.expert_heartbeat_s} s</Detail>
          <Detail term="Anropstimeout">{settings.call_timeout_s} s</Detail>
          <Detail term="Gränser">
            {settings.rpm}/min · {settings.tpm} tok/min · {settings.rpd}/dygn
          </Detail>
          <Detail term="Dygn">{ledger.day}</Detail>
          <Detail term="Förslag">
            {proposals.pending} väntar av {proposals.total}
          </Detail>
          <Detail term="Minnesrot" className="col-span-2 sm:col-span-3">
            <span className="break-all">{settings.memory_root}</span>
          </Detail>
          {status.paused && (
            <Detail term="Pausfil" className="col-span-2 sm:col-span-3">
              <span className="break-all">{status.pause_file}</span>
            </Detail>
          )}
        </dl>
      </details>
    </Card>
  );
};

export default AiBrainSupervisor;
