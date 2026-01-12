import { useState, useEffect, useCallback, useRef, useReducer } from 'react';

export type PomodoroPhase = 'work' | 'break';
export type PomodoroState = 'idle' | 'running' | 'paused';

// Internal timer state for useReducer
interface TimerState {
  phase: PomodoroPhase;
  state: PomodoroState;
  timeRemaining: number;
  wasTransitioned: boolean;
}

// Actions for timer reducer
type TimerAction =
  | { type: 'START' }
  | { type: 'PAUSE' }
  | { type: 'RESET' }
  | { type: 'TICK' }
  | { type: 'TRANSITION_PHASE' }
  | { type: 'SKIP_TO_NEXT_PHASE' }
  | { type: 'CLEAR_TRANSITION_FLAG' };

// Durations in seconds
const WORK_DURATION = 25 * 60; // 25 minutes
const BREAK_DURATION = 5 * 60; // 5 minutes
const NOTIFICATION_DISMISS_DELAY = 5000; // 5 seconds

// SessionStorage key for persisting timer state across page reloads
const STORAGE_KEY = 'pomodoroTimerState';

interface PersistedState {
  phase: PomodoroPhase;
  state: PomodoroState;
  timeRemaining: number;
  lastUpdated: number; // timestamp in milliseconds
}

function loadPersistedState(): PersistedState | null {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as PersistedState;
    // Validate the parsed object has required fields and valid enum values
    const validPhases = ['work', 'break'] as const;
    const validStates = ['idle', 'running', 'paused'] as const;
    if (
      (validPhases as readonly string[]).includes(parsed.phase) &&
      (validStates as readonly string[]).includes(parsed.state) &&
      typeof parsed.timeRemaining === 'number' &&
      parsed.timeRemaining >= 0 &&
      typeof parsed.lastUpdated === 'number' &&
      parsed.lastUpdated > 0 &&
      parsed.lastUpdated <= Date.now() // Prevent future timestamps
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

// Sound URLs (external assets)
const WORK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/99591aa7-ec53-40fc-b6c4-f768f1a73881/d0794f38-b1c8-43f9-9ca3-432ad18a7710/preview.m4a';
const BREAK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/dc07756a-54e3-4851-acc4-9fce465ee255/54252202-2ca1-45b3-963c-a3747597aac5/preview.m4a';

// Pre-loaded audio instances for better performance
const workCompleteAudio = new Audio(WORK_COMPLETE_SOUND);
workCompleteAudio.preload = 'auto';

const breakCompleteAudio = new Audio(BREAK_COMPLETE_SOUND);
breakCompleteAudio.preload = 'auto';

// Sonos TTS configuration
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

function playSound(audio: HTMLAudioElement) {
  // Ensure audio is loaded before playing
  if (audio.readyState < HTMLMediaElement.HAVE_ENOUGH_DATA) {
    // Audio not ready, wait for it to load then play
    const playWhenReady = () => {
      audio.currentTime = 0;
      audio.play().catch(() => {
        // Silently handle playback errors - audio may be blocked by browser autoplay policy
      });
      audio.removeEventListener('canplaythrough', playWhenReady);
    };
    audio.addEventListener('canplaythrough', playWhenReady);
    audio.load();
  } else {
    // Audio is ready, play immediately
    audio.currentTime = 0;
    audio.play().catch(() => {
      // Silently handle playback errors - audio may be blocked by browser autoplay policy
    });
  }
}

// Reducer for timer state management
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
  const persisted = loadPersistedState();
  if (!persisted) {
    return { phase: 'work', state: 'idle', timeRemaining: WORK_DURATION, wasTransitioned: false };
  }

  // Calculate elapsed time since last update
  const elapsedMs = Date.now() - persisted.lastUpdated;
  const elapsedSeconds = Math.floor(elapsedMs / 1000);

  // If the timer was running, subtract elapsed time
  if (persisted.state === 'running') {
    const newTimeRemaining = Math.max(0, persisted.timeRemaining - elapsedSeconds);

    // Timer expired during reload - calculate post-transition state
    if (newTimeRemaining === 0) {
      const nextPhase: PomodoroPhase = persisted.phase === 'work' ? 'break' : 'work';
      const nextDuration = nextPhase === 'work' ? WORK_DURATION : BREAK_DURATION;
      return {
        phase: nextPhase,
        state: 'running',
        timeRemaining: nextDuration,
        wasTransitioned: true  // Flag to skip notification/sound
      };
    }

    return {
      phase: persisted.phase,
      state: persisted.state,
      timeRemaining: newTimeRemaining,
      wasTransitioned: false
    };
  }

  // If paused or idle, preserve the exact time
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

  // Persist state to sessionStorage whenever it changes
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

  // Timer effect - only decrements time
  useEffect(() => {
    if (timerState.state !== 'running') return;

    const interval = setInterval(() => {
      dispatch({ type: 'TICK' });
    }, 1000);

    return () => clearInterval(interval);
  }, [timerState.state]);

  // Phase transition effect - handles transitions when timer reaches zero
  useEffect(() => {
    if (timerState.state !== 'running') return;
    if (timerState.timeRemaining !== 0) return;

    // Only play sound and show notification if NOT a restored transition
    if (!timerState.wasTransitioned) {
      // Play sound based on completed phase
      playSound(timerState.phase === 'work' ? workCompleteAudio : breakCompleteAudio);

      // Show notification
      setShowNotification(true);

      // Auto-dismiss notification after delay
      if (notificationTimeoutRef.current) {
        clearTimeout(notificationTimeoutRef.current);
      }
      notificationTimeoutRef.current = window.setTimeout(() => {
        setShowNotification(false);
        notificationTimeoutRef.current = null;
      }, NOTIFICATION_DISMISS_DELAY);

      // Transition to next phase
      dispatch({ type: 'TRANSITION_PHASE' });
    } else {
      // Clear the flag without transitioning (already transitioned during reload)
      dispatch({ type: 'CLEAR_TRANSITION_FLAG' });
    }
  }, [timerState.timeRemaining, timerState.state]);

  // Cleanup timeout on unmount
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
