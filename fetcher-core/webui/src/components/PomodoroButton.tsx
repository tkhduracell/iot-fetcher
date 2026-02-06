import React, { useRef, useEffect, useState, useCallback } from 'react';
import { usePomodoroTimer, formatTime } from '../hooks/usePomodoroTimer';

const LONG_PRESS_DURATION = 800; // milliseconds

const PomodoroButton: React.FC = () => {
  const [{ phase, state, timeRemaining, showNotification }, { start, pause, reset, dismissNotification, skipToNextPhase }] = usePomodoroTimer();
  const notificationRef = useRef<HTMLDivElement>(null);
  const longPressTimerRef = useRef<number | null>(null);
  const [longPressProgress, setLongPressProgress] = useState(0);
  const longPressStartRef = useRef<number | null>(null);
  const animationFrameRef = useRef<number | null>(null);

  // Focus notification when it appears for accessibility
  useEffect(() => {
    if (showNotification && notificationRef.current) {
      notificationRef.current.focus();
    }
  }, [showNotification]);

  // Cleanup animation frame and timer on unmount
  useEffect(() => {
    return () => {
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current);
      }
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  const updateProgress = useCallback(() => {
    if (longPressStartRef.current === null) return;
    
    const elapsed = Date.now() - longPressStartRef.current;
    const progress = Math.min(elapsed / LONG_PRESS_DURATION, 1);
    setLongPressProgress(progress);
    
    if (progress < 1) {
      animationFrameRef.current = requestAnimationFrame(updateProgress);
    }
  }, []);

  const cancelLongPress = useCallback(() => {
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    longPressStartRef.current = null;
    setLongPressProgress(0);
  }, []);

  const handlePressStart = useCallback(() => {
    // Only enable long press when timer is active (not idle)
    if (state === 'idle') return;
    
    longPressStartRef.current = Date.now();
    setLongPressProgress(0);
    animationFrameRef.current = requestAnimationFrame(updateProgress);
    
    longPressTimerRef.current = window.setTimeout(() => {
      skipToNextPhase();
      cancelLongPress();
    }, LONG_PRESS_DURATION);
  }, [state, skipToNextPhase, cancelLongPress, updateProgress]);

  const handlePressEnd = useCallback(() => {
    cancelLongPress();
  }, [cancelLongPress]);

  const handleClick = () => {
    if (state === 'idle') {
      start();
    } else if (state === 'running') {
      pause();
    } else if (state === 'paused') {
      start();
    }
  };

  const handleReset = (e: React.MouseEvent) => {
    e.stopPropagation();
    reset();
  };

  const handleNotificationKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') {
      dismissNotification();
    }
  };

  const getAriaLabel = () => {
    if (state === 'idle') {
      return 'Start Pomodoro timer';
    }
    const phaseLabel = phase === 'work' ? 'work phase' : 'break phase';
    const timeLabel = formatTime(timeRemaining);
    const stateLabel = state === 'paused' ? 'paused' : 'running';
    return `Pomodoro timer, ${phaseLabel}, ${timeLabel} remaining, ${stateLabel}`;
  };

  // Button styling based on state and phase
  const getButtonStyle = () => {
    const baseStyle = 'px-3 py-1 rounded-full shadow text-sm font-semibold cursor-pointer transition-colors duration-200';
    
    if (state === 'idle') {
      return `${baseStyle} bg-purple-600 hover:bg-purple-700 text-white`;
    }
    
    if (phase === 'work') {
      return state === 'paused'
        ? `${baseStyle} bg-orange-500 hover:bg-orange-600 text-white`
        : `${baseStyle} bg-red-600 hover:bg-red-700 text-white`;
    }
    
    // break phase
    return state === 'paused'
      ? `${baseStyle} bg-teal-500 hover:bg-teal-600 text-white`
      : `${baseStyle} bg-green-600 hover:bg-green-700 text-white`;
  };

  const getButtonText = () => {
    if (state === 'idle') {
      return '🍅 Pomodoro';
    }
    
    const timeStr = formatTime(timeRemaining);
    const phaseIcon = phase === 'work' ? '🍅' : '☕';
    const stateIcon = state === 'paused' ? '⏸' : '';
    
    return `${phaseIcon} ${timeStr} ${stateIcon}`.trim();
  };

  const getNotificationMessage = () => {
    if (phase === 'work') {
      return 'Break time is over! Time to focus. 🍅';
    }
    return 'Great work! Take a break. ☕';
  };

  const getTitle = () => {
    if (state === 'idle') return 'Start Pomodoro';
    if (state === 'running') return 'Pause (hold to skip)';
    return 'Resume (hold to skip)';
  };

  return (
    <div className="relative">
      <div className="flex items-center gap-1">
        <button
          onClick={handleClick}
          onMouseDown={handlePressStart}
          onMouseUp={handlePressEnd}
          onMouseLeave={handlePressEnd}
          onTouchStart={handlePressStart}
          onTouchEnd={handlePressEnd}
          onTouchCancel={handlePressEnd}
          title={getTitle()}
          className={`${getButtonStyle()} relative overflow-hidden`}
          aria-label={getAriaLabel()}
        >
          {/* Long press progress indicator */}
          {longPressProgress > 0 && (
            <span 
              className="absolute inset-0 bg-white/30 origin-left"
              style={{ transform: `scaleX(${longPressProgress})` }}
            />
          )}
          <span className="relative">{getButtonText()}</span>
        </button>
        {state !== 'idle' && (
          <button
            onClick={handleReset}
            title="Reset timer"
            className="bg-gray-500 hover:bg-gray-600 text-white px-2 py-1 rounded-full shadow text-sm font-semibold cursor-pointer"
            aria-label="Reset Pomodoro timer"
          >
            ✕
          </button>
        )}
      </div>
      
      {/* Notification popup */}
      {showNotification && (
        <div 
          ref={notificationRef}
          role="alert"
          tabIndex={0}
          className="absolute top-full right-0 mt-2 z-50 animate-fade-in outline-none"
          onClick={dismissNotification}
          onKeyDown={handleNotificationKeyDown}
        >
          <div className="bg-gray-800 text-white px-4 py-3 rounded-lg shadow-lg min-w-[200px] text-center cursor-pointer hover:bg-gray-700">
            <p className="text-lg font-semibold">{getNotificationMessage()}</p>
            <p className="text-xs text-gray-400 mt-1">Click or press Escape to dismiss</p>
          </div>
        </div>
      )}
    </div>
  );
};

export default PomodoroButton;
