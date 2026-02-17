import { useState, useEffect, useCallback, useRef, useReducer } from 'react';

export type PomodoroPhase = 'work' | 'break';
export type PomodoroState = 'idle' | 'running' | 'paused';

interface TimerState {
  phase: PomodoroPhase;
  state: PomodoroState;
  timeRemaining: number;
  wasTransitioned: boolean;
}

type TimerAction =
  | { type: 'START' }
  | { type: 'PAUSE' }
  | { type: 'RESET' }
  | { type: 'TICK' }
  | { type: 'TRANSITION_PHASE' }
  | { type: 'SKIP_TO_NEXT_PHASE' }
  | { type: 'CLEAR_TRANSITION_FLAG' };

const WORK_DURATION = 25 * 60;
const BREAK_DURATION = 5 * 60;
const NOTIFICATION_DISMISS_DELAY = 5000;

const STORAGE_KEY = 'pomodoroTimerState';

interface PersistedState {
  phase: PomodoroPhase;
  state: PomodoroState;
  timeRemaining: number;
  lastUpdated: number;
}

function loadPersistedState(): PersistedState | null {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as PersistedState;
    const validPhases = ['work', 'break'] as const;
    const validStates = ['idle', 'running', 'paused'] as const;
    if (
      (validPhases as readonly string[]).includes(parsed.phase) &&
      (validStates as readonly string[]).includes(parsed.state) &&
      typeof parsed.timeRemaining === 'number' &&
      parsed.timeRemaining >= 0 &&
      typeof parsed.lastUpdated === 'number' &&
      parsed.lastUpdated > 0 &&
      parsed.lastUpdated <= Date.now()
    ) {
      return parsed;
    }
    return null;
  } catch (error) {
    console.error('Failed to load persisted Pomodoro state:', error);
    return null;
  }
}

function savePersistedState(phase: PomodoroPhase, state: PomodoroState, timeRemaining: number): void {
  const data: PersistedState = {
    phase,
    state,
    timeRemaining,
    lastUpdated: Date.now()
  };
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (error) {
    console.error('Failed to save Pomodoro state to sessionStorage:', error);
  }
}

function clearPersistedState(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch (error) {
    console.error('Failed to clear Pomodoro state from sessionStorage:', error);
  }
}

const WORK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/99591aa7-ec53-40fc-b6c4-f768f1a73881/d0794f38-b1c8-43f9-9ca3-432ad18a7710/preview.m4a';
const BREAK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/dc07756a-54e3-4851-acc4-9fce465ee255/54252202-2ca1-45b3-963c-a3747597aac5/preview.m4a';

const workCompleteAudio = typeof window !== 'undefined' ? new Audio(WORK_COMPLETE_SOUND) : null;
const breakCompleteAudio = typeof window !== 'undefined' ? new Audio(BREAK_COMPLETE_SOUND) : null;

const SONOS_SPEAKER = 'Kontor';
const SONOS_VOLUME = 40;

function speakOnSonos(message: string) {
  const encodedMessage = encodeURIComponent(message);
  const encodedSpeaker = encodeURIComponent(SONOS_SPEAKER);
  fetch(`/sonos/${encodedSpeaker}/say/${encodedMessage}/${SONOS_VOLUME}`)
    .then(response => {
      if (!response.ok) {
        console.error(`Failed to speak "${message}" on Sonos speaker "${SONOS_SPEAKER}": ${response.status}`);
      }
    })
    .catch(err => console.error(`Failed to speak "${message}" on Sonos speaker "${SONOS_SPEAKER}":`, err));
}

export interface PomodoroTimerState {
  phase: PomodoroPhase;
  state: PomodoroState;
  timeRemaining: number;
  showNotification: boolean;
}

export interface PomodoroTimerActions {
  start: () => void;
  pause: () => void;
  reset: () => void;
  dismissNotification: () => void;
  skipToNextPhase: () => void;
}

function playSound(audio: HTMLAudioElement | null) {
  if (!audio) return;
  audio.currentTime = 0;
  audio.play().catch(err => console.error('Failed to play sound:', err));
}

function timerReducer(state: TimerState, action: TimerAction): TimerState {
  switch (action.type) {
    case 'START':
      return { ...state, state: 'running' };

    case 'PAUSE':
      return { ...state, state: 'paused' };

    case 'RESET':
      return {
        phase: 'work',
        state: 'idle',
        timeRemaining: WORK_DURATION,
        wasTransitioned: false
      };

    case 'TICK':
      return {
        ...state,
        timeRemaining: Math.max(0, state.timeRemaining - 1)
      };

    case 'TRANSITION_PHASE': {
      const nextPhase: PomodoroPhase = state.phase === 'work' ? 'break' : 'work';
      const nextDuration = nextPhase === 'work' ? WORK_DURATION : BREAK_DURATION;
      return {
        phase: nextPhase,
        state: 'running',
        timeRemaining: nextDuration,
        wasTransitioned: false
      };
    }

    case 'SKIP_TO_NEXT_PHASE': {
      if (state.state === 'idle') return state;
      const nextPhase: PomodoroPhase = state.phase === 'work' ? 'break' : 'work';
      const nextDuration = nextPhase === 'work' ? WORK_DURATION : BREAK_DURATION;
      return {
        phase: nextPhase,
        state: 'running',
        timeRemaining: nextDuration,
        wasTransitioned: false
      };
    }

    case 'CLEAR_TRANSITION_FLAG':
      return { ...state, wasTransitioned: false };

    default:
      return state;
  }
}

