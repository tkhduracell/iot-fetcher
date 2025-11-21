# Roborock UI Integration

## Overview

Added a "Clean" button to the React UI that allows users to start Roborock vacuum cleaning by selecting rooms from a dropdown.

## Files Added/Modified

### New Files Created:

1. **`src/hooks/useRoborockZones.ts`**
   - React Query hook for fetching available zones
   - Fetches from `GET /roborock/zones`
   - Caches results for 1 hour

2. **`src/components/RoborockCleanButton.tsx`**
   - Main UI component
   - Dropdown showing zones grouped by map
   - Handles POST requests to start cleaning

### Modified Files:

1. **`src/types.ts`**
   - Added `RoborockZone` type definition

2. **`src/App.tsx`**
   - Imported and added `RoborockCleanButton` to header
   - Positioned between title and Reload/Health badges

## Features

### Clean Button
- Green button labeled "Clean"
- Located in top-right header next to Reload/Health badges
- Disabled state while cleaning operation is in progress

### Dropdown Menu
- Opens below the Clean button
- Closes when clicking outside
- Groups zones by map name (e.g., "First Floor", "Second Floor")
- Shows human-readable room names (Kitchen, Bedroom, etc.)

### Zone Selection
- Each zone shows:
  - Room name (e.g., "Kitchen")
  - Segment ID as subtitle
  - Hover effect with arrow icon
- Clicking a zone:
  - Closes dropdown
  - POSTs to `/roborock/<device_id>/<map_flag>/<zone_id>/clean`
  - Shows "Starting..." state during request

### Error Handling
- Loading state while fetching zones
- Error message if zones fail to load
- Empty state if no zones configured
- Alert notification if clean operation fails

## UI/UX Details

### Styling
- Consistent with existing badge components
- Dark mode support
- Smooth transitions and hover effects
- Responsive dropdown with max height and scrolling

### Interaction Flow
1. User clicks "Clean" button
2. Dropdown opens showing all available zones
3. User selects a room (e.g., "Kitchen")
4. Button shows "Starting..." state
5. POST request sent to backend
6. Dropdown closes
7. Success/error feedback provided

## API Integration

### GET `/roborock/zones`
```typescript
// Response format
type RoborockZone = {
  zone_id: string;
  zone_name: string;        // Human-readable: "Kitchen"
  segment_id: number;
  iot_id: string;
  map_name: string;         // "First Floor"
  map_flag: number;         // 0, 1, 2...
  device_id: string;
  device_name: string;
  device_product_id: string;
};
```

### POST `/roborock/<device_id>/<map_id>/<zone_id>/clean`
- Loads the correct map
- Starts cleaning the selected segment
- No JSON body required

## Example Usage

1. Click "Clean" button
2. See zones grouped by floor:
   ```
   First Floor
     ├─ Kitchen (Segment 17)
     ├─ Living Room (Segment 16)
     └─ Office (Segment 18)

   Second Floor
     ├─ Bedroom (Segment 20)
     └─ Bathroom (Segment 21)
   ```
3. Click "Kitchen"
4. Vacuum starts cleaning the kitchen

## Testing

To test the integration:

1. Start the React dev server: `npm run dev`
2. Ensure backend is running with Roborock credentials
3. Click the green "Clean" button
4. Verify zones load correctly
5. Select a zone and verify cleaning starts

## Future Enhancements

Potential improvements:
- Toast notifications for success/failure
- Cleaning progress indicator
- Schedule cleaning feature
- Clean history view
- Cancel cleaning operation
