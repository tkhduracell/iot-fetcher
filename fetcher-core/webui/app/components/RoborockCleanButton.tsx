'use client';

import React, { useEffect, useState } from 'react';
import RoborockCleanDialog from './RoborockCleanDialog';
import { useRoborockTargets } from '../hooks/useRoborockTargets';

const RoborockCleanButton: React.FC = () => {
  const [open, setOpen] = useState(false);
  const { targets, isLoading, refetch } = useRoborockTargets();

  // Sync with URL hash, matching SpeakersButton.
  useEffect(() => {
    const check = () => setOpen(window.location.hash === '#clean');
    check();
    window.addEventListener('hashchange', check);
    return () => window.removeEventListener('hashchange', check);
  }, []);

  const handleClose = () => {
    history.pushState(null, '', window.location.pathname + window.location.search);
    setOpen(false);
  };

  const toggle = () => {
    if (open) {
      handleClose();
    } else {
      refetch();
      history.pushState(null, '', '#clean');
      setOpen(true);
    }
  };

  const isEmpty = targets.floors.length === 0 && targets.rooms.length === 0;

  // Hide entirely when Home Assistant is unconfigured or unreachable.
  if (isLoading || isEmpty) return null;

  return (
    <>
      <button
        onClick={toggle}
        title="Start Roborock cleaning"
        className="px-4 py-1.5 rounded-full shadow text-sm font-semibold cursor-pointer transition-colors duration-200 bg-green-600 hover:bg-green-700 text-white flex items-center gap-1.5"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M10 2a8 8 0 100 16 8 8 0 000-16zm0 3a5 5 0 110 10 5 5 0 010-10zm0 3a2 2 0 100 4 2 2 0 000-4z" clipRule="evenodd" />
        </svg>
        Clean
      </button>
      {open && <RoborockCleanDialog targets={targets} onClose={handleClose} />}
    </>
  );
};

export default RoborockCleanButton;
