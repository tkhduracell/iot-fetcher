#!/usr/bin/env bash
# Poll a pull request's CI checks and merge it as soon as they're all green.
#
# Stands in for GitHub's native auto-merge, which isn't available on private
# repos under the free plan. Run it in the background and forget about it —
# it prints one line per state change and a final verdict, then exits with a
# code that says what happened (see EXIT CODES below).
set -uo pipefail

INTERVAL=60
TIMEOUT=3600
METHOD=squash
DELETE_BRANCH=--delete-branch
ALLOW_NO_CHECKS=false
PR=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [<pr-number-or-url>] [options]

Polls the PR's checks and merges when every one has passed. With no PR
argument, uses the PR for the current branch.

Options:
  --method <squash|merge|rebase>  Merge method (default: squash)
  --interval <seconds>            Poll interval (default: 60)
  --timeout <seconds>             Give up after this long (default: 3600)
  --keep-branch                   Don't delete the head branch after merging
  --allow-no-checks               Merge even if the PR has no CI checks at all
  -h, --help                      Show this help

Exit codes:
  0  merged
  2  a check failed — nothing was merged
  3  merge conflict with the base branch
  4  blocked by review (changes requested, or an approval is required)
  5  GitHub refused the merge for some other reason
  6  timed out while checks were still running
  7  PR is a draft — mark it ready first
  8  PR has no checks (pass --allow-no-checks to merge anyway)
  9  PR isn't open (already merged or closed)
 10  setup problem (no gh, not authenticated, or no PR found)

Examples:
  $(basename "$0")                    # PR for the current branch
  $(basename "$0") 441                # a specific PR
  $(basename "$0") 441 --interval 30  # poll more often
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --method)          METHOD="$2"; shift 2 ;;
    --interval)        INTERVAL="$2"; shift 2 ;;
    --timeout)         TIMEOUT="$2"; shift 2 ;;
    --keep-branch)     DELETE_BRANCH=""; shift ;;
    --allow-no-checks) ALLOW_NO_CHECKS=true; shift ;;
    -h|--help)         usage; exit 0 ;;
    -*)                echo "unknown option: $1" >&2; usage >&2; exit 10 ;;
    *)                 PR="$1"; shift ;;
  esac
done

case "$METHOD" in
  squash|merge|rebase) ;;
  *) echo "--method must be squash, merge or rebase (got '$METHOD')" >&2; exit 10 ;;
esac

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

command -v gh >/dev/null 2>&1 || {
  echo "gh CLI not found — install it, or drive the merge through the GitHub MCP tools instead." >&2
  exit 10
}

FIELDS='number,title,url,state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup'
GH_ERR=""
snapshot=""
ERRFILE="$(mktemp -t gh-automerge.XXXXXX)"
trap 'rm -f "$ERRFILE"' EXIT

# Sets the globals `snapshot` and `GH_ERR`. It has to assign them in the
# caller's shell rather than echoing — `x=$(fetch_pr)` would run the whole
# function in a subshell and the error text would never make it back out.
fetch_pr() {
  GH_ERR=""
  local out
  if out=$(gh pr view ${PR:+"$PR"} --json "$FIELDS" 2>"$ERRFILE"); then
    snapshot="$out"
    return 0
  fi
  GH_ERR="$(cat "$ERRFILE")"
  return 1
}

# Don't probe auth separately: a token can satisfy `gh auth status` and even
# `gh api user` while still being denied on this repo (that's what happens in
# Claude Code remote sessions). The first real call is the honest test.
if ! fetch_pr; then
  if grep -qiE 'not enabled|forbidden|401|403|authentication|gh auth login' <<<"$GH_ERR"; then
    echo "gh can't reach this repo: ${GH_ERR%%$'\n'*}" >&2
    echo "Run 'gh auth login', or drive the merge through the GitHub MCP tools instead." >&2
  else
    echo "No pull request found${PR:+ for '$PR'} — pass a PR number, or run this from a branch that has one." >&2
    [[ -n "$GH_ERR" ]] && echo "$GH_ERR" >&2
  fi
  exit 10
fi

PR="$(jq -r .number <<<"$snapshot")"
log "watching PR #$PR — $(jq -r .title <<<"$snapshot")"
log "$(jq -r .url <<<"$snapshot")"

