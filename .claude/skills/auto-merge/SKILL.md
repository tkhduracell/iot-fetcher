---
name: auto-merge
description: Watch a pull request's CI checks and merge it as soon as they're all green. Use this whenever the user wants a PR merged after CI passes rather than right now — "merge when green", "merge it when CI passes", "auto-merge this", "merge when done", "keep an eye on the PR and land it", or just "merged when done" after you've opened a PR. This repo is private on a free GitHub plan, so GitHub's own auto-merge button does not exist here and `enable_pr_auto_merge` will fail — this skill is the replacement. Also use it when a PR needs babysitting to green before landing.
allowed-tools: Bash, mcp__github__pull_request_read, mcp__github__merge_pull_request, mcp__github__list_pull_requests, mcp__github__unsubscribe_pr_activity
---

# Auto-merge a PR when CI goes green

GitHub's native auto-merge is a paid feature for private repos, so it isn't
available on this one. `enable_pr_auto_merge` will fail here — don't reach for
it. Instead, watch the checks and merge yourself once they pass.

There are two ways to do that, and which one you use depends on whether `gh`
can actually reach the repo from wherever you're running.

## Pick a lane

Just run the script. It tells you within a second if `gh` can't get through:

```bash
./scripts/gh-automerge.sh <pr-number>          # or no argument for the current branch
```

- **Exit 10 with "gh can't reach this repo"** → you're in a Claude Code remote
  or web session, where GitHub is only reachable through the MCP tools even
  though the `gh` binary exists and `gh auth status` looks plausible. Use
  **Lane B**.
- **Anything else** → `gh` works. Use **Lane A**.

## Lane A — `gh` works (local machine, rpi5)

Run the script in the background and let it do the whole job. It polls, merges
when green, and exits with a code that says what happened:

```bash
./scripts/gh-automerge.sh 441 --interval 60
```

Use `run_in_background: true` — this can legitimately run for an hour, and you
get one notification when it finishes rather than a turn per poll. Read the
output file when it completes and report the verdict.

Options worth knowing: `--method merge|rebase` (default `squash`, which matches
this repo's linear `title (#N)` history), `--interval`, `--timeout`,
`--keep-branch`, `--allow-no-checks`. `--help` lists everything.

Exit codes: `0` merged · `2` a check failed · `3` conflict · `4` blocked by
review · `5` GitHub refused · `6` timed out · `7` draft · `8` no checks ·
`9` not open · `10` setup problem.

Nothing was merged for any non-zero code, so each one needs a decision from
you, not a silent retry.

## Lane B — MCP only (Claude Code on the web / remote sessions)

Same logic, driven by hand. The loop per poll:

1. `mcp__github__pull_request_read` with `method: "get_check_runs"` — every run
   needs `status: "completed"`. Treat `success`, `neutral` and `skipped` as
   passing; anything else (`failure`, `cancelled`, `timed_out`,
   `action_required`) is a failure.
2. If checks are still running, wait and poll again. Foreground `sleep` is
   blocked in this harness, so pace yourself with a background tick:
   `Bash("sleep 90", run_in_background: true)` — the completion notification
   wakes you for the next poll. Start at 60s and ease out toward ~3 min
   (60 → 90 → 120 → 150 → 180); a typical build here takes ~9 minutes, so
   tight polling just burns turns.
3. When everything is complete and passing, check `method: "get"` for
   `draft`, `mergeable`/`mergeable_state`, and review state before merging.
4. Merge with `mcp__github__merge_pull_request`, `merge_method: "squash"`,
   `commit_title: "<PR title> (#<number>)"`.

## What must stop the merge

These are the reasons the script exits non-zero, and they apply just as much in
Lane B. The point of an auto-merge helper is to save waiting, not to lower the
bar for landing code:

- **A failing check.** Report which one and link the job. Diagnose it — the fix
  is a commit, not a re-run. Re-running is only right when the job died before
  any test body executed (checkout, dependency install, lost runner), and never
  a way to shake loose a real failure. Never skip or disable a test to get green.
- **A draft PR.** Mark it ready first, deliberately.
- **A merge conflict.** Merge the base branch in, resolve, push, and let CI
  re-run before merging.
- **Changes requested, or a required approval that isn't there.** That's a
  human's call.
- **No checks at all.** Usually means the workflow hasn't registered yet, not
  that the PR is safe. The script waits three polls, then refuses; only pass
  `--allow-no-checks` when you know the repo genuinely has no CI for that path.

## After it merges

- Report the squash SHA and that CI was green when it landed.
- If the session was watching the PR, `mcp__github__unsubscribe_pr_activity`
  and delete any scheduled check-in Routine you created for it — the merge
  notice makes both obsolete.
- Deployment isn't automatic. Merging only lands the change on `main`; getting
  it onto rpi5 is a separate step (see the `rpi5` skill).
