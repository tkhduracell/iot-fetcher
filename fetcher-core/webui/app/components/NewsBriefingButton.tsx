'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import NewsBriefingPanel, { BriefingStory, SourceStatus } from './NewsBriefingPanel';

type Phase = 'idle' | 'fetching' | 'writing' | 'speaking' | 'done' | 'error';

type Briefing = {
  transcript: string;
  stories: BriefingStory[];
  sources: SourceStatus[];
  room: string;
  volume: string;
};

/** Fetching and writing both happen inside one POST, so the client cannot see
 *  the boundary — flip the label once the fetch has realistically finished. */
const WRITING_LABEL_DELAY_MS = 6000;
const DONE_LABEL_MS = 3000;

const NewsBriefingButton: React.FC = () => {
  const [phase, setPhase] = useState<Phase>('idle');
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const check = () => setOpen(window.location.hash === '#news');
    check();
    window.addEventListener('hashchange', check);
    return () => window.removeEventListener('hashchange', check);
  }, []);

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const closePanel = useCallback(() => {
    history.pushState(null, '', window.location.pathname + window.location.search);
    setOpen(false);
  }, []);

  /** One /say call for the whole briefing — the script is kept short enough to
   *  fit a single URL segment and finish inside the proxy's 120s timeout. */
  const speak = useCallback(async (b: Briefing) => {
    setPhase('speaking');
    const resp = await fetch(
      `/sonos/${encodeURIComponent(b.room)}/say/${encodeURIComponent(b.transcript)}/${b.volume}`,
    );
    if (!resp.ok) {
      const detail = (await resp.text().catch(() => '')).trim().slice(0, 200);
      throw new Error(detail ? `Uppläsning misslyckades (${resp.status}): ${detail}` : `Uppläsning misslyckades (${resp.status})`);
    }
  }, []);

  const run = useCallback(async () => {
    if (phase !== 'idle' && phase !== 'done' && phase !== 'error') return;
    setError(null);
    setPhase('fetching');
    timers.current.push(setTimeout(() => {
      setPhase((p) => (p === 'fetching' ? 'writing' : p));
    }, WRITING_LABEL_DELAY_MS));

    try {
      const resp = await fetch('/api/news/briefing', { method: 'POST' });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(body?.error || `Kunde inte hämta nyheter (${resp.status})`);

      if (!body.transcript) {
        setPhase('error');
        setError('Inga Malmönyheter senaste dygnet');
        return;
      }

      const next: Briefing = body;
      setBriefing(next);
      history.pushState(null, '', '#news');
      setOpen(true);
      await speak(next);

      setPhase('done');
      timers.current.push(setTimeout(() => setPhase('idle'), DONE_LABEL_MS));
    } catch (e) {
      console.error('News briefing failed:', e);
      setError(e instanceof Error ? e.message : 'Något gick fel');
      setPhase('error');
    }
  }, [phase, speak]);

  const replay = useCallback(async () => {
    if (!briefing || phase === 'speaking') return;
    try {
      await speak(briefing);
      setPhase('done');
      timers.current.push(setTimeout(() => setPhase('idle'), DONE_LABEL_MS));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Något gick fel');
      setPhase('error');
    }
  }, [briefing, phase, speak]);

  const busy = phase === 'fetching' || phase === 'writing' || phase === 'speaking';

  const label = {
    idle: 'Nyheter',
    fetching: 'Hämtar…',
    writing: 'Skriver…',
    speaking: 'Läser upp…',
    done: 'Klar',
    error: 'Fel',
  }[phase];

  return (
    <>
      <button
        onClick={run}
        disabled={busy}
        title={error ?? undefined}
        className={`px-4 py-1.5 rounded-full shadow text-sm font-semibold cursor-pointer transition-colors duration-200 text-white flex items-center gap-1.5 disabled:cursor-default ${
          phase === 'error' ? 'bg-red-600 hover:bg-red-700' : 'bg-amber-600 hover:bg-amber-700 disabled:hover:bg-amber-600'
        }`}
      >
        {busy ? (
          <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
        ) : (
          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M2 5a2 2 0 012-2h8a2 2 0 012 2v10a2 2 0 002 2H4a2 2 0 01-2-2V5zm3 1h6v2H5V6zm0 4h6v1H5v-1zm0 3h4v1H5v-1z" clipRule="evenodd" />
            <path d="M15 7h1a2 2 0 012 2v6a1 1 0 11-2 0V9h-1V7z" />
          </svg>
        )}
        {label}
      </button>
      {open && briefing && (
        <NewsBriefingPanel
          transcript={briefing.transcript}
          stories={briefing.stories}
          sources={briefing.sources}
          replaying={phase === 'speaking'}
          onReplay={replay}
          onClose={closePanel}
        />
      )}
    </>
  );
};

export default NewsBriefingButton;
