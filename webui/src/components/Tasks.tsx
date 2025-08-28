import React from 'react';
import useTasksQuery, {Task} from '../hooks/useTasksQuery';


const Wrapper = (props: { children: React.ReactNode }) => {
  return (
    <div className="px-1.5 py-1 rounded-md bg-gray-100 dark:bg-gray-800 shadow-sm ring-1 ring-gray-200 dark:ring-gray-700 flex flex-col gap-1 flex-1">
      {props.children}
    </div>
  );
}

const getStatusColor = (status: string): string => {
  switch (status) {
    case 'completed':
      return 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200';
    case 'in_progress':
      return 'bg-yellow-100 dark:bg-yellow-900 text-yellow-800 dark:text-yellow-200';
    case 'pending':
      return 'bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200';
    default:
      return 'bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200';
  }
};

const getPriorityColor = (priority: string): string => {
  switch (priority) {
    case 'high':
      return 'bg-red-500 dark:bg-red-600';
    case 'medium':
      return 'bg-yellow-500 dark:bg-yellow-600';
    case 'low':
      return 'bg-green-500 dark:bg-green-600';
    default:
      return 'bg-gray-500 dark:bg-gray-600';
  }
};

const TaskItem: React.FC<{ task: Task }> = ({ task }) => (
  <div className="flex items-center gap-2 p-2 rounded bg-white dark:bg-gray-700 shadow-sm">
    <div className={`w-2 h-2 rounded-full ${getPriorityColor(task.priority)}`} />
    <div className="flex-1 min-w-0">
      <div className="text-xs font-medium text-gray-900 dark:text-gray-100 truncate">
        {task.title}
      </div>
    </div>
    <div className={`px-1.5 py-0.5 rounded text-xs font-medium ${getStatusColor(task.status)}`}>
      {task.status.replace('_', ' ')}
    </div>
  </div>
);

const Tasks: React.FC = () => {
  const { tasks, initialLoading, error } = useTasksQuery();

  if (initialLoading) {
    return (
      <Wrapper>
        <div className="text-sm text-gray-600 dark:text-gray-400 p-4 text-center">
          Laddar uppgifter...
        </div>
      </Wrapper>
    );
  }

  if (error) {
    return (
      <Wrapper>
        <div className="text-sm text-red-600 dark:text-red-400 p-4 text-center">
          Fel vid laddning: {error.message}
        </div>
      </Wrapper>
    );
  }

  return (
    <Wrapper>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          📋 Uppgifter
        </h3>
        <div className="text-xs text-gray-500 dark:text-gray-400">
          {tasks.filter(t => t.status !== 'completed').length} kvar
        </div>
      </div>
      <div className="grid grid-cols-2 gap-1.5 flex-1 overflow-y-auto">
        {tasks.map((task) => (
          <TaskItem key={task.id} task={task} />
        ))}
      </div>
    </Wrapper>
  );
};

export default Tasks;