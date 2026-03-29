'use client';

import React, { useState, useEffect } from 'react';
import SonosZoneManager from './SonosZoneManager';

const SpeakersButton: React.FC = () => {
  const [open, setOpen] = useState(false);

  // Sync with URL hash
  useEffect(() => {
    const check = () => setOpen(window.location.hash === '#speakers');
    check();
    window.addEventListener('hashchange', check);
    return () => window.removeEventListener('hashchange', check);
  }, []);

  const toggle = () => {
    if (open) {
      history.pushState(null, '', window.location.pathname + window.location.search);
      setOpen(false);
    } else {
      history.pushState(null, '', '#speakers');
      setOpen(true);
    }
  };

  const handleClose = () => {
    history.pushState(null, '', window.location.pathname + window.location.search);
    setOpen(false);
  };

  return (
    <>
      <button
        onClick={toggle}
        className="px-4 py-1.5 rounded-full shadow text-sm font-semibold cursor-pointer transition-colors duration-200 bg-indigo-600 hover:bg-indigo-700 text-white flex items-center gap-1.5"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M9.383 3.076A1 1 0 0110 4v12a1 1 0 01-1.617.787L4.7 13.04H2a1 1 0 01-1-1V8a1 1 0 011-1h2.7l3.683-3.747a1 1 0 011.617.787zM14.657 2.929a1 1 0 011.414 0A9.972 9.972 0 0119 10a9.972 9.972 0 01-2.929 7.071 1 1 0 11-1.414-1.414A7.971 7.971 0 0017 10c0-2.21-.894-4.208-2.343-5.657a1 1 0 010-1.414zm-2.829 2.828a1 1 0 011.415 0A5.983 5.983 0 0115 10a5.984 5.984 0 01-1.757 4.243 1 1 0 01-1.415-1.415A3.984 3.984 0 0013 10a3.983 3.983 0 00-1.172-2.828 1 1 0 010-1.415z" clipRule="evenodd" />
        </svg>
        Speakers
      </button>
      {open && <SonosZoneManager onClose={handleClose} />}
    </>
  );
};

export default SpeakersButton;
