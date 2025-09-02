// Remove the values array, only export types
export type Config = readonly ConfigRow[];
export type ConfigRow = readonly ConfigValue[];
export type ConfigValue = {
  measurement: string;
  field: string;
  filter?: Record<string, any>;
  title: string;
  unit: string;
  window?: "5m" | "60m" ;
  range?: "-15m" | "-1h" ;
  decimals?: number;
  reload?: number;
};

// Sonos types based on API response
export type SonosZone = {
  uuid: string;
  coordinator: SonosCoordinator;
  members: SonosMember[];
};

export type SonosCoordinator = {
  uuid: string;
  state: SonosState;
  roomName: string;
  coordinator: string;
  groupState: {
    volume: number;
    mute: boolean;
  };
};

export type SonosMember = {
  uuid: string;
  state: SonosState;
  roomName: string;
  coordinator: string;
  groupState: {
    volume: number;
    mute: boolean;
  };
};

export type SonosState = {
  volume: number;
  mute: boolean;
  equalizer: {
    bass: number;
    treble: number;
    loudness: boolean;
    speechEnhancement?: boolean;
    nightMode?: boolean;
  };
  currentTrack: SonosTrack;
  nextTrack: SonosTrack;
  trackNo: number;
  elapsedTime: number;
  elapsedTimeFormatted: string;
  playbackState: "PLAYING" | "PAUSED_PLAYBACK" | "STOPPED";
  playMode: {
    repeat: string;
    shuffle: boolean;
    crossfade: boolean;
  };
};

export type SonosTrack = {
  artist: string;
  title: string;
  album: string;
  albumArtUri: string;
  duration: number;
  uri: string;
  trackUri?: string;
  type?: string;
  stationName?: string;
  absoluteAlbumArtUri?: string;
};
