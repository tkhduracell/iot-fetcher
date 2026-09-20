'use client';

import React from 'react';
import {
  type LedgerKey,
  compactTokens,
  formatIn,
  quotaCounts,
  quotaTone,
  shortModel,
} from '../lib/aiBrain';
import { MONO, Pill, SANS, WALL, toneColor } from './AiBrainWallTheme';

/** The day's ledger, one row per key.
 *
 *  The bars fill with what has been **spent**, not with what is left. The old
 *  supervisor drew the remaining fraction, so a key sitting at 17 of 200 —
 *  blocked, going nowhere — showed a long confident bar and read as healthy.
 *  Here that key is a short bar against a full track, and its state is written
 *  next to it in words. A bar that is nearly full is nearly used up, every
 *  time, which is the only reading a bar should have. */

/** Spent fraction in 0..1. A key with no limit served has no denominator, so
 *  it gets an empty track and lets the counts speak — never a full bar, which
 *  would claim an exhaustion nobody reported. */
function spentFraction(used: number, limit: number): number {
  if (!Number.isFinite(limit) || limit <= 0) return 0;
  const u = Number.isFinite(used) ? used : 0;
  return Math.max(0, Math.min(1, u / limit));
}

const Bar: React.FC<{ fraction: number; color: string; known: boolean }> = ({
  fraction,
  color,
  known,
}) => (
  <div
    className="h-[6px] w-full rounded-full overflow-hidden"
    style={{ background: 'rgba(244, 237, 226, 0.10)' }}
    role="presentation"
  >
    <div
      className="h-full rounded-full transition-all"
      style={{
        width: `${fraction * 100}%`,
        background: color,
        opacity: known ? 0.9 : 0.35,
      }}
    />
  </div>
);

export const LedgerKeyRow: React.FC<{ entry: LedgerKey; now: number }> = ({ entry, now }) => {
  const blocked = entry.blocked_until !== null && entry.blocked_until > now;
  const disabled = entry.disabled_until !== null && entry.disabled_until > now;
  const unusable = blocked || disabled;

  const reqLimit = entry.requests_limit;
  const tokLimit = entry.tokens_limit;
  const reqFraction = spentFraction(entry.requests_day, reqLimit);
  const tokFraction = spentFraction(entry.tokens_day, tokLimit);

  // A key that cannot be called is the loudest thing on the row whatever its
  // numbers say — quotaTone only knows about the budget.
  const reqTone = unusable ? 'error' : quotaTone(entry.requests_remaining);
  const tokTone = unusable ? 'error' : quotaTone(entry.tokens_remaining);

  return (
    <li className="flex flex-col gap-1 min-w-0 list-none">
      <div className="flex items-baseline gap-2 flex-wrap min-w-0">
        <span
          className="text-[14px] break-words"
          style={{ fontFamily: MONO, color: unusable ? WALL.rose : WALL.ink }}
          title={entry.key}
        >
          {shortModel(entry.key)}
        </span>
        {blocked && (
          <Pill tone="error" title={`Blockerad av ai-brain till ${formatIn(entry.blocked_until, now)}`}>
            blockerad · släpps {formatIn(entry.blocked_until, now)}
          </Pill>
        )}
        {disabled && (
          <Pill tone="error" title={`Avstängd till ${formatIn(entry.disabled_until, now)}`}>
            avstängd · släpps {formatIn(entry.disabled_until, now)}
          </Pill>
        )}
        {entry.consecutive_429 > 0 && (
          <Pill tone="warn" title="Antal 429-svar i rad från leverantören">
            {entry.consecutive_429}× 429
          </Pill>
        )}
        {entry.recent_requests > 0 && (
          <span
            className="text-[12px] ml-auto tabular-nums"
            style={{ fontFamily: MONO, color: WALL.inkFaint }}
          >
            {entry.recent_requests} nyligen
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 min-w-0">
        <div className="flex items-center gap-3 min-w-0">
          <Bar fraction={reqFraction} color={toneColor(reqTone)} known={reqLimit > 0} />
          <span
            className="text-[12px] tabular-nums whitespace-nowrap shrink-0"
            style={{ fontFamily: MONO, color: WALL.inkDim }}
            title="Anrop förbrukade av dygnets budget"
          >
            {quotaCounts(entry.requests_day, reqLimit)} anrop
          </span>
        </div>
        <div className="flex items-center gap-3 min-w-0">
          <Bar fraction={tokFraction} color={toneColor(tokTone)} known={tokLimit > 0} />
          <span
            className="text-[12px] tabular-nums whitespace-nowrap shrink-0"
            style={{ fontFamily: MONO, color: WALL.inkDim }}
            title="Tokens förbrukade av dygnets budget"
          >
            {compactTokens(entry.tokens_day)}/{tokLimit > 0 ? compactTokens(tokLimit) : '–'} tok
          </span>
        </div>
      </div>
    </li>
  );
};

/** The legend that keeps the bars honest: say which direction they fill. */
export const LedgerLegend: React.FC = () => (
  <span className="text-[12px]" style={{ fontFamily: SANS, color: WALL.inkFaint }}>
    staplarna visar förbrukat av dygnets budget
  </span>
);

export default LedgerKeyRow;
