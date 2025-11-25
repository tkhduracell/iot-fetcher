import { useState, useEffect, useCallback, useRef } from 'react';

export type PomodoroPhase = 'work' | 'break';
export type PomodoroState = 'idle' | 'running' | 'paused';

// Durations in seconds
const WORK_DURATION = 25 * 60; // 25 minutes
const BREAK_DURATION = 5 * 60; // 5 minutes
const NOTIFICATION_DISMISS_DELAY = 5000; // 5 seconds
const SESSION_STORAGE_KEY = 'pomodoro-timer-state';

// Session storage persistence interface
interface PersistedPomodoroState {
  phase: PomodoroPhase;
  state: PomodoroState;
  timeRemaining: number;
  savedAt: number; // timestamp in ms
}

function saveState(phase: PomodoroPhase, state: PomodoroState, timeRemaining: number): void {
  const persistedState: PersistedPomodoroState = {
    phase,
    state,
    timeRemaining,
    savedAt: Date.now(),
  };
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(persistedState));
  } catch (err) {
    console.error('Failed to save Pomodoro state to session storage:', err);
  }
}

function loadState(): { phase: PomodoroPhase; state: PomodoroState; timeRemaining: number } | null {
  try {
    const stored = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!stored) return null;

    const persistedState: PersistedPomodoroState = JSON.parse(stored);
    const elapsedSeconds = Math.floor((Date.now() - persistedState.savedAt) / 1000);

    // If timer was running, subtract elapsed time
    let adjustedTimeRemaining = persistedState.timeRemaining;
    if (persistedState.state === 'running') {
      adjustedTimeRemaining = Math.max(0, persistedState.timeRemaining - elapsedSeconds);
    }

    return {
      phase: persistedState.phase,
      state: persistedState.state,
      timeRemaining: adjustedTimeRemaining,
    };
  } catch (err) {
    console.error('Failed to load Pomodoro state from session storage:', err);
    return null;
  }
}

function clearPersistedState(): void {
  try {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch (err) {
    console.error('Failed to clear Pomodoro state from session storage:', err);
  }
}

// Sound URLs (external assets - could be moved to local /public directory for better reliability)
const WORK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/99591aa7-ec53-40fc-b6c4-f768f1a73881/d0794f38-b1c8-43f9-9ca3-432ad18a7710/preview.m4a';
const BREAK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/dc07756a-54e3-4851-acc4-9fce465ee255/54252202-2ca1-45b3-963c-a3747597aac5/preview.m4a';

// Pre-loaded audio instances for better performance and memory efficiency
const workCompleteAudio = new Audio(WORK_COMPLETE_SOUND);
const breakCompleteAudio = new Audio(BREAK_COMPLETE_SOUND);

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
  audio.currentTime = 0;
  audio.play().catch(err => console.error('Failed to play sound:', err));
}

export function usePomodoroTimer(): [PomodoroTimerState, PomodoroTimerActions] {
  // Initialize state from session storage if available
  const initialState = loadState();
  const [phase, setPhase] = useState<PomodoroPhase>(initialState?.phase ?? 'work');
  const [state, setState] = useState<PomodoroState>(initialState?.state ?? 'idle');
  const [timeRemaining, setTimeRemaining] = useState(initialState?.timeRemaining ?? WORK_DURATION);
  const [showNotification, setShowNotification] = useState(false);
  const notificationTimeoutRef = useRef<number | null>(null);

  const start = useCallback(() => {
    setState('running');
    speakOnSonos("let's go");
  }, []);

  const pause = useCallback(() => {
    setState('paused');
    speakOnSonos("let's pause");
  }, []);

  const reset = useCallback(() => {
    setState('idle');
    setPhase('work');
    setTimeRemaining(WORK_DURATION);
    setShowNotification(false);
    clearPersistedState();
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

  // Helper function to transition to the next phase
  const transitionToNextPhase = useCallback(() => {
    const nextPhase: PomodoroPhase = phase === 'work' ? 'break' : 'work';
    const nextDuration = nextPhase === 'work' ? WORK_DURATION : BREAK_DURATION;
    
    // Play sound based on completed phase
    playSound(phase === 'work' ? workCompleteAudio : breakCompleteAudio);
    
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
    
    // Transition to next phase and reset timer
    setPhase(nextPhase);
    setTimeRemaining(nextDuration);
  }, [phase]);

  const skipToNextPhase = useCallback(() => {
    if (state === 'idle') return;
    transitionToNextPhase();
    setState('running');
  }, [state, transitionToNextPhase]);

  // Timer effect - only decrements time
  useEffect(() => {
    if (state !== 'running') return;

    const interval = setInterval(() => {
      setTimeRemaining((prev) => (prev > 0 ? prev - 1 : prev));
    }, 1000);

    return () => clearInterval(interval);
  }, [state]);

  // Phase transition effect - handles transitions when timer reaches zero
  useEffect(() => {
    if (state !== 'running') return;
    if (timeRemaining === 0) {
      transitionToNextPhase();
    }
  }, [timeRemaining, state, transitionToNextPhase]);

  // Persist state to session storage when state changes
  useEffect(() => {
    // Only persist non-idle states
    if (state !== 'idle') {
      saveState(phase, state, timeRemaining);
    }
  }, [phase, state, timeRemaining]);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (notificationTimeoutRef.current) {
        clearTimeout(notificationTimeoutRef.current);
      }
    };
  }, []);

  return [
    { phase, state, timeRemaining, showNotification },
    { start, pause, reset, dismissNotification, skipToNextPhase }
  ];
}

export function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}
