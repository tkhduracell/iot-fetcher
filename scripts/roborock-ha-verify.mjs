#!/usr/bin/env node
// Prints the labelled Roborock automations exactly as the web-ui will see them.
const URL_BASE = (process.env.HOMEASSISTANT_URL || '').replace(/\/$/, '');
const TOKEN = process.env.HOMEASSISTANT_TOKEN;

const template =
  `{% set ns = namespace(floors=[], rooms=[]) %}` +
  `{% for e in label_entities('roborock_floor') %}` +
  `{% set ns.floors = ns.floors + [{'entity_id': e, 'name': state_attr(e, 'friendly_name')}] %}` +
  `{% endfor %}` +
  `{% for e in label_entities('roborock_room') %}` +
  `{% set ns.rooms = ns.rooms + [{'entity_id': e, 'name': state_attr(e, 'friendly_name')}] %}` +
  `{% endfor %}` +
  `{{ {'floors': ns.floors, 'rooms': ns.rooms} | tojson }}`;

const resp = await fetch(`${URL_BASE}/api/template`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ template }),
});
const parsed = JSON.parse(await resp.text());
console.log(`floors (${parsed.floors.length}):`, parsed.floors.map(f => f.name).join(', '));
console.log(`rooms  (${parsed.rooms.length}):`, parsed.rooms.map(r => r.name).join(', '));