# Reduce the check rollup to one line per check: "<state>\t<name>\t<url>".
# CheckRun entries (Actions) report status+conclusion; StatusContext entries
# (classic commit statuses, e.g. Netlify) report a single state. NEUTRAL and
# SKIPPED are deliberately counted as passing — they mean "this check chose
# not to block", which is how GitHub treats them too.
ROLLUP_JQ='
  (.statusCheckRollup // [])[] |
  if .__typename == "CheckRun" then
    (if .status != "COMPLETED" then "PENDING"
     elif (.conclusion | ascii_upcase) as $c
          | ($c == "SUCCESS" or $c == "NEUTRAL" or $c == "SKIPPED") then "PASS"
     else "FAIL" end) as $s
    | [$s, .name, (.detailsUrl // "")]
  else
    ((.state // "") | ascii_upcase) as $c
    | (if $c == "SUCCESS" then "PASS"
       elif $c == "PENDING" or $c == "EXPECTED" then "PENDING"
       else "FAIL" end) as $s
    | [$s, (.context // .name // "status"), (.targetUrl // "")]
  end | @tsv'

deadline=$(( $(date +%s) + TIMEOUT ))
no_checks_polls=0
last_summary=""

# Sleep until the next poll and refresh the snapshot, or give up if that
# would run past the deadline.
wait_next() {
  if (( $(date +%s) + INTERVAL > deadline )); then
    log "timed out after ${TIMEOUT}s — PR #$PR left unmerged."
    exit 6
  fi
  sleep "$INTERVAL"
  # A transient API blip shouldn't kill a watch that's been running for an
  # hour — keep the previous snapshot and try again next lap.
  fetch_pr || log "couldn't refresh PR state, retrying"
}

while :; do
  state=$(jq -r .state <<<"$snapshot")
  if [[ "$state" != "OPEN" ]]; then
    log "PR #$PR is $state, not open — nothing to merge."
    exit 9
  fi

  if [[ "$(jq -r .isDraft <<<"$snapshot")" == "true" ]]; then
    log "PR #$PR is a draft — mark it ready for review first."
    exit 7
  fi

  checks="$(jq -r "$ROLLUP_JQ" <<<"$snapshot")"
  total=$(grep -c . <<<"$checks" || true)
  pending=$(grep -c '^PENDING' <<<"$checks" || true)
  failed=$(grep -c '^FAIL' <<<"$checks" || true)

  if (( total == 0 )); then
    if [[ "$ALLOW_NO_CHECKS" == "true" ]]; then
      log "no checks on this PR; merging anyway (--allow-no-checks)"
      pending=0; failed=0
    else
      # Checks often take a few seconds to register after a push, so don't
      # bail on the first empty poll.
      no_checks_polls=$(( no_checks_polls + 1 ))
      if (( no_checks_polls >= 3 )); then
        log "PR #$PR has no CI checks after $no_checks_polls polls — refusing to merge blindly."
        log "Re-run with --allow-no-checks if that's expected for this repo."
        exit 8
      fi
      log "no checks reported yet (poll $no_checks_polls/3)"
      wait_next
      continue
    fi
  fi

  if (( failed > 0 )); then
    log "CI failed on PR #$PR — not merging. Failing checks:"
    grep '^FAIL' <<<"$checks" | while IFS=$'\t' read -r _ name url; do
      log "  ✗ $name${url:+  $url}"
    done
    exit 2
  fi

  if (( pending == 0 )); then
    mergeable=$(jq -r .mergeable <<<"$snapshot")
    merge_state=$(jq -r .mergeStateStatus <<<"$snapshot")
    review=$(jq -r .reviewDecision <<<"$snapshot")

    if [[ "$mergeable" == "CONFLICTING" || "$merge_state" == "DIRTY" ]]; then
      log "PR #$PR conflicts with its base branch — resolve the conflicts first."
      exit 3
    fi
    if [[ "$review" == "CHANGES_REQUESTED" || "$review" == "REVIEW_REQUIRED" ]]; then
      log "PR #$PR is blocked by review ($review)."
      exit 4
    fi

    (( total > 0 )) && log "all $total check(s) green — merging (--$METHOD)"
    if merge_out=$(gh pr merge "$PR" "--$METHOD" $DELETE_BRANCH 2>&1); then
      log "merged PR #$PR ✓"
      exit 0
    fi
    # UNKNOWN mergeability means GitHub is still computing the merge commit;
    # that's worth another lap rather than a hard failure.
    if [[ "$mergeable" == "UNKNOWN" ]]; then
      log "merge not ready yet (mergeability still being computed), retrying"
    else
      log "GitHub refused the merge: $merge_out"
      exit 5
    fi
  else
    summary="$pending of $total check(s) still running: $(grep '^PENDING' <<<"$checks" | cut -f2 | paste -sd', ' -)"
    # Only speak up when something actually changed, so a long wait doesn't
    # bury the interesting lines (and doesn't spam if run under a monitor).
    if [[ "$summary" != "$last_summary" ]]; then
      log "$summary"
      last_summary="$summary"
    fi
  fi

  wait_next
done
