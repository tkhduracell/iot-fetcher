import { useEffect } from 'react';

/**
 * Reloads the page every `intervalMs` milliseconds (default: 1 hour).
 */
export default function useAutoReload(intervalMs: number = 3600000) {
    useEffect(() => {
        const timeout = setTimeout(() => {
            window.location.reload();
        }, intervalMs);
        return () => clearTimeout(timeout);
    }, [intervalMs]);
}
