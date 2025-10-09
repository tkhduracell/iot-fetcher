import os
import requests
import logging
from typing import List, Dict, Any, Optional

from influx import write_influx, Point

# Configure module-specific logger
logger = logging.getLogger(__name__)

sonos_host = os.environ.get('SONOS_HOST', '')


def sonos():
    if not sonos_host:
        logger.error("[sonos] SONOS_HOST environment variable not set, ignoring...")
        return
    
    try:
        _sonos()
    except Exception as e:
        logger.exception(f"[sonos] Failed to execute sonos module: {e}")


def _sonos():
    url = f"http://{sonos_host}/zones"
    
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"[sonos] Failed to fetch zones from {url}: {e}")
        return
    
    zones: List[Dict[str, Any]] = response.json()
    points: List[Point] = []
    
    for zone in zones:
        coordinator = zone.get('coordinator', {})
        state = coordinator.get('state', {})
        room_name = coordinator.get('roomName', 'Unknown')
        playback_state = state.get('playbackState', 'STOPPED')
        
        # Only log zones that are playing
        if playback_state != 'PLAYING':
            continue
        
        current_track = state.get('currentTrack', {})
        artist = current_track.get('artist', '')
        title = current_track.get('title', '')
        volume = state.get('volume', 0)
        
        # Format track info as "artist - title"
        if artist and title:
            track_info = f"{artist} - {title}"
        elif title:
            track_info = title
        else:
            track_info = "Unknown"
        
        # Create InfluxDB point
        point = Point("sonos_playback") \
            .tag("room_name", room_name) \
            .tag("playback_state", playback_state) \
            .field("volume", volume) \
            .field("track_info", track_info)
        
        # Add optional fields if available
        if artist:
            point = point.field("artist", artist)
        if title:
            point = point.field("title", title)
        
        points.append(point)
    
    if points:
        write_influx(points)
