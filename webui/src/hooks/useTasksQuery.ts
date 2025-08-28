import { useState, useEffect } from 'react';
import { queryReloadInterval, queryJitterInterval } from '../globals';

type Task = {
  id: number;
  title: string;
  status: 'pending' | 'completed' | 'in_progress';
  priority: 'high' | 'medium' | 'low';
};

type UseTasksQueryResult = {
  tasks: Task[];
  loading: boolean;
  initialLoading: boolean;
  error: Error | null;
};

const useTasksQuery = (reloadInterval: number = queryReloadInterval): UseTasksQueryResult => {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    let intervalId: NodeJS.Timeout | null = null;
    let timerId: NodeJS.Timeout | null = null;

    const fetchTasks = async () => {
      if (cancelled) return;
      
      setLoading(true);
      setError(null);
      
      try {
        const response = await fetch('/home/tasks');
        
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (!cancelled) {
          setTasks(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err as Error);
          setTasks([]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          setInitialLoading(false);
        }
      }
    };

    const jitter = Math.random() * queryJitterInterval;
    timerId = setTimeout(() => {
      if (cancelled) return;
      fetchTasks();
      intervalId = setInterval(fetchTasks, reloadInterval);
    }, jitter);

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      if (timerId) clearTimeout(timerId);
    };
  }, [reloadInterval]);

  return { tasks, loading, initialLoading, error };
};

export default useTasksQuery;