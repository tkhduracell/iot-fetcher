'use client';

import React from 'react';
import { type LedgerKey, type Status, quotaLabel, quotaTone } from '../lib/aiBrain';

/** Card shell, matching EnergyPriceBar's Wrapper. */
export const Card: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <div
    className={`px-3 py-2.5 rounded-md bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 ring-blue-200 dark:ring-blue-800 flex flex-col gap-2 ${className}`}
  >
    {children}
  </div>
);

export const Pill: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${className}`}>{children}</span>
);

const QUOTA_COLOURS = {
  ok: 'bg-green-500 dark:bg-green-600',
  warn: 'bg-yellow-500 dark:bg-yellow-600',
  error: 'bg-red-500 dark:bg-red-600',
} as const;

/** Remaining-quota bar: green above 40%, yellow above 15%, red below.
 *
 *  `remaining` is the API's fraction (0..1) and is the bar's width directly --
 *  it is not derived from used/limit, because the ledger owns that arithmetic
 *  (a synthetic token budget, a day that can roll mid-request). The counts are
 *  the label beside it, so the bar says both how much and how much of what. */
const QuotaBar: React.FC<{ label: string; remaining: number; used: number; limit: number }> = ({
  label,
  remaining,
  used,
  limit,
}) => {
  const frac = Number.isFinite(remaining) ? Math.max(0, Math.min(1, remaining)) : 0;

  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex justify-between text-[11px] text-gray-700 dark:text-gray-300">
        <span>{label}</span>
        <span className="tabular-nums">{quotaLabel(used, limit)}</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-gray-300 dark:bg-gray-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${QUOTA_COLOURS[quotaTone(remaining)]}`}
          style={{ width: `${frac * 100}%` }}
        />
      </div>
    </div>
  );
};

const KeyRow: React.FC<{ entry: LedgerKey; now: number }> = ({ entry, now }) => {
  const blocked = entry.blocked_until !== null && entry.blocked_until > now;
  const disabled = entry.disabled_until !== null && entry.disabled_until > now;

  return (
    <div className="flex flex-col gap-1 rounded bg-blue-50 dark:bg-blue-950 px-2 py-1.5">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-xs font-semibold text-gray-900 dark:text-gray-100">{entry.key}</span>
        {blocked && (
          <Pill className="bg-red-600 text-white">Blockerad</Pill>
        )}
        {disabled && (
          <Pill className="bg-gray-600 text-white">Avstängd</Pill>
        )}
        {entry.consecutive_429 > 0 && (
          <Pill className="bg-yellow-600 text-white">{entry.consecutive_429}× 429</Pill>
        )}
        <span className="ml-auto text-[11px] text-gray-600 dark:text-gray-400 tabular-nums">
          {entry.recent_requests} senaste
        </span>
      </div>
      <QuotaBar
        label="Förfrågningar"
        remaining={entry.requests_remaining}
        used={entry.requests_day}
        limit={entry.requests_limit}
      />
      <QuotaBar
        label="Tokens"
        remaining={entry.tokens_remaining}
        used={entry.tokens_day}
        limit={entry.tokens_limit}
      />
    </div>
  );
};

const Detail: React.FC<{ term: string; children: React.ReactNode }> = ({ term, children }) => (
  <div className="flex flex-col">
    <dt className="text-[11px] uppercase tracking-wide text-gray-600 dark:text-gray-400">{term}</dt>
    <dd className="text-xs text-gray-900 dark:text-gray-100 break-words">{children}</dd>
  </div>
);

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}

const AiBrainSupervisor: React.FC<{ status: Status }> = ({ status }) => {
  // The API payload is cast, not validated, so default every nested object:
  // a partial response should degrade to dashes, not blank the whole page.
  const settings = status.settings ?? ({} as Status['settings']);
  const ledger = status.ledger ?? { day: '–', keys: [] };
  const slack = status.slack ?? { configured: false, connected: false, queued: 0, sessions: 0 };
  const proposals = status.proposals ?? { pending: 0, total: 0 };

  return (
    <Card>
      <div className="flex items-center gap-2 flex-wrap">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Övervakare</h2>
        <Pill
          className={
            status.paused
              ? 'bg-yellow-500 dark:bg-yellow-600 text-white'
              : 'bg-green-600 dark:bg-green-700 text-white'
          }
        >
          {status.paused ? 'PAUSAD' : 'Aktiv'}
        </Pill>
        <span className="ml-auto text-xs text-gray-700 dark:text-gray-300 tabular-nums">
          Uppe {formatUptime(status.uptime_s)} · dygn {ledger.day}
        </span>
      </div>

      {status.paused && (
        <p className="text-[11px] text-gray-700 dark:text-gray-300 break-all">
          Pausfil: {status.pause_file}
        </p>
      )}

      <div className="flex flex-col gap-1.5">
        {(ledger.keys ?? []).length === 0 ? (
          <p className="text-xs text-gray-700 dark:text-gray-300">Inga nycklar i huvudboken.</p>
        ) : (
          (ledger.keys ?? []).map((k) => <KeyRow key={k.key} entry={k} now={status.now} />)
        )}
      </div>

      <div className="flex items-center gap-1.5 flex-wrap">
        <Pill
          className={
            !slack.configured
              ? 'bg-gray-500 dark:bg-gray-600 text-white'
              : slack.connected
                ? 'bg-green-600 dark:bg-green-700 text-white'
                : 'bg-red-600 dark:bg-red-700 text-white'
          }
        >
          Slack: {!slack.configured ? 'av' : slack.connected ? 'ansluten' : 'frånkopplad'}
        </Pill>
        {slack.configured && (
          <>
            <Pill className="bg-blue-200 dark:bg-blue-800 text-gray-900 dark:text-gray-100">
              {slack.sessions} sessioner
            </Pill>
            <Pill className="bg-blue-200 dark:bg-blue-800 text-gray-900 dark:text-gray-100">
              {slack.queued} i kö
            </Pill>
          </>
        )}
        <Pill
          className={
            proposals.pending > 0
              ? 'bg-yellow-500 dark:bg-yellow-600 text-white'
              : 'bg-blue-200 dark:bg-blue-800 text-gray-900 dark:text-gray-100'
          }
        >
          Förslag: {proposals.pending} väntar av {proposals.total}
        </Pill>
        {settings.dry_run && <Pill className="bg-purple-600 text-white">Torrkörning</Pill>}
      </div>

      <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-1.5">
        <Detail term="Modellkedja">{(settings.llm_chain ?? []).join(' → ') || '–'}</Detail>
        <Detail term="Experter">{(settings.experts ?? []).join(', ') || '–'}</Detail>
        <Detail term="Hjärtslag hjärna">{settings.brain_heartbeat_s} s</Detail>
        <Detail term="Hjärtslag expert">{settings.expert_heartbeat_s} s</Detail>
        <Detail term="Anropstimeout">{settings.call_timeout_s} s</Detail>
        <Detail term="Gränser">
          {settings.rpm}/min · {settings.tpm} tok/min · {settings.rpd}/dygn
        </Detail>
        <Detail term="Minnesrot">{settings.memory_root}</Detail>
      </dl>
    </Card>
  );
};

export default AiBrainSupervisor;
