'use client';

import React from 'react';
import { createPortal } from 'react-dom';

export type BriefingStory = {
  title: string;
  url: string;
  sources: string[];
  publishedAt: string | null;
};

export type SourceStatus = { source: string; count: number; error: string | null };

type Props = {
  transcript: string;
  room: string;
  stories: BriefingStory[];
  sources: SourceStatus[];
  replaying: boolean;
  onReplay: () => void;
  onClose: () => void;
};

const NewsBriefingPanel: React.FC<Props> = ({
  transcript, room, stories, sources, replaying, onReplay, onClose,
}) => {
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const failed = sources.filter((s) => s.error || s.count === 0);
  // Cues are directions for the voice, not part of the bulletin — show them
  // dimmed rather than inline, so the panel reads like what you just heard.
  const segments = transcript.split(/(\[[^\]]+\])/g).filter(Boolean);

  return createPortal(
    <div
      className="fixed inset-0 z-[110] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[85vh] overflow-y-auto bg-gray-800 rounded-2xl shadow-2xl p-6 flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-white">Malmökollen</h2>
            <p className="text-xs text-gray-400">Senaste dygnet, uppläst i {room}</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Stäng"
            className="w-9 h-9 flex-shrink-0 rounded-full bg-gray-700 hover:bg-gray-600 text-white flex items-center justify-center transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <p className="text-sm leading-relaxed text-gray-100">
          {segments.map((seg, i) =>
            seg.startsWith('[') && seg.endsWith(']') ? (
              <span key={i} className="text-gray-500 text-xs italic">{seg} </span>
            ) : (
              <span key={i}>{seg}</span>
            ),
          )}
        </p>

        {stories.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Källor</span>
            <ul className="flex flex-col gap-1">
              {stories.map((s) => (
                <li key={s.url}>
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-gray-300 hover:text-white underline decoration-gray-600 hover:decoration-gray-300"
                  >
                    {s.title}
                    <span className="text-gray-500"> — {s.sources.join(', ')}</span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {failed.length > 0 && (
          <p className="text-xs text-amber-400">
            {failed.map((f) => `${f.source} ${f.error ? 'kunde inte hämtas' : 'gav inga nyheter'}`).join(' · ')}
          </p>
        )}

        <button
          onClick={onReplay}
          disabled={replaying}
          className="w-full bg-amber-600 hover:bg-amber-500 disabled:opacity-40 disabled:hover:bg-amber-600 text-white font-semibold rounded-lg px-4 py-3 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
        >
          {replaying ? 'Läser upp…' : 'Läs upp igen'}
        </button>
      </div>
    </div>,
    document.body,
  );
};

export default NewsBriefingPanel;
