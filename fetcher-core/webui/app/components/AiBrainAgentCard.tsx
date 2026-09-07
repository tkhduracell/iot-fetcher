'use client';

import React from 'react';
import { type AgentSummary, type Tone, formatAgo, formatIn, statusTone } from '../lib/aiBrain';
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

  return (
    <button
      type="button"
      onClick={() => onSelect(agent.name)}
      aria-pressed={selected}
      className={`text-left px-3 py-2.5 rounded-md bg-blue-100 dark:bg-blue-900 shadow-sm ring-1 transition-colors flex flex-col gap-1.5 cursor-pointer ${
        selected
          ? 'ring-2 ring-blue-500 dark:ring-blue-400'
          : 'ring-blue-200 dark:ring-blue-800 hover:bg-blue-200 dark:hover:bg-blue-800'
      }`}
    >
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${DOT_CLASSES[tone]} ${
            agent.in_progress ? 'animate-pulse' : ''
          }`}
        />
        <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
          {agent.name}
        </span>
        <Pill className={`ml-auto ${TONE_CLASSES[tone]}`}>{status}</Pill>
      </div>

      <div className="text-[11px] text-gray-700 dark:text-gray-300 flex flex-col gap-0.5">
        <span className="truncate">
          {agent.last_cycle?.model ?? 'ingen modell'}
          {agent.last_cycle ? ` · ${agent.last_cycle.rounds} rundor` : ''}
        </span>
        <span className="tabular-nums">
          {formatAgo(agent.last_cycle_at, now)}
          {agent.in_progress ? '' : ` · nästa ${formatIn(agent.next_wake_at, now)}`}
        </span>
      </div>

      <div className="flex gap-1 flex-wrap">
        {Object.entries(agent.cycle_counts ?? {}).map(([key, count]) => (
          <Pill
            key={key}
            className={`${TONE_CLASSES[statusTone(key)]} opacity-80 text-[10px]`}
          >
            {key} {count}
          </Pill>
        ))}
      </div>

      <div className="flex gap-1 flex-wrap">
        {agent.needs_compaction && (
          <Pill className="bg-orange-500 dark:bg-orange-600 text-white text-[10px]">
            behöver komprimering
          </Pill>
        )}
        <Pill className="bg-blue-200 dark:bg-blue-800 text-gray-900 dark:text-gray-100 text-[10px]">
          {agent.facts} fakta
        </Pill>
        {agent.unread_notes > 0 && (
          <Pill className="bg-purple-600 dark:bg-purple-700 text-white text-[10px]">
            {agent.unread_notes} olästa
          </Pill>
        )}
      </div>
    </button>
  );
};

export default AiBrainAgentCard;
