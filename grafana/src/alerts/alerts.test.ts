import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildAlerts } from './index.ts';

interface Rule {
  uid?: string;
  title: string;
  condition: string;
  ruleGroup: string;
  folderUID: string;
  data: Array<{ refId?: string }>;
}

interface RuleGroup {
  folderUid?: string;
  title?: string;
  interval?: number;
  rules?: Rule[];
}

test('alerts build into a single Irisgatan group', () => {
  const groups = buildAlerts() as RuleGroup[];
  assert.equal(groups.length, 1);
  assert.equal(groups[0].title, 'Irisgatan');
  assert.equal(groups[0].rules?.length, 4);
});

test('every rule has the four required identity fields', () => {
  const groups = buildAlerts() as RuleGroup[];
  for (const rule of groups[0].rules ?? []) {
    assert.ok(rule.uid, `${rule.title}: missing uid`);
    assert.ok(rule.title, `rule missing title`);
    assert.ok(rule.condition, `${rule.title}: missing condition`);
    assert.ok(rule.folderUID, `${rule.title}: missing folderUID`);
  }
});

test('every rule condition refers to an existing data refId', () => {
  const groups = buildAlerts() as RuleGroup[];
  for (const rule of groups[0].rules ?? []) {
    const refIds = new Set(rule.data.map((d) => d.refId).filter(Boolean));
    assert.ok(refIds.has(rule.condition), `${rule.title}: condition "${rule.condition}" not in refIds ${[...refIds].join(',')}`);
  }
});

test('rule UIDs are unique', () => {
  const groups = buildAlerts() as RuleGroup[];
  const uids = (groups[0].rules ?? []).map((r) => r.uid);
  assert.equal(new Set(uids).size, uids.length, `duplicate UIDs in ${uids.join(',')}`);
});
