import { useEffect, useRef } from 'react';

type UseAutoScrollOptions = {
  enabled?: boolean;
  scrollDuration?: number; // Duration for one direction in milliseconds
  minViewportHeight?: number; // Only enable on smaller screens
};

const useAutoScroll = ({ 
  enabled = true, 
  scrollDuration = 15 * 60 * 1000, // 15 minutes
  minViewportHeight = 800 // Enable on screens smaller than 800px
}: UseAutoScrollOptions = {}) => {
  const intervalRef = useRef<NodeJS.Timeout>();
  const startTimeRef = useRef<number>();
  const isScrollingDownRef = useRef<boolean>(true);

  const getScrollableHeight = () => {
    return document.documentElement.scrollHeight - window.innerHeight;
  };

  const updateScroll = () => {
    const now = Date.now();
    
    if (!startTimeRef.current) {
      startTimeRef.current = now;
    }

    const elapsed = now - startTimeRef.current;
    const progress = Math.min(elapsed / scrollDuration, 1);

    const maxScroll = getScrollableHeight();
    
    let targetScroll: number;
    if (isScrollingDownRef.current) {
      // Scrolling down: 0 to maxScroll
      targetScroll = progress * maxScroll;
    } else {
      // Scrolling up: maxScroll to 0
      targetScroll = maxScroll - (progress * maxScroll);
    }

    window.scrollTo({
      top: targetScroll,
      behavior: 'auto'
    });

    if (progress >= 1) {
      // Direction completed, switch direction and reset timer
      isScrollingDownRef.current = !isScrollingDownRef.current;
      startTimeRef.current = now;
    }
  };

  useEffect(() => {
    // Only enable on smaller viewports
    if (!enabled || window.innerHeight >= minViewportHeight) {
      return;
    }

    const startScrolling = () => {
      // Clear any existing interval first
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      
      const maxScroll = getScrollableHeight();
      if (maxScroll <= 0) return; // No scrolling needed
      
      const intervalMs = scrollDuration / maxScroll;
      
      intervalRef.current = setInterval(updateScroll, Math.max(intervalMs, 16));
    };

    // Start the scrolling
    startScrolling();

    // Handle window resize to recalculate if auto-scroll should be enabled
    const handleResize = () => {
      if (window.innerHeight >= minViewportHeight && intervalRef.current) {
        clearInterval(intervalRef.current);
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      window.removeEventListener('resize', handleResize);
    };
  }, [enabled, scrollDuration, minViewportHeight]);

  // Pause scrolling on user interaction
  useEffect(() => {
    let resumeTimeoutRef: NodeJS.Timeout;

    const startScrolling = () => {
      // Clear any existing interval first
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      
      const maxScroll = getScrollableHeight();
      if (maxScroll <= 0) return;
      
      const intervalMs = scrollDuration / maxScroll;
      intervalRef.current = setInterval(updateScroll, Math.max(intervalMs, 16)); // Minimum 16ms (60fps)
    };

    const handleUserInteraction = () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
      
      // Clear any existing resume timeout
      if (resumeTimeoutRef) {
        clearTimeout(resumeTimeoutRef);
      }
      
      // Resume after 30 seconds of inactivity
      resumeTimeoutRef = setTimeout(() => {
        if (window.innerHeight < minViewportHeight) {
          startTimeRef.current = undefined;
          startScrolling();
        }
      }, 30000);
    };

    const events = ['mousedown', 'touchstart', 'keydown', 'wheel'];
    events.forEach(event => {
      document.addEventListener(event, handleUserInteraction, { passive: true });
    });

    return () => {
      events.forEach(event => {
        document.removeEventListener(event, handleUserInteraction);
      });
      if (resumeTimeoutRef) {
        clearTimeout(resumeTimeoutRef);
      }
    };
  }, [minViewportHeight, scrollDuration]);
};

export default useAutoScroll;