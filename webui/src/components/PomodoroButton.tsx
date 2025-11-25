import React, { useRef, useEffect } from 'react';
import { usePomodoroTimer, formatTime } from '../hooks/usePomodoroTimer';

const PomodoroButton: React.FC = () => {
  const [{ phase, state, timeRemaining, showNotification }, { start, pause, reset, dismissNotification }] = usePomodoroTimer();
  const notificationRef = useRef<HTMLDivElement>(null);

  // Focus notification when it appears for accessibility
  useEffect(() => {
    if (showNotification && notificationRef.current) {
      notificationRef.current.focus();
    }
  }, [showNotification]);

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

  return (
    <div className="relative">
      <div className="flex items-center gap-1">
        <button
          onClick={handleClick}
          title={state === 'idle' ? 'Start Pomodoro' : state === 'running' ? 'Pause' : 'Resume'}
          className={getButtonStyle()}
          aria-label={getAriaLabel()}
        >
          {getButtonText()}
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
