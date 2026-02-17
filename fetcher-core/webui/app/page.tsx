'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import LatestValue from './components/LatestValue';
import HealthBadge from './components/HealthBadge';
import RefreshBadge from './components/RefreshBadge';
import RoborockCleanButton from './components/RoborockCleanButton';
import PomodoroButton from './components/PomodoroButton';
import useAutoReload from './hooks/useAutoReload';
import EnergyPriceBar from './components/EnergyPriceBar';
import SonosWidget from './components/SonosWidget';
import { values } from './lib/values';
import { Config, ConfigRow } from './lib/types';

const Row: React.FC<{row: ConfigRow; rowIdx: number; onOpen: (row: number, col: number) => void;}> = ({ row, rowIdx, onOpen }) => (
  <div className="flex flex-row gap-1 min-h-[13.8vh]">
    {row.map((item, colIdx) => (
      <div className="flex-1 cursor-pointer" key={`${item.measurement}-${item.field}-${item.filter ? JSON.stringify(item.filter) : ''}`} onClick={() => onOpen(rowIdx, colIdx)}>
        <LatestValue {...item} />
      </div>
    ))}
  </div>
);

const Grid: React.FC<{values: Config; onOpen: (row: number, col: number) => void;}> = ({ values, onOpen }) => (
  <>{values.map((row, rowIdx) => (
    <Row row={row} rowIdx={rowIdx} onOpen={onOpen} key={rowIdx} />
  ))}</>
);

export default function DashboardPage() {
  useAutoReload();
  const router = useRouter();

  const openFullscreen = (row: number, col: number) => {
    router.push(`/fullscreen/${row}/${col}`);
  };

  return (
    <div className="min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 transition-colors duration-300 relative p-1">
      <div className="flex items-center gap-2 mx-1 my-2">
        <h1 className="text-2xl font-semibold tracking-tight">🏡 Irisgatan 16</h1>
        <div className='flex flex-grow-1 gap-1 justify-end'>
          <PomodoroButton />
          <RoborockCleanButton />
          <RefreshBadge />
          <HealthBadge />
        </div>
      </div>
      <div className="w-full py-0 flex flex-col gap-1.5">
        <Grid values={values} onOpen={openFullscreen} />
        <EnergyPriceBar />
        <SonosWidget />
      </div>
    </div>
  );
}
