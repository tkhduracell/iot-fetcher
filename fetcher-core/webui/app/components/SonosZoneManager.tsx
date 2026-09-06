'use client';

import React, { useState, useRef, useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import useSonosQuery from '../hooks/useSonosQuery';
import { SonosZone, SonosMember } from '../lib/types';

type DragState = {
  speakerRoom: string;
  speakerUuid: string;
  sourceZoneUuid: string;
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
  chipRect: DOMRect;
} | null;

type PendingMove = {
  speakerUuid: string;
  speakerRoom: string;
  targetZoneUuid: string;
};

const UNGROUPED_ZONE_ID = '__ungrouped__';

const SonosZoneManager: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { result: zones, refetch } = useSonosQuery();
  const [dragState, setDragState] = useState<DragState>(null);
  const [hoverZoneId, setHoverZoneId] = useState<string | null>(null);
  const [pendingMoves, setPendingMoves] = useState<PendingMove[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [volumeEditing, setVolumeEditing] = useState<string | null>(null);
  const [localVolumes, setLocalVolumes] = useState<Map<string, number>>(new Map());
  const [announceOpen, setAnnounceOpen] = useState(false);
  const [announceRoom, setAnnounceRoom] = useState('');
  const [announceVolume, setAnnounceVolume] = useState(40);
  const [sayText, setSayText] = useState('');
  const [saySending, setSaySending] = useState(false);
  const volumeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const zoneRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (announceOpen) {
          closeAnnounce();
        } else if (volumeEditing) {
          setVolumeEditing(null);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose, volumeEditing, announceOpen]);

  // Build optimistic zone view: real zones + pending moves applied
  const optimisticZones = useCallback((): (SonosZone & { pendingSpeakers?: PendingMove[] })[] => {
    if (pendingMoves.length === 0) return zones;

    const pendingUuids = new Set(pendingMoves.map(m => m.speakerUuid));
    // Remove pending speakers from their current zones
    const adjusted = zones.map(zone => ({
      ...zone,
      members: zone.members.filter(m => !pendingUuids.has(m.uuid)),
      pendingSpeakers: pendingMoves.filter(m => m.targetZoneUuid === zone.uuid),
    }));
    return adjusted;
  }, [zones, pendingMoves]);

  const findHoveredZone = useCallback((x: number, y: number): string | null => {
    for (const [zoneId, el] of zoneRefs.current.entries()) {
      const rect = el.getBoundingClientRect();
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
        return zoneId;
      }
    }
    return null;
  }, []);

  const handlePointerDown = useCallback((
    e: React.PointerEvent,
    speakerRoom: string,
    speakerUuid: string,
    sourceZoneUuid: string
  ) => {
    if (volumeEditing) return; // Don't drag while volume slider is open
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    const chip = (e.currentTarget as HTMLElement);
    setDragState({
      speakerRoom,
      speakerUuid,
      sourceZoneUuid,
      startX: e.clientX,
      startY: e.clientY,
      currentX: e.clientX,
      currentY: e.clientY,
      chipRect: chip.getBoundingClientRect(),
    });
  }, [volumeEditing]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragState) return;
    setDragState(prev => prev ? { ...prev, currentX: e.clientX, currentY: e.clientY } : null);
    setHoverZoneId(findHoveredZone(e.clientX, e.clientY));
  }, [dragState, findHoveredZone]);

  const performJoin = async (speakerRoom: string, targetRoom: string): Promise<boolean> => {
    // Try direct join first
    const resp = await fetch(`/sonos/${encodeURIComponent(speakerRoom)}/join/${encodeURIComponent(targetRoom)}`);
    if (resp.ok) return true;

    // Fallback: pause → join → play
    await fetch(`/sonos/${encodeURIComponent(targetRoom)}/pause`);
    await new Promise(r => setTimeout(r, 500));
    const resp2 = await fetch(`/sonos/${encodeURIComponent(speakerRoom)}/join/${encodeURIComponent(targetRoom)}`);
    await new Promise(r => setTimeout(r, 500));
    await fetch(`/sonos/${encodeURIComponent(targetRoom)}/play`);
    return resp2.ok;
  };

  const handlePointerUp = useCallback(async () => {
    if (!dragState || !hoverZoneId || hoverZoneId === dragState.sourceZoneUuid) {
      setDragState(null);
      setHoverZoneId(null);
      return;
    }

    const { speakerRoom, speakerUuid } = dragState;
    const targetZone = hoverZoneId === UNGROUPED_ZONE_ID
      ? null
      : zones.find(z => z.uuid === hoverZoneId);
    const targetRoom = targetZone?.coordinator.roomName;
    const targetZoneUuid = hoverZoneId;

    // Optimistic: add pending move immediately
    const move: PendingMove = { speakerUuid, speakerRoom, targetZoneUuid };
    setPendingMoves(prev => [...prev, move]);
    setDragState(null);
    setHoverZoneId(null);
    setError(null);

    try {
      if (targetZoneUuid === UNGROUPED_ZONE_ID) {
        const resp = await fetch(`/sonos/${encodeURIComponent(speakerRoom)}/leave`);
        if (!resp.ok) throw new Error(`Sonos returned ${resp.status}`);
      } else if (targetRoom) {
        const ok = await performJoin(speakerRoom, targetRoom);
        if (!ok) throw new Error('Join failed after retry');
      }
      await new Promise(r => setTimeout(r, 500));
      await refetch();
    } catch (err) {
      setError(`Failed to move ${speakerRoom}: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setPendingMoves(prev => prev.filter(m => m.speakerUuid !== speakerUuid));
    }
  }, [dragState, hoverZoneId, zones, refetch]);

  const handleVolumeChange = useCallback((roomName: string, uuid: string, volume: number) => {
    setVolumeEditing(uuid);
    setLocalVolumes(prev => new Map(prev).set(uuid, volume));
    if (volumeTimeout.current) clearTimeout(volumeTimeout.current);
    volumeTimeout.current = setTimeout(async () => {
      await fetch(`/sonos/${encodeURIComponent(roomName)}/volume/${volume}`);
      await refetch();
      setLocalVolumes(prev => { const m = new Map(prev); m.delete(uuid); return m; });
    }, 300);
  }, [refetch]);

  const registerZoneRef = useCallback((zoneId: string, el: HTMLDivElement | null) => {
    if (el) {
      zoneRefs.current.set(zoneId, el);
    } else {
      zoneRefs.current.delete(zoneId);
    }
  }, []);

  const getAllSpeakers = (zone: SonosZone): SonosMember[] => {
    const seen = new Set<string>();
    const speakers: SonosMember[] = [];
    for (const member of zone.members) {
      if (!seen.has(member.uuid)) {
        seen.add(member.uuid);
        speakers.push(member);
      }
    }
    return speakers;
  };

  const displayZones = optimisticZones();
  const hasPending = pendingMoves.length > 0;

  // Every speaker in the house, so an announcement can target a grouped room
  // too — not just the unassigned ones the old per-chip Say button covered.
  const allSpeakers = zones
    .flatMap(z => z.members)
    .filter((m, i, arr) => arr.findIndex(x => x.uuid === m.uuid) === i)
    .sort((a, b) => a.roomName.localeCompare(b.roomName, 'sv'));

  const openAnnounce = () => {
    setAnnounceRoom(prev => (prev && allSpeakers.some(sp => sp.roomName === prev))
      ? prev
      : (allSpeakers[0]?.roomName ?? ''));
    setSayText('');
    setError(null);
    setAnnounceOpen(true);
  };

  const closeAnnounce = () => {
    setAnnounceOpen(false);
    setSayText('');
    setError(null);
  };

  const handleSay = async () => {
    const room = announceRoom;
    if (!room || !sayText.trim()) return;
    setSaySending(true);
    try {
      // node-sonos-http-api: /{room}/say/{text}/{volume} — an all-digit second
      // parameter is taken as the announcement volume (it restores the
      // previous volume afterwards), so no separate volume call is needed.
      const resp = await fetch(
        `/sonos/${encodeURIComponent(room)}/say/${encodeURIComponent(sayText.trim())}/${announceVolume}`
      );
      if (!resp.ok) throw new Error(`Announce failed (${resp.status})`);
      setSayText('');
      setAnnounceOpen(false);
      setError(null);
    } catch (err) {
      console.error('TTS error:', err);
      setError(err instanceof Error ? err.message : 'Announce failed');
    } finally {
      setSaySending(false);
    }
  };

  const renderSpeakerChip = (speaker: SonosMember, zoneUuid: string, isPending: boolean, isZonePlaying?: boolean) => {
    const isDragging = dragState?.speakerUuid === speaker.uuid;
    const displayVolume = localVolumes.get(speaker.uuid) ?? speaker.state.volume;
    const isEditing = volumeEditing === speaker.uuid;
    // Amplitude scales with volume, duration is fixed (8 beats @ 128 BPM = 3.75s)
    const v = displayVolume / 100;
    const pulseScale = isZonePlaying ? 1 + v * 0.06 : 1;
    const pulseSpread = isZonePlaying ? 3 + v * 12 : 0;
    const pulseOpacity = isZonePlaying ? 0.15 + v * 0.35 : 0;

    return (
      <div
        key={speaker.uuid}
        onPointerDown={(e) => !isPending && !isEditing && handlePointerDown(e, speaker.roomName, speaker.uuid, zoneUuid)}
        className={`bg-gray-700 hover:bg-gray-650 rounded-lg px-4 py-3.5 select-none transition-all touch-none ${
          isDragging ? 'opacity-30' : ''
        } ${isPending ? 'opacity-60 animate-pulse' : isEditing ? '' : 'cursor-grab active:cursor-grabbing'} ${
          isZonePlaying && !isDragging && !isPending ? 'speaker-playing' : ''
        }`}
        style={isZonePlaying && !isDragging && !isPending ? {
          '--pulse-scale': pulseScale,
          '--pulse-spread': `${pulseSpread}px`,
          '--pulse-opacity': pulseOpacity,
        } as React.CSSProperties : undefined}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isPending && (
              <svg className="w-3.5 h-3.5 text-indigo-400 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            )}
            <span className="text-base text-gray-200 font-medium">{speaker.roomName}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <svg className="w-4 h-4 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M9.383 3.076A1 1 0 0110 4v12a1 1 0 01-1.617.787L4.7 13.04H2a1 1 0 01-1-1V8a1 1 0 011-1h2.7l3.683-3.747a1 1 0 011.617.787z" clipRule="evenodd" />
            </svg>
            <span className="text-sm text-gray-400 tabular-nums">{displayVolume}</span>
          </div>
        </div>
        {/* Volume slider — styled as a thin bar */}
        <div className="mt-1.5 relative h-8 rounded-full">
          <div className="absolute inset-0 bg-gray-600 rounded-full overflow-hidden pointer-events-none">
            <div
              className="h-full bg-indigo-500 rounded-full transition-all"
              style={{ width: `${displayVolume}%` }}
            />
          </div>
          <input
            type="range"
            min={0}
            max={100}
            value={displayVolume}
            onChange={(e) => {
              e.stopPropagation();
              handleVolumeChange(speaker.roomName, speaker.uuid, Number(e.target.value));
            }}
            onPointerDown={(e) => e.stopPropagation()}
            className="absolute inset-0 w-full opacity-0 cursor-pointer"
          />
        </div>
      </div>
    );
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-gray-900/95 backdrop-blur-sm flex flex-col"
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onClick={() => volumeEditing && setVolumeEditing(null)}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-xl font-semibold text-white">Sonos Speakers</h2>
          <p className="text-xs text-gray-400">Dra och släpp högtalare för att flytta dem mellan grupper.</p>
        </div>
        <div className="flex items-center gap-3">
          {error && (
            <span className="text-sm text-red-400">{error}</span>
          )}
          {hasPending && (
            <span className="text-sm text-gray-400 animate-pulse">Updating...</span>
          )}
          <button
            onClick={onClose}
            className="w-10 h-10 rounded-full bg-gray-700 hover:bg-gray-600 text-white flex items-center justify-center transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Main content: sidebar + zone columns */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left sidebar: unassigned speakers */}
        <div
          ref={(el) => registerZoneRef(UNGROUPED_ZONE_ID, el)}
          data-zone-id={UNGROUPED_ZONE_ID}
          className={`w-[240px] flex-shrink-0 border-r border-gray-700 p-4 overflow-y-auto flex flex-col gap-3 transition-all ${
            hoverZoneId === UNGROUPED_ZONE_ID && dragState
              ? 'bg-red-900/20'
              : ''
          }`}
        >
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">Unassigned Speakers</h3>
          {(() => {
            const soloZones = displayZones.filter(z => z.members.length === 1 && z.coordinator.state.playbackState !== 'PLAYING');
            const pendingInSidebar = pendingMoves.filter(m => m.targetZoneUuid === UNGROUPED_ZONE_ID);
            if (soloZones.length === 0 && pendingInSidebar.length === 0) {
              return (
                <div className="flex-1 flex flex-col items-center justify-center gap-2 text-gray-500">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span className="text-xs text-center">All speakers assigned</span>
                </div>
              );
            }
            return (
              <>
                {soloZones.map(zone => {
                  const speaker = zone.members[0];
                  return renderSpeakerChip(speaker, zone.uuid, false, false);
                })}
                {pendingInSidebar.map(move => {
                  const originalSpeaker = zones.flatMap(z => z.members).find(m => m.uuid === move.speakerUuid);
                  if (!originalSpeaker) return null;
                  return renderSpeakerChip(originalSpeaker, UNGROUPED_ZONE_ID, true);
                })}
              </>
            );
          })()}

          {/* Announce to any speaker — replaces the per-speaker Say buttons */}
          <button
            onClick={openAnnounce}
            disabled={allSpeakers.length === 0}
            className="mt-auto w-full flex items-center justify-center gap-2 bg-green-600 hover:bg-green-500 disabled:opacity-40 disabled:hover:bg-green-600 text-white font-semibold rounded-lg px-4 py-3 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-green-400"
          >
            <svg className="w-5 h-5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M18 3a1 1 0 00-1.447-.894L8.763 6H5a3 3 0 000 6h.28l1.771 5.316A1 1 0 008 18h1a1 1 0 001-1v-4.382l6.553 3.276A1 1 0 0018 15V3z" />
            </svg>
            Announce
          </button>
        </div>

        {/* Zone columns */}
        <div className="flex-1 overflow-y-auto p-6">
          <div className="flex flex-wrap gap-4">
            {displayZones
              .filter(zone => zone.members.length > 1 || zone.coordinator.state.playbackState === 'PLAYING')
              .map(zone => {
              const speakers = getAllSpeakers(zone);
              const pending = ('pendingSpeakers' in zone ? (zone as any).pendingSpeakers : []) as PendingMove[];
              const { coordinator } = zone;
              const { currentTrack, playbackState } = coordinator.state;
              const isPlaying = playbackState === 'PLAYING';
              const isHover = hoverZoneId === zone.uuid && dragState && dragState.sourceZoneUuid !== zone.uuid;

              return (
                <div
                  key={zone.uuid}
                  ref={(el) => registerZoneRef(zone.uuid, el)}
                  data-zone-id={zone.uuid}
                  className={`w-[calc(50%-0.5rem)] min-w-[280px] bg-gray-800 rounded-xl p-4 flex flex-col gap-3 transition-all ${
                    isHover ? 'ring-2 ring-blue-500 bg-gray-750' : ''
                  }`}
                >
                  {/* Zone header with album art */}
                  <div className="flex gap-3">
                    <div className="flex-1 min-w-0 flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
                          isPlaying ? 'bg-green-400' : 'bg-gray-500'
                        }`} />
                        <h3 className="text-sm font-semibold text-white truncate">
                          {coordinator.roomName}
                        </h3>
                        {(speakers.length + pending.length) > 1 && (
                          <span className="text-xs text-gray-400 bg-gray-700 rounded-full px-2 py-0.5">
                            {speakers.length + pending.length}
                          </span>
                        )}
                      </div>
                      {isPlaying && currentTrack.title && (
                        <div className="text-xs text-gray-400 truncate px-1">
                          {currentTrack.artist ? `${currentTrack.artist} — ${currentTrack.title}` : currentTrack.title}
                        </div>
                      )}
                    </div>
                    {isPlaying && currentTrack.absoluteAlbumArtUri && (
                      <img
                        src={currentTrack.absoluteAlbumArtUri}
                        alt=""
                        className="w-12 h-12 rounded-lg object-cover flex-shrink-0"
                      />
                    )}
                  </div>

                  {/* Speaker chips */}
                  <div className="flex flex-col gap-2">
                    {speakers.map(speaker => renderSpeakerChip(speaker, zone.uuid, false, isPlaying))}
                    {pending.map(move => {
                      const originalSpeaker = zones.flatMap(z => z.members).find(m => m.uuid === move.speakerUuid);
                      if (!originalSpeaker) return null;
                      return renderSpeakerChip(originalSpeaker, zone.uuid, true, isPlaying);
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Announce dialog — centered, above the speaker panel */}
      {announceOpen && createPortal(
        <div
          className="fixed inset-0 z-[110] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={closeAnnounce}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="announce-title"
            className="w-full max-w-md bg-gray-800 rounded-2xl shadow-2xl border border-gray-700 p-6 flex flex-col gap-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 id="announce-title" className="text-lg font-semibold text-white">Announce</h3>
              <button
                onClick={closeAnnounce}
                aria-label="Close"
                className="w-8 h-8 rounded-full bg-gray-700 hover:bg-gray-600 text-white flex items-center justify-center transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Speaker</span>
              <select
                value={announceRoom}
                onChange={(e) => setAnnounceRoom(e.target.value)}
                className="w-full bg-gray-700 text-white rounded-lg px-3 py-2.5 outline-none focus:ring-2 focus:ring-green-500 cursor-pointer"
              >
                {allSpeakers.map(sp => (
                  <option key={sp.uuid} value={sp.roomName}>{sp.roomName}</option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Volume</span>
                <span className="text-sm text-gray-300 tabular-nums">{announceVolume}</span>
              </div>
              <div className="relative h-8 rounded-full">
                <div className="absolute inset-0 bg-gray-600 rounded-full overflow-hidden pointer-events-none">
                  <div className="h-full bg-green-500 rounded-full transition-all" style={{ width: `${announceVolume}%` }} />
                </div>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={announceVolume}
                  onChange={(e) => setAnnounceVolume(Number(e.target.value))}
                  aria-label="Announcement volume"
                  className="absolute inset-0 w-full opacity-0 cursor-pointer"
                />
              </div>
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Message</span>
              <textarea
                value={sayText}
                onChange={(e) => setSayText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSay(); }
                }}
                rows={3}
                autoFocus
                placeholder="Middagen är klar!"
                className="w-full resize-none bg-gray-700 text-white rounded-lg px-3 py-2.5 outline-none focus:ring-2 focus:ring-green-500 placeholder:text-gray-500"
              />
            </label>

            {error && <span className="text-sm text-red-400">{error}</span>}

            <button
              onClick={handleSay}
              disabled={saySending || !sayText.trim() || !announceRoom}
              className="w-full bg-green-600 hover:bg-green-500 disabled:opacity-40 disabled:hover:bg-green-600 text-white font-semibold rounded-lg px-4 py-3 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-green-400"
            >
              {saySending ? 'Sending...' : 'Send'}
            </button>
          </div>
        </div>,
        document.body
      )}

      {/* Drag overlay */}
      {dragState && createPortal(
        <div
          className="fixed pointer-events-none z-[100]"
          style={{
            left: dragState.chipRect.left + (dragState.currentX - dragState.startX),
            top: dragState.chipRect.top + (dragState.currentY - dragState.startY),
            width: dragState.chipRect.width,
          }}
        >
          <div className="bg-indigo-600 rounded-lg px-4 py-3.5 shadow-2xl shadow-indigo-500/30" style={{ width: dragState.chipRect.width, height: dragState.chipRect.height }}>
            <span className="text-base text-white font-medium">{dragState.speakerRoom}</span>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

export default SonosZoneManager;
