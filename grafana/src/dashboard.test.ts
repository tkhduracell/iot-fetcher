import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildDashboard } from './dashboard.ts';

interface GridPos { x: number; y: number; h: number; w: number; }
interface Panel { type: string; title?: string; gridPos: GridPos; collapsed?: boolean; panels?: Panel[]; }

test('each panel y value falls within its row bounds', () => {
  const dashboard = buildDashboard() as { panels: Panel[] };
  const panels = dashboard.panels;

  const rows = panels
    .filter(p => p.type === 'row')
    .map(p => ({ title: p.title ?? '', y: p.gridPos.y }));

  const rowRanges = rows.map((row, i) => ({
    title: row.title,
    minY: row.y + 1,
    maxY: rows[i + 1] ? rows[i + 1].y - 1 : Infinity,
  }));

  let currentRowIndex = -1;

  for (const panel of panels) {
    if (panel.type === 'row') {
      currentRowIndex = rows.findIndex(r => r.y === panel.gridPos.y);

      if (panel.collapsed && panel.panels) {
        const range = rowRanges[currentRowIndex];
        for (const sub of panel.panels) {
          assert.ok(
            sub.gridPos.y >= range.minY && sub.gridPos.y <= range.maxY,
            `Panel "${sub.title}" in collapsed row "${range.title}" has y=${sub.gridPos.y}, expected [${range.minY}, ${range.maxY}]`
          );
        }
      }
      continue;
    }

    if (currentRowIndex === -1) continue;
    const range = rowRanges[currentRowIndex];
    assert.ok(
      panel.gridPos.y >= range.minY && panel.gridPos.y <= range.maxY,
      `Panel "${panel.title}" in row "${range.title}" has y=${panel.gridPos.y}, expected [${range.minY}, ${range.maxY}]`
    );
  }
});