function getInitialState(): TimerState {
  if (typeof window === 'undefined') {
    return { phase: 'work', state: 'idle', timeRemaining: WORK_DURATION, wasTransitioned: false };
  }

  const persisted = loadPersistedState();
  if (!persisted) {
    return { phase: 'work', state: 'idle', timeRemaining: WORK_DURATION, wasTransitioned: false };
  }

  const elapsedMs = Date.now() - persisted.lastUpdated;
  const elapsedSeconds = Math.floor(elapsedMs / 1000);

  if (persisted.state === 'running') {
    const newTimeRemaining = Math.max(0, persisted.timeRemaining - elapsedSeconds);

    if (newTimeRemaining === 0) {
      const nextPhase: PomodoroPhase = persisted.phase === 'work' ? 'break' : 'work';
      const nextDuration = nextPhase === 'work' ? WORK_DURATION : BREAK_DURATION;
      return {
        phase: nextPhase,
        state: 'running',
        timeRemaining: nextDuration,
        wasTransitioned: true
      };
    }

    return {
      phase: persisted.phase,
      state: persisted.state,
      timeRemaining: newTimeRemaining,
      wasTransitioned: false
    };
  }

  return {
    phase: persisted.phase,
    state: persisted.state,
    timeRemaining: persisted.timeRemaining,
    wasTransitioned: false
  };
}

export function usePomodoroTimer(): [PomodoroTimerState, PomodoroTimerActions] {
  const [timerState, dispatch] = useReducer(timerReducer, null, getInitialState);
  const [showNotification, setShowNotification] = useState(false);
  const notificationTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    if (timerState.state === 'idle') {
      clearPersistedState();
    } else if (!timerState.wasTransitioned) {
      savePersistedState(timerState.phase, timerState.state, timerState.timeRemaining);
    }
  }, [timerState]);

  const start = useCallback(() => {
    dispatch({ type: 'START' });
    speakOnSonos("let's go");
  }, []);

  const pause = useCallback(() => {
    dispatch({ type: 'PAUSE' });
    speakOnSonos("let's pause");
  }, []);

  const reset = useCallback(() => {
    dispatch({ type: 'RESET' });
    setShowNotification(false);
    if (notificationTimeoutRef.current) {
      clearTimeout(notificationTimeoutRef.current);
      notificationTimeoutRef.current = null;
    }
  }, []);

  const dismissNotification = useCallback(() => {
    setShowNotification(false);
    if (notificationTimeoutRef.current) {
      clearTimeout(notificationTimeoutRef.current);
      notificationTimeoutRef.current = null;
    }
  }, []);

  const skipToNextPhase = useCallback(() => {
    dispatch({ type: 'SKIP_TO_NEXT_PHASE' });
  }, []);

  useEffect(() => {
    if (timerState.state !== 'running') return;

    const interval = setInterval(() => {
      dispatch({ type: 'TICK' });
    }, 1000);

    return () => clearInterval(interval);
  }, [timerState.state]);

  useEffect(() => {
    if (timerState.state !== 'running') return;
    if (timerState.timeRemaining !== 0) return;

    if (!timerState.wasTransitioned) {
      playSound(timerState.phase === 'work' ? workCompleteAudio : breakCompleteAudio);

      setShowNotification(true);

      if (notificationTimeoutRef.current) {
        clearTimeout(notificationTimeoutRef.current);
      }
      notificationTimeoutRef.current = window.setTimeout(() => {
        setShowNotification(false);
        notificationTimeoutRef.current = null;
      }, NOTIFICATION_DISMISS_DELAY);

      dispatch({ type: 'TRANSITION_PHASE' });
    } else {
      dispatch({ type: 'CLEAR_TRANSITION_FLAG' });
    }
  }, [timerState.timeRemaining, timerState.state]);

  useEffect(() => {
    return () => {
      if (notificationTimeoutRef.current) {
        clearTimeout(notificationTimeoutRef.current);
      }
    };
  }, []);

  return [
    { phase: timerState.phase, state: timerState.state, timeRemaining: timerState.timeRemaining, showNotification },
    { start, pause, reset, dismissNotification, skipToNextPhase }
  ];
}

export function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}
