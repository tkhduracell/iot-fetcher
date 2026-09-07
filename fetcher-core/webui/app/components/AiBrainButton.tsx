'use client';

import React from 'react';
import Link from 'next/link';

const AiBrainButton: React.FC = () => (
  <Link
    href="/ai-brain"
    title="AI-hjärnan"
    aria-label="AI-hjärnan"
    className="bg-blue-600 hover:bg-blue-700 text-white w-9 h-9 rounded-full shadow cursor-pointer items-center justify-center transition-colors text-lg leading-none"
    style={{ display: 'none' }}
  >
    🧠
  </Link>
);

export default AiBrainButton;
