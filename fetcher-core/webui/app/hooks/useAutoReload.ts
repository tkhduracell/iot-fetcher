import { useEffect } from 'react';
import { pageReloadInterval } from '../lib/globals';

export default function useAutoReload(intervalMs: number = pageReloadInterval) {
    useEffect(() => {
        const timeout = setTimeout(() => {
            window.location.reload();
        }, intervalMs);
        return () => clearTimeout(timeout);
    }, [intervalMs]);
}
