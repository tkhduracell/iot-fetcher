import * as alerting from '@grafana/grafana-foundation-sdk/alerting';
import { poolpumpInaktiv } from './poolpump-inaktiv.ts';
import { poolpumpVarvtal } from './poolpump-varvtal.ts';
import { energiPerFas } from './energi-per-fas.ts';
import { ngenicLuftfuktighet } from './ngenic-luftfuktighet.ts';

const FOLDER_UID_FALLBACK = 'beveqmuomx5hcd';

/**
 * buildAlerts groups all alert rules by their target folder + ruleGroup so
 * each (folder, group) pair maps to exactly one provisioning PUT. Rules
 * inside a group share an evaluation cadence — see GROUP_INTERVAL.
 */
export function buildAlerts(folderUID = FOLDER_UID_FALLBACK): alerting.RuleGroup[] {
  const rules = [poolpumpInaktiv(), poolpumpVarvtal(), energiPerFas(), ngenicLuftfuktighet()];

  // Stamp the resolved folder UID onto each rule. The Foundation SDK
  // requires it on Rule (it's part of the persisted shape), even though
  // the rule-group provisioning endpoint also takes the folder in its URL.
  for (const r of rules) r.folderUID(folderUID);

  // Group rules by (folderUID, ruleGroup). Today everything lives in one
  // folder + group; the loop is here so adding a new group is data-only.
  const byGroup = new Map<string, alerting.RuleGroup>();
  for (const r of rules) {
    const built = r.build();
    const key = `${built.folderUID}::${built.ruleGroup}`;
    let group = byGroup.get(key);
    if (!group) {
      group = new alerting.RuleGroupBuilder(built.ruleGroup)
        .folderUid(built.folderUID)
        .interval(60) // 60s eval cadence — matches Grafana's UI default.
        .build();
      group.rules = [];
      byGroup.set(key, group);
    }
    group.rules!.push(built);
  }
  return [...byGroup.values()];
}
