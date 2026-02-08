# Roborock Zone/Room Discovery Findings

## Summary

Successfully discovered how to retrieve room names like "Kitchen", "Bedroom", etc. from Roborock devices.

## The Solution

Room names are stored in **`home_data.rooms`** from the cloud API, and matched to device segments via `iot_id`.

### Data Flow

1. **Cloud API** (`home_data.rooms`): Contains room names and IDs
   ```python
   [
     HomeDataRoom(id=1234567, name='Kitchen'),
     HomeDataRoom(id=2345678, name='Office'),
     HomeDataRoom(id=3456789, name='Living Room'),
     HomeDataRoom(id=4567890, name='Bedroom'),
     ...
   ]
   ```

2. **Device Segments** (`local_client.get_room_mapping()`): Contains segment IDs and IoT IDs
   ```python
   [
     RoomMapping(segment_id=16, iot_id='2345678'),
     RoomMapping(segment_id=17, iot_id='4567890'),
     ...
   ]
   ```

3. **Mapping**: Match `room_mapping.iot_id` with `home_data_room.id` to get the name

### Implementation

The updated `/roborock/zones` endpoint now:

1. Builds a `room_name_map` from `home_data.rooms` (id → name)
2. Iterates through all available maps (First Floor, Second Floor, etc.)
3. Loads each map and gets its segments
4. Matches segment `iot_id` to room name
5. Returns zones with actual room names

### Example Response

```json
[
  {
    "zone_id": "17",
    "zone_name": "Bedroom",
    "segment_id": 17,
    "iot_id": "1234567",
    "map_name": "Floor 2",
    "map_flag": 2,
    "device_id": "abcd1234EXAMPLE5678wxyz",
    "device_name": "Roborock S6 MaxV",
    "device_product_id": "exampleProductId123"
  },
  {
    "zone_id": "16",
    "zone_name": "Living Room",
    "segment_id": 16,
    "iot_id": "2345678",
    "map_name": "Floor 2",
    "map_flag": 2,
    "device_id": "abcd1234EXAMPLE5678wxyz",
    "device_name": "Roborock S6 MaxV",
    "device_product_id": "exampleProductId123"
  }
]
```

## Room Configuration Example

Example of how rooms are configured:

- **1234567** → Bedroom
- **2345678** → Living Room
- **3456789** → Kitchen
- **4567890** → Office
- **5678901** → Bathroom

## Maps

Example multi-map configuration:
- **Map 0**: First Floor
- **Map 1**: Second Floor
- **Map 2**: Basement

## Key Learnings

1. **Room names are in the cloud API**, not the device API
2. **Segments are map-specific** - each map can have different segments
3. **Must iterate all maps** to get all available zones
4. **Local connection required** for getting segments (MQTT can also work)
5. **Map loading takes ~1 second** - must wait after `load_multi_map()`

## API Endpoints

### GET `/roborock/zones`

Returns all available zones/rooms across all maps with their actual names.

**Response Example:**
```json
[
  {
    "zone_id": "17",
    "zone_name": "Bedroom",
    "segment_id": 17,
    "iot_id": "1234567",
    "map_name": "First Floor",
    "map_flag": 0,
    "device_id": "abcd1234EXAMPLE5678wxyz",
    "device_name": "Roborock S6 MaxV",
    "device_product_id": "exampleProductId123"
  }
]
```

### POST `/roborock/<device_id>/<map_id>/<zone_id>/clean`

Starts cleaning a specific zone/segment on a specific map.

**Parameters:**
- `device_id`: Device DUID (e.g., `abcd1234EXAMPLE5678wxyz`)
- `map_id`: Map flag (0=First Floor, 1=Second Floor, 2=Basement)
- `zone_id`: Segment ID to clean, or "all" for full cleaning

**Example Requests:**
```bash
# Clean Bedroom (segment 17) on First Floor
POST /roborock/abcd1234EXAMPLE5678wxyz/0/17/clean

# Full clean on Second Floor
POST /roborock/abcd1234EXAMPLE5678wxyz/1/all/clean
```

**Response Example:**
```json
{
  "success": true,
  "message": "Started segment 17 cleaning on First Floor",
  "device_id": "abcd1234EXAMPLE5678wxyz",
  "map_id": "0",
  "map_name": "First Floor",
  "zone_id": "17",
  "result": "ok"
}
```

**Implementation Details:**
1. Loads the specified map using `local_client.load_multi_map()`
2. Waits 1.5 seconds for map to load
3. Sends `app_segment_clean` command with segment ID
4. Cleans with repeat=1 (single pass)

## Updated Files

- `routes/roborock.py` - Complete implementation with:
  - `get_roborock_zones()` - Returns zones with room names
  - `start_roborock_clean()` - Loads map and starts cleaning

## Environment Requirements

```bash
ROBOROCK_USERNAME=your_email@example.com
ROBOROCK_PASSWORD=your_password
```

## Rate Limiting

Note: The Roborock API has rate limiting. If you see "request too frequency" errors, wait a few minutes before retrying.

## Investigation Process

The room names were discovered through extensive testing:

1. **Initial Discovery**: Found segments via `get_room_mapping()` but only got numeric IDs
2. **Local vs MQTT**: Confirmed both methods return identical `RoomMapping` objects
3. **Map Iteration**: Tested loading different maps across multiple floors
4. **Cloud API Deep Dive**: Found `home_data.rooms` contains the actual room names!
5. **Mapping Solution**: Matched `segment.iot_id` with `room.id` to get names

### Key Files Created During Investigation:
- 8 test scripts (all cleaned up after completion)
- `ROBOROCK_FINDINGS.md` - This documentation

### Time Investment:
- Multiple test scripts to explore API
- Network isolation troubleshooting (port 58867)
- Rate limit handling (code 9002)
- Final solution: Matching IoT IDs between segments and rooms

## Success Criteria Met

✅ Room names retrieved (Kitchen, Bedroom, Office, etc.)
✅ All maps iterated across multiple floors
✅ Map loading before cleaning implemented
✅ Clean endpoint updated with map selection
✅ No JSON payload required for cleaning
✅ Test scripts cleaned up
✅ Documentation complete
