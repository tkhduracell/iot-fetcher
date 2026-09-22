'use client';

import React, { useCallback, useMemo, useState } from 'react';
import {
  type AgentDetailPlus,
  type AgentSummary,
  type Revision,
  type Status,
  type UsefulnessResponse,
  fetchAgentPlus,
  fetchAgents,
  fetchStatus,
  fetchUsefulness,
  formatAgo,
  formatDay,
} from '../lib/aiBrain';
import useAiBrain from '../hooks/useAiBrain';
import AiBrainCharterUsefulness from './AiBrainCharterUsefulness';
import {
  Age,
  CloseButton,
  EmptyState,
  MONO,
  SANS,
  SERIF,
  Section,
  WALL,
  WallShell,
  useServerClock,
} from './AiBrainWallTheme';

/** `/ai-brain/charter` — the answer to "den är inte nyttig än".
 *
 *  Three things, in the order they answer the question. The goals the brain
 *  wrote for itself, because nobody else wrote them. The drift in how it
 *  describes itself — the oldest identity it kept against the one it holds now
 *  — because that drift is the readable part of why its behaviour changed. And
 *  nyttan: the share of cycles that produced anything at all.
 *
 *  Every body here is a document the brain rewrites itself, served capped at
 *  2000 characters with a `truncated` flag. A capped body is never rendered as
 *  if it were the whole document. */

const POLL_MS = 30_000;

/** The revision a drift comparison starts from: the oldest kept body.
 *
 *  History arrives newest first and holds *superseded* bodies only — the live
 *  document is the agent's own `goals`/`identity` field — so the oldest is the
 *  last element, and the newest side of the comparison is the live one. */
export function oldestRevision(history: Revision[] | undefined): Revision | null {
  const h = history ?? [];
  return h.length > 0 ? h[h.length - 1] : null;
}

/** A document body as the brain wrote it, with the API's 2000-char cap named
 *  where it bites. Rendering a capped body silently would present a fragment as
 *  the whole charter. */
const DocBody: React.FC<{
  body: string | undefined;
  truncated?: boolean;
  dim?: boolean;
}> = ({ body, truncated, dim = false }) => {
  const text = (body ?? '').trim();
  if (!text) {
    return (
      <p className="text-[13px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
        tomt dokument
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <p
        className="text-[18px] leading-[1.45] m-0 whitespace-pre-wrap break-words max-w-[68ch]"
        style={{ fontFamily: SERIF, color: dim ? WALL.inkDim : WALL.ink, fontWeight: 400 }}
      >
        {text}
      </p>
      {truncated && (
        <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.amber }}>
          kapad av API:t vid 2000 tecken — det här är början av dokumentet, inte hela
        </p>
      )}
    </div>
  );
};

/** Superseded bodies, newest first, each behind its own disclosure so the
 *  screen stays readable when a document has been rewritten a dozen times. */
const RevisionList: React.FC<{ history: Revision[]; now: number; label: string }> = ({
  history,
  now,
  label,
}) => (
  <ol className="flex flex-col gap-2 list-none m-0 p-0">
    {history.map((rev, i) => (
      <li key={`${rev.at}-${i}`} className="min-w-0">
        <details>
          <summary
            className="cursor-pointer text-[13px]"
            style={{ fontFamily: MONO, color: WALL.inkDim }}
          >
            {formatDay(rev.at)} · {formatAgo(rev.at, now)} · {label} nr {history.length - i}
            {rev.truncated ? ' · kapad' : ''}
          </summary>
          <div className="pt-2 pl-3" style={{ borderLeft: `1px solid ${WALL.rule}` }}>
            <DocBody body={rev.body} truncated={rev.truncated} dim />
          </div>
        </details>
      </li>
    ))}
  </ol>
);

