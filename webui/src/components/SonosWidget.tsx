import React from 'react';
import useSonosQuery from '../hooks/useSonosQuery';
import { SonosZone } from '../types';

type SonosZoneCardProps = {
  zone: SonosZone;
  onPlayPause: (roomName: string, action: 'play' | 'pause') => void;
  onNext: (roomName: string) => void;
  onMuteToggle: (zoneUuid: string, roomName: string, action: 'mute' | 'unmute') => void;
  onVolumeChange: (roomName: string, change: number) => void;
};

const SonosZoneCard: React.FC<SonosZoneCardProps> = ({ zone, onPlayPause, onNext, onMuteToggle, onVolumeChange }) => {
  const { coordinator, members } = zone;
  const { roomName, state } = coordinator;
  const { currentTrack, playbackState } = state;
  
  const isPlaying = playbackState === 'PLAYING';
  const isMuted = state.mute;
  const trackInfo = currentTrack.artist && currentTrack.title 
    ? `${currentTrack.artist} - ${currentTrack.title}`
    : currentTrack.title || 'No track info';
  
  // Format room name with additional speakers if multiple in zone
  // Filter out the coordinator to avoid showing the room name twice
  const additionalSpeakers = members
    .filter(m => m.uuid !== coordinator.uuid)
    .map(m => m.roomName);
  const displayRoomName = additionalSpeakers.length > 0
    ? `${roomName} (+${additionalSpeakers.join(', ')})`
    : roomName;

  const handlePlayPause = () => {
    const action = isPlaying ? 'pause' : 'play';
    onPlayPause(roomName, action);
  };

  const handleNext = () => {
    onNext(roomName);
  };

  const handleMuteToggle = () => {
    const action = isMuted ? 'unmute' : 'mute';
    onMuteToggle(zone.uuid, roomName, action);
  };

  const handleVolumeDown = () => {
    onVolumeChange(roomName, -5);
  };

  const handleVolumeUp = () => {
    onVolumeChange(roomName, 5);
  };

  return (
    <div className="flex-1 min-w-[200px] max-w-[calc(50%-0.25rem)] bg-white dark:bg-gray-800 rounded-lg p-3 shadow-sm border border-gray-200 dark:border-gray-700">
      <div className="flex items-center gap-3">
        {currentTrack.absoluteAlbumArtUri && (
          <img 
            src={currentTrack.absoluteAlbumArtUri} 
            alt={currentTrack.album}
            className="w-12 h-12 rounded object-cover flex-shrink-0"
          />
        )}
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm text-gray-900 dark:text-gray-100 mb-1">
            {displayRoomName}
          </div>
          <div className="text-xs text-gray-600 dark:text-gray-400 truncate">
            {trackInfo}
          </div>
        </div>
        <div className="flex gap-1 items-center">
          <button
            onClick={handlePlayPause}
            className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500 hover:bg-blue-600 dark:bg-blue-600 dark:hover:bg-blue-700 text-white flex items-center justify-center transition-colors"
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? (
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path d="M6 4h2v12H6V4zm6 0h2v12h-2V4z"/>
              </svg>
            ) : (
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path d="M6.5 5.5v9l7-4.5-7-4.5z"/>
              </svg>
            )}
          </button>
          
          <button
            onClick={handleNext}
            className="flex-shrink-0 w-10 h-10 rounded-full bg-gray-500 hover:bg-gray-600 dark:bg-gray-600 dark:hover:bg-gray-700 text-white flex items-center justify-center transition-colors"
            title="Next track"
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
              <path d="M4.555 5.168A1 1 0 003 6v8a1 1 0 001.555.832L10 11.202V14a1 1 0 001.555.832l6-4a1 1 0 000-1.664l-6-4A1 1 0 0010 6v2.798L4.555 5.168z"/>
            </svg>
          </button>
          
          <button
            onClick={handleVolumeDown}
            className="flex-shrink-0 w-8 h-8 rounded-full bg-green-500 hover:bg-green-600 dark:bg-green-600 dark:hover:bg-green-700 text-white flex items-center justify-center transition-colors"
            title="Volume Down (-5)"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path d="M5 10a1 1 0 011-1h8a1 1 0 110 2H6a1 1 0 01-1-1z"/>
            </svg>
          </button>

          <button
            onClick={handleMuteToggle}
            className={`flex-shrink-0 w-10 h-10 rounded-full text-white flex items-center justify-center transition-colors ${
              isMuted 
                ? 'bg-red-500 hover:bg-red-600 dark:bg-red-600 dark:hover:bg-red-700' 
                : 'bg-green-500 hover:bg-green-600 dark:bg-green-600 dark:hover:bg-green-700'
            }`}
            title={isMuted ? 'Unmute' : 'Mute'}
          >
            {isMuted ? (
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M9.383 3.076A1 1 0 0110 4v12a1 1 0 01-1.617.787L4.7 13.04H2a1 1 0 01-1-1V8a1 1 0 011-1h2.7l3.683-3.747a1 1 0 011.617.787zM14.657 2.929a1 1 0 011.414 0A9.972 9.972 0 0119 10a9.972 9.972 0 01-2.929 7.071 1 1 0 11-1.414-1.414A7.971 7.971 0 0017 10c0-2.21-.894-4.208-2.343-5.657a1 1 0 010-1.414zm-2.829 2.828a1 1 0 011.415 0A5.983 5.983 0 0115 10a5.984 5.984 0 01-1.757 4.243 1 1 0 01-1.415-1.415A3.984 3.984 0 0013 10a3.983 3.983 0 00-1.172-2.828 1 1 0 010-1.415z" clipRule="evenodd"/>
                <path d="M3 3l14 14" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            ) : (
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M9.383 3.076A1 1 0 0110 4v12a1 1 0 01-1.617.787L4.7 13.04H2a1 1 0 01-1-1V8a1 1 0 011-1h2.7l3.683-3.747a1 1 0 011.617.787zM14.657 2.929a1 1 0 011.414 0A9.972 9.972 0 0119 10a9.972 9.972 0 01-2.929 7.071 1 1 0 11-1.414-1.414A7.971 7.971 0 0017 10c0-2.21-.894-4.208-2.343-5.657a1 1 0 010-1.414zm-2.829 2.828a1 1 0 011.415 0A5.983 5.983 0 0115 10a5.984 5.984 0 01-1.757 4.243 1 1 0 01-1.415-1.415A3.984 3.984 0 0013 10a3.983 3.983 0 00-1.172-2.828 1 1 0 010-1.415z" clipRule="evenodd"/>
              </svg>
            )}
          </button>

          <button
            onClick={handleVolumeUp}
            className="flex-shrink-0 w-8 h-8 rounded-full bg-green-500 hover:bg-green-600 dark:bg-green-600 dark:hover:bg-green-700 text-white flex items-center justify-center transition-colors"
            title="Volume Up (+5)"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10 5a1 1 0 011 1v3h3a1 1 0 110 2h-3v3a1 1 0 11-2 0v-3H6a1 1 0 110-2h3V6a1 1 0 011-1z"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
};

