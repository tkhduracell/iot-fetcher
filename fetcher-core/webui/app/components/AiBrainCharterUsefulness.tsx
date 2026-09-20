'use client';

import React from 'react';
import {
  type UsefulnessBuckets,
  type UsefulnessLoop,
  usefulShare,
} from '../lib/aiBrain';
import { MONO, Mono, SANS, SERIF, WALL } from './AiBrainWallTheme';

/** Nyttan — what came of the cycles.
 *
 *  `/api/usefulness` buckets every cycle a loop has run into four outcomes:
 *  nothing at all, a note to itself, something real, or a repeat of something
 *  it had already said. This is the number that decides whether the thing earns
 *  its keep, so it is rendered without flattery: `usefulShare` returns `null`
 *  for a loop that has never run and that renders as "har inte kört", never as
 *  0 %, and a repeat is coloured as the cost it is rather than as output. */

const BUCKETS: { key: keyof UsefulnessBuckets; label: string; color: string; why: string }[] = [
  { key: 'real', label: 'något verkligt', color: WALL.sage, why: 'cykeln ledde till en handling' },
  { key: 'note', label: 'en anteckning', color: WALL.amber, why: 'cykeln skrev ned något' },
  { key: 'repeat', label: 'upprepning', color: WALL.rose, why: 'cykeln sa om något den redan sagt' },
  { key: 'nothing', label: 'ingenting', color: WALL.inkFaint, why: 'cykeln gav inget utfall' },
];

/** "62 %" or "har inte kört" — never a 0 % that is really an absence. */
export function shareLabel(b: UsefulnessBuckets | null | undefined): string {
  const share = usefulShare(b);
  if (share === null) return 'har inte kört';
  return `${Math.round(share * 100)} %`;
}

/** The four outcomes as one bar. Widths are shares of `total`; a loop with no
 *  cycles renders no bar at all rather than an empty frame that reads as zero. */
export const UsefulnessBar: React.FC<{ buckets: UsefulnessBuckets }> = ({ buckets }) => {
  const total = buckets.total ?? 0;
  if (total <= 0) return null;
  return (
    <div
      className="flex h-[10px] w-full rounded-full overflow-hidden"
      style={{ background: 'rgba(244, 237, 226, 0.06)' }}
    >
      {BUCKETS.map((b) => {
        const n = buckets[b.key] ?? 0;
        if (n <= 0) return null;
        return (
          <span
            key={b.key}
            title={`${n} × ${b.label} — ${b.why}`}
            style={{
              width: `${(n / total) * 100}%`,
              background: b.color,
              opacity: b.key === 'nothing' ? 0.5 : 0.85,
            }}
          />
        );
      })}
    </div>
  );
};

/** The counts behind a bar, in the order the bar draws them. */
export const UsefulnessLegend: React.FC<{ buckets: UsefulnessBuckets }> = ({ buckets }) => (
  <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
    {BUCKETS.map((b) => (
      <span
        key={b.key}
        className="text-[13px]"
        style={{ fontFamily: SANS, color: WALL.inkDim }}
        title={b.why}
      >
        <Mono className="text-[14px]">{buckets[b.key] ?? 0}</Mono>{' '}
        <span style={{ color: b.color }}>{b.label}</span>
      </span>
    ))}
  </div>
);

/** Totals first, then one row per loop. */
export const AiBrainCharterUsefulness: React.FC<{
  totals: UsefulnessBuckets;
  loops: UsefulnessLoop[];
}> = ({ totals, loops }) => {
  const share = usefulShare(totals);
  const sorted = [...loops].sort((a, b) => (b.total ?? 0) - (a.total ?? 0));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <p
          className="text-[20px] leading-[1.35] m-0 max-w-[58ch]"
          style={{ fontFamily: SERIF, color: WALL.ink }}
        >
          {share === null ? (
            'Ingen cykel har hunnit ge ett utfall ännu.'
          ) : (
            <>
              <Mono className="text-[24px]" >
                <span style={{ color: share >= 0.5 ? WALL.sage : WALL.amber }}>
                  {Math.round(share * 100)} %
                </span>
              </Mono>{' '}
              av {totals.total} varv gav en anteckning eller något verkligt.
              {totals.repeat > 0 && ` ${totals.repeat} var upprepningar.`}
            </>
          )}
        </p>
        <UsefulnessBar buckets={totals} />
        <UsefulnessLegend buckets={totals} />
      </div>

      <ul className="flex flex-col gap-3 list-none m-0 p-0">
        {sorted.map((loop) => (
          <li key={loop.name} className="flex flex-col gap-1 min-w-0">
            <div className="flex items-baseline justify-between gap-4 min-w-0">
              <span
                className="text-[16px] truncate"
                style={{ fontFamily: SANS, color: WALL.ink }}
                title={loop.name}
              >
                {loop.name}
              </span>
              <span
                className="text-[13px] shrink-0 tabular-nums"
                style={{
                  fontFamily: MONO,
                  color: usefulShare(loop) === null ? WALL.inkFaint : WALL.inkDim,
                }}
              >
                {shareLabel(loop)}
                {loop.total > 0 && ` · ${loop.total} varv`}
              </span>
            </div>
            {loop.total > 0 ? (
              <UsefulnessBar buckets={loop} />
            ) : (
              <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
                inga cykler registrerade — ingen andel att visa
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
};

export default AiBrainCharterUsefulness;
