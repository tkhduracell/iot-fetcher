import { useEffect } from 'react';
import { pageReloadInterval } from '../globals';
/**
 * Reloads the page every `intervalMs` milliseconds (default: 1 hour).
 */
export default function useAutoReload(intervalMs: number = pageReloadInterval) {
    useEffect(() => {
        const timeout = setTimeout(() => {
            window.location.reload();
        }, intervalMs);
        return () => clearTimeout(timeout);
    }, [intervalMs]);
}