const AiBrainCharter: React.FC = () => {
  const agentsFetcher = useCallback((signal: AbortSignal) => fetchAgents(signal), []);
  const statusFetcher = useCallback((signal: AbortSignal) => fetchStatus(signal), []);
  const usefulnessFetcher = useCallback((signal: AbortSignal) => fetchUsefulness(signal), []);

  const agents = useAiBrain<{ agents: AgentSummary[] }>(agentsFetcher, [], POLL_MS);
  const status = useAiBrain<Status>(statusFetcher, [], POLL_MS);
  // Resolves to null (not an error) when /api/usefulness is unreachable.
  const usefulness = useAiBrain<UsefulnessResponse | null>(usefulnessFetcher, [], POLL_MS);
  const now = useServerClock(status.data?.now);

  const agentList = useMemo(() => agents.data?.agents ?? [], [agents.data]);
  const fallback = agentList.find((a) => a.priority === 'brain') ?? agentList[0];
  const [chosen, setChosen] = useState<string | null>(null);
  const name = chosen && agentList.some((a) => a.name === chosen) ? chosen : fallback?.name;

  const agentFetcher = useCallback(
    (signal: AbortSignal) => fetchAgentPlus(name ?? '', signal),
    [name],
  );
  const agent = useAiBrain<AgentDetailPlus>(agentFetcher, [name], POLL_MS, Boolean(name));

  const detail = agent.data;
  const goalsHistory = detail?.goals_history ?? [];
  const identityHistory = detail?.identity_history ?? [];
  const firstIdentity = oldestRevision(identityHistory);

  const usefulnessData = usefulness.data;
  const usefulnessLoops = usefulnessData?.loops ?? [];
  const hasUsefulness = Boolean(usefulnessData && usefulnessData.totals);

  return (
    <WallShell
      density="read"
      current="/ai-brain/charter"
      close={<CloseButton />}
      headerRight={
        agentList.length > 1 ? (
          <div className="flex flex-wrap items-center gap-2 shrink-0" role="group" aria-label="Loop">
            {agentList.map((a) => {
              const active = a.name === name;
              return (
                <button
                  key={a.name}
                  type="button"
                  onClick={() => setChosen(a.name)}
                  aria-pressed={active}
                  className="rounded-full px-3 py-[2px] text-[12px]"
                  style={{
                    fontFamily: MONO,
                    color: active ? WALL.ink : WALL.inkFaint,
                    border: `1px solid ${active ? WALL.ink : WALL.rule}`,
                    background: 'transparent',
                    cursor: 'pointer',
                  }}
                >
                  {a.name}
                </button>
              );
            })}
          </div>
        ) : undefined
      }
      footer={
        <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
          /api/agents/{name ?? '–'} · /api/usefulness · dokumenten skriver hjärnan själv · vyn är
          skrivskyddad, ändringar sägs i Slack
        </p>
      }
    >
      <Section
        title="Mål den satt själv"
        accent={WALL.amber}
        show={Boolean(detail && (detail.goals ?? '').trim())}
        empty={
          !name ? (
            <EmptyState why={agents.error ? 'ingen kontakt med ai-brain' : 'hämtar looparna …'}>
              Vet inte vilka mål som finns.
            </EmptyState>
          ) : (
            <EmptyState why="goals.md är tom — hjärnan skriver den själv först när den bestämt sig för något">
              Den har inte satt några mål ännu.
            </EmptyState>
          )
        }
      >
        <div className="flex flex-col gap-3">
          <DocBody body={detail?.goals} />
          {goalsHistory.length > 0 ? (
            <div className="flex flex-col gap-2">
              <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
                omskriven {goalsHistory.length}{' '}
                {goalsHistory.length === 1 ? 'gång' : 'gånger'} · senast{' '}
                <Age at={goalsHistory[0]?.at} now={now} />
              </p>
              <RevisionList history={goalsHistory} now={now} label="mål" />
            </div>
          ) : (
            <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
              ingen tidigare version sparad — goals.md har inte skrivits om sedan den skapades
            </p>
          )}
        </div>
      </Section>

      <Section
        title="Hur den beskriver sig"
        accent={WALL.clay}
        show={Boolean(detail && ((detail.identity ?? '').trim() || identityHistory.length > 0))}
        empty={
          <EmptyState why="identity.md är tom och saknar historik — driften syns först när hjärnan skrivit om sig själv minst en gång">
            Den har inte beskrivit sig ännu.
          </EmptyState>
        }
      >
        <div className="flex flex-col gap-4">
          {firstIdentity ? (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-4">
              <div className="flex flex-col gap-2 min-w-0">
                <p
                  className="text-[12px] uppercase tracking-[0.18em] m-0"
                  style={{ fontFamily: SANS, color: WALL.inkFaint }}
                >
                  Först · {formatDay(firstIdentity.at)}
                </p>
                <DocBody body={firstIdentity.body} truncated={firstIdentity.truncated} dim />
              </div>
              <div className="flex flex-col gap-2 min-w-0">
                <p
                  className="text-[12px] uppercase tracking-[0.18em] m-0"
                  style={{ fontFamily: SANS, color: WALL.clay }}
                >
                  Nu
                </p>
                <DocBody body={detail?.identity} />
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <DocBody body={detail?.identity} />
              <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
                ingen drift att visa — identity.md har inte skrivits om sedan den skapades, så det
                finns bara en version
              </p>
            </div>
          )}

          {identityHistory.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-[12px] m-0" style={{ fontFamily: MONO, color: WALL.inkFaint }}>
                {identityHistory.length}{' '}
                {identityHistory.length === 1 ? 'tidigare version' : 'tidigare versioner'}
              </p>
              <RevisionList history={identityHistory} now={now} label="identitet" />
            </div>
          )}
        </div>
      </Section>

      <Section
        title="Nyttan"
        accent={WALL.sage}
        show={hasUsefulness}
        empty={
          usefulness.initialLoading ? (
            <EmptyState why="hämtar /api/usefulness">Räknar varven …</EmptyState>
          ) : (
            <EmptyState why="/api/usefulness svarade inte — utan den går det inte att säga om hjärnan gör nytta, och en nolla här vore en gissning">
              Vet inte vad varven gav.
            </EmptyState>
          )
        }
      >
        {usefulnessData && (
          <AiBrainCharterUsefulness totals={usefulnessData.totals} loops={usefulnessLoops} />
        )}
      </Section>

      {/* constitution.md is the document the brain may not rewrite, and the
          introspection API does not serve it at all. Saying so is more honest
          than an empty panel that looks like a brain with no constitution. */}
      <Section
        title="Constitution"
        show={false}
        empty={
          <EmptyState why="/api/agents serverar identity.md och goals.md, men inte constitution.md — den lever bara på disk i memory_root">
            Grundlagen visas inte här.
          </EmptyState>
        }
      >
        {null}
      </Section>
    </WallShell>
  );
};

export default AiBrainCharter;
