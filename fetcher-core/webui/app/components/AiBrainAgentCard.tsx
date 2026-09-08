'use client';

import React from 'react';
import {
  type AgentSummary,
  type Tone,
  formatAgo,
  formatIn,
  shortModel,
  statusTone,
} from '../lib/aiBrain';
import { Pill } from './AiBrainSupervisor';

const TONE_CLASSES: Record<Tone, string> = {
  ok: 'bg-green-600 dark:bg-green-700 text-white',
  warn: 'bg-yellow-500 dark:bg-yellow-600 text-white',
  error: 'bg-red-600 dark:bg-red-700 text-white',
  busy: 'bg-blue-600 dark:bg-blue-700 text-white',
  idle: 'bg-gray-500 dark:bg-gray-600 text-white',
};

const DOT_CLASSES: Record<Tone, string> = {
  ok: 'bg-green-500',
  warn: 'bg-yellow-500',
  error: 'bg-red-500',
  busy: 'bg-blue-500',
  idle: 'bg-gray-400',
};

const CHIP = 'px-1 py-px rounded text-[10px] font-semibold whitespace-nowrap';

type Props = {
  agent: AgentSummary;
  /** Ticks once a second so the countdown moves without re-polling. */
  now: number;
  selected: boolean;
  onSelect: (name: string) => void;
};

const AiBrainAgentCard: React.FC<Props> = ({ agent, now, selected, onSelect }) => {
  const tone = agent.in_progress ? 'busy' : statusTone(agent.last_cycle?.status);
  const status = agent.in_progress ? 'kör' : (agent.last_cycle?.status ?? 'okänd');

  // Line 2 is a single "·"-joined run so the parts collapse cleanly rather
  // than leaving a dangling separator when a cycle has never run.
  const meta = [
    agent.last_cycle?.model ? shortModel(agent.last_cycle.model) : 'ingen modell',
    agent.last_cycle ? `${agent.last_cycle.rounds} rundor` : null,
    formatAgo(agent.last_cycle_at, now),
    agent.in_progress ? null : `nästa ${formatIn(agent.next_wake_at, now)}`,
  ].filter(Boolean);

  const counts = Object.entries(agent.cycle_counts ?? {});

  return (
    <button
      type="button"
      onClick={() => onSelect(agent.name)}
      aria-pressed={selected}
      className={`text-left p-2 rounded-md bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 transition-colors flex flex-col gap-1 cursor-pointer min-w-0 ${
        selected
          ? 'ring-2 ring-blue-500 dark:ring-blue-400'
          : 'ring-blue-200 dark:ring-blue-800 hover:bg-blue-200 dark:hover:bg-blue-800'
      }`}
    >
      <div className="flex items-center gap-1.5 min-w-0">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${DOT_CLASSES[tone]} ${
            agent.in_progress ? 'animate-pulse' : ''
          }`}
        />
        <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
          {agent.name}
        </span>
        <span className="text-[10px] text-gray-600 dark:text-gray-400 shrink-0">
          {agent.priority === 'brain' ? 'hjärna' : 'expert'}
        </span>
        <Pill className={`ml-auto shrink-0 ${TONE_CLASSES[tone]}`}>{status}</Pill>
      </div>

      <div className="text-xs text-gray-700 dark:text-gray-300 tabular-nums truncate">
        {meta.join(' · ')}
      </div>

      {(counts.length > 0 ||
        agent.facts > 0 ||
        agent.unread_notes > 0 ||
        agent.needs_compaction) && (
        <div className="flex gap-1 flex-wrap">
          {counts.map(([key, count]) => (
            <span key={key} className={`${CHIP} ${TONE_CLASSES[statusTone(key)]} opacity-80`}>
              {key} {count}
            </span>
          ))}
          {agent.facts > 0 && (
            <span className={`${CHIP} bg-blue-200 dark:bg-blue-800 text-gray-900 dark:text-gray-100`}>
              {agent.facts} fakta
            </span>
          )}
          {agent.unread_notes > 0 && (
            <span className={`${CHIP} bg-purple-600 dark:bg-purple-700 text-white`}>
              {agent.unread_notes} olästa
            </span>
          )}
          {agent.needs_compaction && (
            <span className={`${CHIP} bg-orange-500 dark:bg-orange-600 text-white`}>
              komprimering
            </span>
          )}
        </div>
      )}
    </button>
  );
};

export default AiBrainAgentCard;
