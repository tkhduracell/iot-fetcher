# Roborock Clean Dialog (web-ui) — Design

Date: 2026-07-25
Branch: `cc/roborock-button-web-ui-c6c31d`

## Problem

The dashboard needs a working "Städa" button that opens a dialog listing the rooms
the Roborock can clean, with whole-floor actions on top.

An earlier attempt on this branch added `RoborockCleanButton` + `useRoborockZones`,
which call a Roborock-cloud sidecar at `http://localhost:8081`. **That sidecar does
not exist anywhere in this repo** — no service, no Dockerfile, no compose entry. The
button is dead code and the two `ROBOROCK_*.md` files document a service that was
never built.

This design replaces it by driving the Home Assistant automations that already work.

## Current state in Home Assistant

HA runs on the rpi5 at `http://192.168.68.87:8123` (`network_mode: host`).

Maps, from `select.roborock_s6_maxv_selected_map.options`:
`Floor 1`, `Floor 0`, `Uterummet`, `Annexet`

Existing `Städa *` automations:

| Automation | Map | Action |
|---|---|---|
| `Städa Vån1` | Floor 1 | `vacuum.start` (whole floor) |
| `Städa Uterum` | Uterummet | `vacuum.start` (whole map) |
| `Städa Köket` | Floor 1 | `app_segment_clean` `[21]` |
| `Städa Kontoret` | Floor 1 | `app_segment_clean` `[16]` |
| `Städa Vardagsrummet` | Floor 1 | `app_segment_clean` `[19, 20]` |
| `Städa Matrummet` | Floor 1 | `app_segment_clean` `[22]` |
| `Städa Lobbyn` | Floor 1 | `app_segment_clean` `[23]` |
| `Städa` | *(none)* | full clean on whatever map is loaded |

The floor-level pattern the feature needs therefore already exists. Floor entries are
missing only for `Floor 0` and `Annexet`.

Room automations also fire an ElevenLabs TTS announcement to `media_player.kok`.
**This is left as-is** — triggering from the dashboard will announce, and that is fine.

## Decisions

1. **Whole-floor cleans go through HA automations**, not direct service calls from the
   web-ui. Keeps the cleaning logic in HA where it can be edited in the UI and reused
   from voice/dashboards, and matches the existing pattern.
2. **Discovery is driven by HA labels**, read over the HA template API. Survives
   renames; adding a room later means labelling it in HA with no code change.
3. **Tapping starts immediately and the dialog stays open** showing live vacuum state,
   with a "Return to dock" action. No confirmation step.

## HA changes

### New automations

Both copy the `Städa Vån1` shape exactly (`roborock.get_maps` → `select.select_option`
→ `vacuum.start`), with no `app_segment_clean`:

- `Städa Vån0` → option `Floor 0`
- `Städa Annexet` → option `Annexet`

Created via `POST /api/config/automation/config/<id>`, which writes `automations.yaml`
and reloads.

### New labels

| Label | Automations |
|---|---|
| `roborock_floor` | Städa Vån1, Städa Vån0, Städa Uterum, Städa Annexet |
| `roborock_room` | Städa Köket, Städa Kontoret, Städa Vardagsrummet, Städa Matrummet, Städa Lobbyn |

`automation.stada` stays unlabelled — it has no map and would be ambiguous in the dialog.

Labels are created with the WebSocket API (`config/label_registry/create`) and assigned
with `config/entity_registry/update`; neither is exposed over REST. The plan must verify
these command names against the running HA version before relying on them.

## Web-ui changes

The web-ui is a Next.js app served by the `iot-fetcher` container. All HA calls happen in
server-side route handlers so the long-lived token never reaches the browser.

New variables in `fetcher-core/webui/.env` (already loaded by `docker-compose.local.yml`):

```
HOMEASSISTANT_URL=http://192.168.68.87:8123
HOMEASSISTANT_TOKEN=<long-lived token>
```

### API routes

| Route | Behaviour |
|---|---|
| `GET /api/roborock/targets` | One `POST /api/template` call using `label_entities('roborock_floor')` / `label_entities('roborock_room')`, returning `{floors, rooms}` of `{entity_id, name}`. |
| `GET /api/roborock/status` | Reads `sensor.roborock_s6_maxv_status`, `_battery`, `_current_room`, `_vacuum_error`. |
| `POST /api/roborock/trigger` | Body `{entity_id}`. **Resolves the labelled set first and rejects any entity_id not in it**, then calls `automation.trigger`. |
| `POST /api/roborock/dock` | `vacuum.return_to_base` on `vacuum.roborock_s6_maxv`. |

The allow-list check on `trigger` is a hard requirement: without it the browser could
trigger any automation in HA.

If `HOMEASSISTANT_URL`/`TOKEN` are unset, or HA is unreachable, `targets` returns an empty
set and the button hides itself, matching how the current component hides on error.

### Components

- `RoborockCleanButton.tsx` — rewritten as a button plus `#clean` hash-synced modal,
  mirroring `SpeakersButton` → `SonosZoneManager`.
- `RoborockCleanDialog.tsx` — floors as large touch targets on top, rooms below, and a
  status footer showing state, battery, current room and any error, plus "Return to dock".
- `useRoborockTargets.ts` — fetches targets on mount (so the button knows whether to
  render at all) and refetches on each dialog open.
- `useRoborockStatus.ts` — polls `/api/roborock/status` every 5s **only while the dialog
  is open**.

Display names strip the leading `Städa ` from the automation's friendly name. Ordering is
alphabetical within each group; reorder by renaming in HA.

Targets are fetched from HA rather than cached in `localStorage` (the old hook cached for
an hour), so a newly labelled room appears without clearing browser storage.

### Deletions

- `app/api/roborock/zones/route.ts`
- `app/api/roborock/[deviceId]/[mapId]/[zoneId]/clean/route.ts`
- `app/hooks/useRoborockZones.ts`
- `RoborockZone` in `app/lib/types.ts`
- `fetcher-core/webui/ROBOROCK_FINDINGS.md`
- `fetcher-core/webui/ROBOROCK_UI_INTEGRATION.md`

`ROBOROCK_USERNAME` / `ROBOROCK_PASSWORD` become unused by the web-ui. The python
fetcher's own copies are left untouched.

## Testing

Local, pre-merge:

- Unit-test the `trigger` allow-list: an entity_id outside the labelled set is rejected
  with 403 and no HA call is made.
- Unit-test target parsing from a recorded HA template response, including the empty case.
- Run the dev server against the real HA on the LAN; confirm the dialog lists 4 floors and
  5 rooms with names stripped of `Städa `.
- Trigger `Städa Kontoret` and confirm the vacuum leaves the dock and the footer flips from
  `charging` to a cleaning state.
- Confirm polling stops when the dialog closes.

Post-merge, on rpi5: deploy with both compose files, load the dashboard, run one room and
one floor clean, and confirm `Städa Vån0` and `Städa Annexet` appear and select the right
map.

## Out of scope

Map image thumbnails (`image.roborock_s6_maxv_*` exist), scheduling, clean history,
per-room fan speed, and cancelling a clean mid-run (only "Return to dock" is offered).
