'use client';

import React, { useCallback, useMemo, useState } from 'react';
import {
  type AgentDetailPlus,
  type AgentSummary,
  type FactStat,
  type Gap,
  type Status,
  fetchAgentPlus,
  fetchAgents,
  fetchFactBodies,
  fetchStatus,
  formatDay,
  freshnessTone,
  sortFactStats,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import AiBrainKnowledgeFacts, { type FactRowData, claimOf } from './AiBrainKnowledgeFacts';
import AiBrainKnowledgeFilters, { ALL_LOOPS } from './AiBrainKnowledgeFilters';
import {
  Age,
  BackLink,
  BeliefRow,
  EmptyState,
  MONO,
  Mono,
  Pill,
  SERIF,
  Section,
  WALL,
  WallShell,
  toneColor,
  useServerClock,
} from './AiBrainWallTheme';

/** `/ai-brain/knowledge` — the fact base as a body of knowledge.
 *
 *  This screen is the answer to "jag kan inte se vad den vet". The wall shows
 *  three columns of four facts because it is glanced at from across a room;
 *  here every fact every loop has written is listed, each as the sentence it
 *  claims, with the loop that owns it, when it was last written and how many
 *  times it has been rewritten.
 *
 *  It also carries the three things the wall deliberately does not:
 *   - **luckor**, the known unknowns a loop recorded with `note_gap`,
 *   - **det som börjar ruttna**, the facts nothing has touched for longest,
 *   - **taket**, how close each loop is to the 40-fact cap that triggers
 *     compaction, so compaction never happens as a surprise.
 *
 *  Density is `read`: this is a screen you stand in front of, it may scroll,
 *  and a fact's claim still has to be legible at a glance with the full body
 *  one button away. */

const POLL_MS = 30_000;
/** Agent detail carries fact_stats and gaps; it changes per cycle, not per second. */
const DETAIL_POLL_MS = 30_000;
/** Fact bodies change on the order of cycles. Each one is its own request. */
const BODY_POLL_MS = 120_000;

/** How many fact bodies this screen will pull at once.
 *
 *  There is no bulk endpoint — `fetchFactBodies` fans out over
 *  `/api/agents/{name}/facts/{fact}` — so this is a real network budget, not a
 *  layout choice. Facts beyond it still list (by their humanized name) and get
 *  their body as soon as the loop filter narrows the set. */
const BODY_BUDGET = 48;

/** The compaction threshold. SEAM: ai-brain compacts a loop's memory at 40
 *  facts but serves neither the cap nor the loop's distance from it — only the
 *  `needs_compaction` boolean and the count. Mirrored here so the pressure is
 *  visible before the flag flips; if the brain's cap moves, this moves. */
const FACT_CAP = 40;

/** Facts shown under "börjar ruttna" — the oldest, not all of them. */
const MAX_ROTTING = 6;

type Detail = { name: string; detail: AgentDetailPlus | null };

function statMap(stats: FactStat[] | undefined): Map<string, FactStat> {
  const map = new Map<string, FactStat>();
  for (const s of stats ?? []) {
    if (s && typeof s.name === 'string') map.set(s.name, s);
  }
  return map;
}

const AiBrainKnowledge: React.FC = () => {
  const [loop, setLoop] = useState<string>(ALL_LOOPS);
  const [query, setQuery] = useState('');

  const statusFetcher = useCallback((signal: AbortSignal) => fetchStatus(signal), []);
  const agentsFetcher = useCallback((signal: AbortSignal) => fetchAgents(signal), []);

  const status = useAiBrain<Status>(statusFetcher, [], POLL_MS);
  const agents = useAiBrain<{ agents: AgentSummary[] }>(agentsFetcher, [], POLL_MS);

  // ai-brain's clock, not this tablet's — see `useServerClock`.
  const now = useServerClock(status.data?.now);

  const agentList = useMemo(() => agents.data?.agents ?? [], [agents.data]);
  const agentNames = useMemo(() => agentList.map((a) => a.name), [agentList]);
  const namesKey = agentNames.join('\u0000');

  /** Every loop's detail in one poll. `fact_stats` and `gaps` only exist here,
   *  not on the summary, so the screen cannot be built from `/api/agents`
   *  alone. Settled rather than all: one unreachable loop costs its own block. */
  const detailsFetcher = useCallback(
    async (signal: AbortSignal): Promise<Detail[]> => {
      const settled = await Promise.allSettled(
        agentNames.map((name) => fetchAgentPlus(name, signal)),
      );
      return agentNames.map((name, i) => {
        const r = settled[i];
        return { name, detail: r.status === 'fulfilled' ? r.value : null };
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [namesKey],
  );
  const details = useAiBrain<Detail[]>(
    detailsFetcher,
    [namesKey],
    DETAIL_POLL_MS,
    agentNames.length > 0,
  );

  const detailList = useMemo(() => details.data ?? [], [details.data]);

  /** Every fact of every loop, newest write first. Facts without a `fact_stats`
   *  entry keep the API's own order behind the dated ones — a freshly deployed
   *  brain has no stats at all and must still list what it knows. */
  const allRows = useMemo<FactRowData[]>(() => {
    const rows: FactRowData[] = [];
    for (const { name, detail } of detailList) {
      const stats = statMap(detail?.fact_stats);
      for (const fact of detail?.fact_names ?? []) {
        rows.push({ agent: name, name: fact, stat: stats.get(fact) });
      }
    }
    return rows.sort((a, b) => {
      const at = a.stat?.written_at ?? -1;
      const bt = b.stat?.written_at ?? -1;
      if (at !== bt) return bt - at;
      return a.agent.localeCompare(b.agent, 'sv') || a.name.localeCompare(b.name, 'sv');
    });
  }, [detailList]);

  /** The loop filter decides what gets a body fetched; the text filter does not,
   *  so typing never fires a request. */
  const scoped = useMemo(
    () => (loop === ALL_LOOPS ? allRows : allRows.filter((r) => r.agent === loop)),
    [allRows, loop],
  );

  const wanted = useMemo(() => scoped.slice(0, BODY_BUDGET), [scoped]);
  const wantedKey = wanted.map((r) => `${r.agent}/${r.name}`).join('\u0000');

  const bodiesFetcher = useCallback(
    async (signal: AbortSignal): Promise<Record<string, string>> => {
      const byAgent = new Map<string, string[]>();
      for (const r of wanted) {
        const list = byAgent.get(r.agent) ?? [];
        list.push(r.name);
        byAgent.set(r.agent, list);
      }
      const out: Record<string, string> = {};
      const results = await Promise.all(
        [...byAgent.entries()].map(async ([agent, names]) => ({
          agent,
          bodies: await fetchFactBodies(agent, names, signal),
        })),
      );
      for (const { agent, bodies } of results) {
        for (const [name, body] of Object.entries(bodies)) out[`${agent}/${name}`] = body;
      }
      return out;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [wantedKey],
  );
  const bodies = useAiBrain<Record<string, string>>(
    bodiesFetcher,
    [wantedKey],
    BODY_POLL_MS,
    wanted.length > 0,
  );

  const withBodies = useMemo(
    () => scoped.map((r) => ({ ...r, body: bodies.data?.[`${r.agent}/${r.name}`] })),
    [scoped, bodies.data],
  );

  /** The text filter reads the claim the reader sees, plus the fact's own name
   *  so a fact whose body has not arrived is still findable. */
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return withBodies;
    return withBodies.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        claimOf(r).toLowerCase().includes(q) ||
        (r.body ?? '').toLowerCase().includes(q),
    );
  }, [withBodies, query]);

  /** Open gaps across every loop, oldest question first — a known unknown that
   *  has been open longest is the one that has gone unanswered longest. */
  const gaps = useMemo(() => {
    const out: { agent: string; gap: Gap }[] = [];
    for (const { name, detail } of detailList) {
      if (loop !== ALL_LOOPS && name !== loop) continue;
      for (const gap of detail?.gaps ?? []) out.push({ agent: name, gap });
    }
    return out.sort((a, b) => (a.gap.opened_at ?? 0) - (b.gap.opened_at ?? 0));
  }, [detailList, loop]);

  /** What nothing has touched for longest: warn (>1 dygn) and error (>7 dygn)
   *  by `freshnessTone`, oldest first. Facts with no write time cannot rot —
   *  they have no age to measure. */
  const rotting = useMemo(
    () =>
      scoped
        .filter((r) => r.stat && freshnessTone(r.stat.written_at, now) !== 'ok')
        .sort((a, b) => (a.stat?.written_at ?? 0) - (b.stat?.written_at ?? 0))
        .slice(0, MAX_ROTTING),
    [scoped, now],
  );

  const anyStats = useMemo(
    () => detailList.some((d) => sortFactStats(d.detail?.fact_stats).length > 0),
    [detailList],
  );

  const loopsWithCounts = useMemo(
    () =>
      [...agentList]
        .sort((a, b) => (b.facts ?? 0) - (a.facts ?? 0))
        .map((a) => ({ name: a.name, facts: a.facts ?? 0 })),
    [agentList],
  );

  const totalFacts = allRows.length;
  const offline = Boolean((status.error || agents.error) && !status.data && !agents.data);
  const loading = agents.initialLoading || (agentNames.length > 0 && details.initialLoading);

  const headline = offline
    ? 'Ingen kontakt med ai-brain.'
    : loading
      ? 'Läser hjärnans minne …'
      : totalFacts === 0
        ? 'Hjärnan har inte skrivit ner något om huset ännu.'
        : `Hjärnan har skrivit ner ${totalFacts} fakta över ${agentNames.length} ${
            agentNames.length === 1 ? 'loop' : 'loopar'
          }${gaps.length > 0 ? `, och vet om ${gaps.length} ${gaps.length === 1 ? 'lucka' : 'luckor'}` : ''}.`;

  return (
    <WallShell
      density="read"
      title="Kunskap"
      current="/ai-brain/knowledge"
      back={<BackLink />}
      headerRight={
        <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          {offline ? 'ingen kontakt' : `taket ${FACT_CAP} fakta per loop`}
        </span>
      }
    >
      <p
        className="text-[24px] sm:text-[28px] leading-[1.25] max-w-[46ch] m-0"
        style={{ fontFamily: SERIF, color: WALL.ink, fontWeight: 400 }}
      >
        {headline}
      </p>

      {/* The three things the wall has no room for. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-x-8 gap-y-6">
        <Section
          title="Luckor"
          accent={WALL.amber}
          show={gaps.length > 0}
          empty={
            <EmptyState
              why={
                offline
                  ? 'ingen kontakt med ai-brain'
                  : 'en lucka finns först när en loop kallar note_gap — ingen har gjort det än'
              }
            >
              Hjärnan har inte skrivit ner något den undrar över.
            </EmptyState>
          }
        >
          <ul className="flex flex-col gap-3 list-none m-0 p-0">
            {gaps.map(({ agent, gap }) => (
              <BeliefRow
                key={`${agent}/${gap.id}`}
                density="read"
                muted
                lines={0}
                kicker={agent}
                meta={
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    {gap.why ? <span>{gap.why}</span> : null}
                    <Age at={gap.opened_at} now={now} suffix="öppen" />
                  </span>
                }
              >
                {gap.question}
              </BeliefRow>
            ))}
          </ul>
        </Section>

        <Section
          title="Börjar ruttna"
          accent={WALL.rose}
          show={rotting.length > 0}
          empty={
            <EmptyState
              why={
                !anyStats
                  ? 'fact_stats är tomt — hjärnan har inte registrerat någon skrivtid att åldras från'
                  : 'inget fakta är äldre än ett dygn'
              }
            >
              Ingenting har hunnit bli gammalt.
            </EmptyState>
          }
        >
          <ul className="flex flex-col gap-3 list-none m-0 p-0">
            {rotting.map((r) => (
              <li key={`${r.agent}/${r.name}`} className="flex items-baseline justify-between gap-3 min-w-0">
                <span className="text-[14px] truncate" style={{ fontFamily: MONO, color: WALL.ink }}>
                  {r.name}
                </span>
                <span className="shrink-0 flex items-baseline gap-2">
                  <span
                    className="text-[12px] tabular-nums"
                    style={{
                      fontFamily: MONO,
                      color: toneColor(freshnessTone(r.stat?.written_at, now)),
                    }}
                  >
                    {formatDay(r.stat?.written_at)}
                  </span>
                  <Age at={r.stat?.written_at} now={now} />
                </span>
              </li>
            ))}
          </ul>
        </Section>

        <Section
          title="Mot taket"
          accent={WALL.clay}
          show={loopsWithCounts.length > 0}
          empty={
            <EmptyState why={offline ? 'ingen kontakt med ai-brain' : 'inga loopar körs'}>
              Ingen loop att mäta.
            </EmptyState>
          }
        >
          <ul className="flex flex-col gap-2 list-none m-0 p-0">
            {loopsWithCounts.map((l) => {
              const agent = agentList.find((a) => a.name === l.name);
              const share = Math.min(1, l.facts / FACT_CAP);
              const tone = agent?.needs_compaction ? 'error' : share >= 0.75 ? 'warn' : 'ok';
              return (
                <li key={l.name} className="flex flex-col gap-1 min-w-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[14px] truncate" style={{ fontFamily: MONO, color: WALL.ink }}>
                      {l.name}
                    </span>
                    <span className="shrink-0 flex items-baseline gap-2">
                      <Mono className="text-[12px]">
                        <span style={{ color: toneColor(tone) }}>
                          {l.facts}/{FACT_CAP}
                        </span>
                      </Mono>
                      {agent?.needs_compaction && <Pill tone="error">vill kompaktera</Pill>}
                    </span>
                  </div>
                  <div className="h-[3px] w-full rounded" style={{ background: WALL.rule }}>
                    <div
                      className="h-full rounded"
                      style={{ width: `${share * 100}%`, background: toneColor(tone) }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </Section>
      </div>

      {/* The fact base itself. */}
      <Section
        title="Allt den vet"
        show={agentNames.length > 0}
        empty={
          <EmptyState why={offline ? 'ingen kontakt med ai-brain' : 'ai-brain kör inga loopar'}>
            Det finns ingen loop som kan veta något.
          </EmptyState>
        }
        action={
          bodies.data && wanted.length < scoped.length ? (
            <span className="text-[12px]" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
              texten hämtad för {wanted.length} av {scoped.length} — filtrera på en loop för resten
            </span>
          ) : undefined
        }
      >
        <div className="flex flex-col gap-5 min-w-0">
          <AiBrainKnowledgeFilters
            loops={loopsWithCounts}
            loop={loop}
            onLoop={setLoop}
            query={query}
            onQuery={setQuery}
            shown={shown.length}
            total={totalFacts}
          />

          {shown.length > 0 ? (
            <AiBrainKnowledgeFacts rows={shown} now={now} showAgent={loop === ALL_LOOPS} />
          ) : (
            <EmptyState
              why={
                loading
                  ? 'hämtar fakta från ai-brain'
                  : totalFacts === 0
                    ? 'write_fact har inte körts i någon loop än — hjärnan skriver ner något först när en cykel hittar något värt att minnas'
                    : query.trim()
                      ? `inget av ${scoped.length} fakta i urvalet innehåller ”${query.trim()}”`
                      : 'den här loopen har inte skrivit ner något'
              }
            >
              {loading
                ? 'Läser …'
                : totalFacts === 0
                  ? 'Inget nedskrivet ännu.'
                  : 'Inget fakta matchar filtret.'}
            </EmptyState>
          )}
        </div>
      </Section>
    </WallShell>
  );
};

export default AiBrainKnowledge;
