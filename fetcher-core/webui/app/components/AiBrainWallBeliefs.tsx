'use client';

import React, { useCallback, useMemo } from 'react';
import {
  type AgentDetailPlus,
  type FactStat,
  cutAtBoundary,
  fetchAgentPlus,
  fetchFactBodies,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import { Age, BeliefRow, EmptyState, MONO, SANS, WALL } from './AiBrainWallTheme';

/** The beliefs columns: one per agent, each fact shown as title, slug, body
 *  excerpt and age.
 *
 *  Four per column, because the wall is glanced at and must fit 1024×768 with
 *  nothing below the fold — the rest lives on /ai-brain/knowledge behind the
 *  section's "fler →". Luckor (gaps) live there too: the wall says what the
 *  house's brain knows, the knowledge screen says what it does not.
 *
 *  ``fact_stats`` (already in hand from ``fetchAgentPlus``) carries every
 *  fact's title — that alone used to need a guess at a sentence from the raw
 *  body, or the fact's own slug as a last resort. The body is fetched only
 *  for the excerpt underneath the title, which nothing else already has. */

/** Facts per column. A layout decision and a budget at once: each one costs an
 *  HTTP round trip for its body. */
const FACTS_PER_COLUMN = 4;

const DETAIL_POLL_MS = 30_000;
/** Fact bodies change on the order of cycles, not seconds. */
const BODY_POLL_MS = 60_000;

function statsByName(stats: FactStat[] | undefined): Map<string, FactStat> {
  const map = new Map<string, FactStat>();
  for (const s of stats ?? []) {
    if (s && typeof s.name === 'string') map.set(s.name, s);
  }
  return map;
}

const BeliefColumn: React.FC<{ agent: string; facts: number; now: number }> = ({
  agent,
  facts,
  now,
}) => {
  const detailFetcher = useCallback(
    (signal: AbortSignal) => fetchAgentPlus(agent, signal),
    [agent],
  );
  const detail = useAiBrain<AgentDetailPlus>(detailFetcher, [agent], DETAIL_POLL_MS);

  const stats = statsByName(detail.data?.fact_stats);
  const names = detail.data?.fact_names ?? [];

  const shown = useMemo(() => {
    const list = [...names];
    // Newest write first when fact_stats is populated; a freshly deployed brain
    // has writes=1 on everything, in which case the API's own order stands.
    if (stats.size > 0) {
      list.sort((a, b) => (stats.get(b)?.written_at ?? 0) - (stats.get(a)?.written_at ?? 0));
    }
    return list.slice(0, FACTS_PER_COLUMN);
    // `names` is a fresh array each poll; its contents are the real dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names.join('\u0000'), stats.size]);

  const key = shown.join('\u0000');
  const bodiesFetcher = useCallback(
    (signal: AbortSignal) => fetchFactBodies(agent, shown, signal),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agent, key],
  );
  const bodies = useAiBrain<Record<string, string>>(
    bodiesFetcher,
    [agent, key],
    BODY_POLL_MS,
    shown.length > 0,
  );

  const loading = detail.initialLoading;

  return (
    <div className="flex flex-col gap-2 min-w-0 overflow-hidden">
      <div
        className="flex items-baseline gap-2 pb-1 shrink-0"
        style={{ borderBottom: `1px solid ${WALL.rule}` }}
      >
        <span className="text-[14px] tracking-[0.06em] truncate" style={{ fontFamily: MONO, color: WALL.ink }}>
          {detail.data?.emoji ? `${detail.data.emoji} ` : ''}
          {agent}
        </span>
        <span
          className="text-[12px] tabular-nums shrink-0"
          style={{ fontFamily: MONO, color: WALL.inkFaint }}
        >
          {facts} fakta
        </span>
      </div>

      <ul className="flex flex-col gap-3 list-none m-0 p-0 min-h-0 overflow-hidden">
        {shown.map((name) => {
          const stat = stats.get(name);
          // A fact from before `title` existed still gets one -- fact_stats
          // derives it from the body server-side -- so this only stands in
          // for the still-loading gap before the first successful poll.
          const title = stat?.title || name;
          const body = bodies.data?.[name];
          const excerpt = body ? cutAtBoundary(body, 140) : '';
          return (
            <li key={name} className="flex flex-col gap-[2px] min-w-0 list-none">
              <div className="flex items-baseline gap-2 min-w-0">
                <h3
                  className="text-[16px] leading-[1.3] m-0 truncate"
                  style={{ fontFamily: SANS, color: WALL.ink, fontWeight: 500 }}
                >
                  {title}
                </h3>
                <span
                  className="text-[12px] shrink-0"
                  style={{ fontFamily: MONO, color: WALL.inkFaint }}
                >
                  · {name}
                </span>
              </div>
              <BeliefRow
                lines={2}
                meta={
                  stat ? (
                    <Age
                      at={stat.written_at}
                      now={now}
                      suffix={stat.writes > 1 ? `${stat.writes}× skriven` : undefined}
                    />
                  ) : undefined
                }
              >
                {excerpt}
              </BeliefRow>
            </li>
          );
        })}

        {shown.length === 0 && !loading && (
          <li className="list-none">
            <EmptyState why="write_fact har inte körts i den här loopen än">
              Inget nedskrivet ännu.
            </EmptyState>
          </li>
        )}
      </ul>
    </div>
  );
};

const AiBrainWallBeliefs: React.FC<{
  /** Already narrowed to the columns to show, in display order. */
  agents: { name: string; facts: number }[];
  now: number;
}> = ({ agents, now }) => (
  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4 flex-1 min-h-0 overflow-hidden">
    {agents.map((a) => (
      <BeliefColumn key={a.name} agent={a.name} facts={a.facts} now={now} />
    ))}
  </div>
);

export default AiBrainWallBeliefs;
