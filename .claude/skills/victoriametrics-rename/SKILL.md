---
name: victoriametrics-rename
description: Rename a VictoriaMetrics metric by exporting, transforming, and re-importing
allowed-tools: Bash, AskUserQuestion
---

# VictoriaMetrics Rename

Rename a metric in VictoriaMetrics using `scripts/vm-rename.sh`, which handles credential loading automatically.

## Arguments

- `old_name` (required): Current metric name to rename
- `new_name` (required): Desired new metric name

## Steps

1. **Preview the rename** with a dry run:

   ```bash
   ./scripts/vm-rename.sh '<old_name>' '<new_name>' --dry-run
   ```

2. **Show the preview** to the user: report the series count and sample data.

3. **Ask the user** whether to proceed with the rename, and whether to delete the old metric afterward.

4. **Execute the rename** based on user response:

   - Proceed without delete: `./scripts/vm-rename.sh '<old_name>' '<new_name>'`
   - Proceed with delete: `./scripts/vm-rename.sh '<old_name>' '<new_name>' --delete`
   - Cancel: stop and report no changes made.

5. **Report results**: Show the import count and verification output.