const SonosWidget: React.FC = () => {
  const { initialLoading, error, result, updateZoneMute, unavailable } = useSonosQuery();

  const handlePlayPause = async (roomName: string, action: 'play' | 'pause') => {
    try {
      const response = await fetch(`/sonos/${encodeURIComponent(roomName)}/${action}`);
      if (!response.ok) {
        console.error(`Failed to ${action} ${roomName}`);
      }
    } catch (err) {
      console.error(`Error ${action}ing ${roomName}:`, err);
    }
  };

  const handleNext = async (roomName: string) => {
    try {
      const response = await fetch(`/sonos/${encodeURIComponent(roomName)}/next`);
      if (!response.ok) {
        console.error(`Failed to skip to next track for ${roomName}`);
      }
    } catch (err) {
      console.error(`Error skipping to next track for ${roomName}:`, err);
    }
  };

  const handleMuteToggle = async (zoneUuid: string, roomName: string, action: 'mute' | 'unmute') => {
    // Optimistically update UI first
    const newMutedState = action === 'mute';
    updateZoneMute(zoneUuid, newMutedState);
    
    try {
      const response = await fetch(`/sonos/${encodeURIComponent(roomName)}/${action}`);
      if (!response.ok) {
        // Revert on error
        updateZoneMute(zoneUuid, !newMutedState);
        console.error(`Failed to ${action} ${roomName}`);
      }
    } catch (err) {
      // Revert on error
      updateZoneMute(zoneUuid, !newMutedState);
      console.error(`Error ${action}ing ${roomName}:`, err);
    }
  };

  const handleVolumeChange = async (roomName: string, change: number) => {
    try {
      const changeStr = change > 0 ? `+${change}` : `${change}`;
      const response = await fetch(`/sonos/${encodeURIComponent(roomName)}/volume/${changeStr}`);
      if (!response.ok) {
        console.error(`Failed to change volume by ${change} for ${roomName}`);
      }
    } catch (err) {
      console.error(`Error changing volume by ${change} for ${roomName}:`, err);
    }
  };

  if (unavailable) {
    return null; // Hide the widget entirely when Sonos is not configured
  }

  if (initialLoading && !error) {
    return (
      <div className="w-full flex justify-center">
        <div className="text-sm text-gray-600 dark:text-gray-300">
          Laddar Sonos-zoner...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full flex justify-center">
        <div className="text-sm text-red-600 dark:text-red-400">
          Fel vid laddning av Sonos: {error.message}
        </div>
      </div>
    );
  }

  const playingZones = result.filter(zone => zone.coordinator.state.playbackState === 'PLAYING');

  if (playingZones.length === 0) {
    return (
      <div className="w-full flex justify-center">
        <div className="text-sm text-gray-600 dark:text-gray-300">
          Inga zoner spelar just nu
        </div>
      </div>
    );
  }

  return (
    <div className="w-full flex justify-center">
      <div className="w-full">
        <div className="flex gap-2 flex-wrap justify-center">
          {playingZones.map(zone => (
            <SonosZoneCard 
              key={zone.uuid} 
              zone={zone} 
              onPlayPause={handlePlayPause}
              onNext={handleNext}
              onMuteToggle={handleMuteToggle}
              onVolumeChange={handleVolumeChange}
            />
          ))}
        </div>
      </div>
    </div>
  );
};

export default SonosWidget;