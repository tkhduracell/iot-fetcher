import { useState, useEffect, useCallback, useRef } from 'react';

export type PomodoroPhase = 'work' | 'break';
export type PomodoroState = 'idle' | 'running' | 'paused';

// Durations in seconds
const WORK_DURATION = 25 * 60; // 25 minutes
const BREAK_DURATION = 5 * 60; // 5 minutes
const NOTIFICATION_DISMISS_DELAY = 5000; // 5 seconds

// Sound URLs (external assets - could be moved to local /public directory for better reliability)
const WORK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/99591aa7-ec53-40fc-b6c4-f768f1a73881/d0794f38-b1c8-43f9-9ca3-432ad18a7710/preview.m4a';
const BREAK_COMPLETE_SOUND = 'https://public-assets.content-platform.envatousercontent.com/dc07756a-54e3-4851-acc4-9fce465ee255/54252202-2ca1-45b3-963c-a3747597aac5/preview.m4a';

// Pre-loaded audio instances for better performance and memory efficiency
const workCompleteAudio = new Audio(WORK_COMPLETE_SOUND);
const breakCompleteAudio = new Audio(BREAK_COMPLETE_SOUND);

// Sonos TTS configuration
const SONOS_SPEAKER = 'Kontor';
const SONOS_VOLUME = 90;

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
  const [phase, setPhase] = useState<PomodoroPhase>('work');
  const [state, setState] = useState<PomodoroState>('idle');
  const [timeRemaining, setTimeRemaining] = useState(WORK_DURATION);
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
