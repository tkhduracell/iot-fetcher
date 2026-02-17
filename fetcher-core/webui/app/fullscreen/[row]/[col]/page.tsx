'use client';

import React from 'react';
import { useParams, useRouter } from 'next/navigation';
import LatestValueFullscreen from '../../../components/LatestValueFullscreen';
import { values } from '../../../lib/values';

export default function FullscreenPage() {
  const params = useParams();
  const router = useRouter();

  const rowIdx = params.row ? parseInt(params.row as string, 10) : null;
  const colIdx = params.col ? parseInt(params.col as string, 10) : null;

  const fullscreenProps = (rowIdx !== null && colIdx !== null && values[rowIdx]?.[colIdx])
    ? values[rowIdx][colIdx]
    : null;

  const closeFullscreen = () => {
    router.push('/');
  };

  if (!fullscreenProps) {
    return (
      <div className="min-h-screen bg-white dark:bg-gray-900 flex items-center justify-center">
        <div className="text-gray-600 dark:text-gray-300 text-2xl">Metric not found</div>
      </div>
    );
  }

  return (
    <LatestValueFullscreen
      open={true}
      onClose={closeFullscreen}
      {...fullscreenProps}
    />
  );
}
