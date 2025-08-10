import React from 'react';
import { HashRouter, Routes, Route, useParams, useNavigate } from 'react-router';
import LatestValue from './components/LatestValue';
import HealthBadge from './components/HealthBadge';
import RefreshBadge from './components/RefreshBadge';
import useAutoReload from './hooks/useAutoReload';
import LatestValueFullscreen from './components/LatestValueFullscreen';
import { values } from './values';
import EnergyPriceBar from './components/EnergyPriceBar';
import { Config, ConfigRow } from './types';


// Helper to get fullscreen state from router params
function useFullscreenParams(values: Config) {
  const params = useParams();
  const navigate = useNavigate();
  const rowIdx = params.row ? parseInt(params.row, 10) : null;
  const colIdx = params.col ? parseInt(params.col, 10) : null;
  const fullscreenProps = (rowIdx !== null && colIdx !== null && values[rowIdx]?.[colIdx]) ? values[rowIdx][colIdx] : null;

  const openFullscreen = (row: number, col: number) => {
    navigate(`/fullscreen/${row}/${col}`);
  };
  const closeFullscreen = () => {
    navigate('/');
  };
  return { fullscreenProps, openFullscreen, closeFullscreen };
}

// Renders a single row of LatestValue cards
const Row: React.FC<{row: ConfigRow; rowIdx: number; onOpen: (row: number, col: number) => void;}> = ({ row, rowIdx, onOpen }) => (
  <div className="flex flex-row gap-1 min-h-[13.8vh]">
    {row.map((item, colIdx) => (
      <div className="flex-1 cursor-pointer" key={`${item.measurement}-${item.field}`} onClick={() => onOpen(rowIdx, colIdx)}>
        <LatestValue {...item} />
      </div>
    ))}
  </div>
);

// Renders the full grid of LatestValue cards
const Grid: React.FC<{values: Config; onOpen: (row: number, col: number) => void;}> = ({ values, onOpen }) => (
  <>{values.map((row, rowIdx) => (
    <Row row={row} rowIdx={rowIdx} onOpen={onOpen} key={rowIdx} />
  ))}</>
);

const AppContent: React.FC = () => {
    useAutoReload();
    const { fullscreenProps, openFullscreen, closeFullscreen } = useFullscreenParams(values);

    return (
        <div className="min-h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 transition-colors duration-300 relative p-1">
            {/* Top right badges */}
            <div className="flex items-center gap-2 mx-1 my-2">
                <h1 className="text-2xl font-semibold tracking-tight">🏡 Irisgatan 16</h1>
                <div className='flex flex-grow-1 gap-1 justify-end'>
                  <RefreshBadge />
                  <HealthBadge />
                </div>
            </div>
            <div className="w-full py-0 flex flex-col gap-1.5">
                <Grid values={values} onOpen={openFullscreen} />
                <EnergyPriceBar />
            </div>
            { fullscreenProps && <LatestValueFullscreen 
                open={!!fullscreenProps}
                onClose={closeFullscreen} 
                {...fullscreenProps} /> }
        </div>
    );
};

const App: React.FC = () => (
  <HashRouter>
    <Routes>
      <Route path="/" element={<AppContent />} />
      <Route path="/fullscreen/:row/:col" element={<AppContent />} />
    </Routes>
  </HashRouter>
);

export default App;