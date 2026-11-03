#!/usr/bin/env bash
# Snapshot the agents' memory to Google Cloud Storage. Meant for a nightly cron.
#
# /memory is the one thing in this stack that cannot be rebuilt: the journals,
# facts, goals, identity and open proposals are months of an agent's own work,
# and they live on one SD card. Everything else comes back from the repo.
#
# Run it on the machine hosting the container (rpi5), as root, since the bind
# mount is owned by the container's uid 1000:
#     sudo ./ai-brain/scripts/backup-memory.sh --dry-run   # build and check only
#     sudo ./ai-brain/scripts/backup-memory.sh
#
# The upload happens inside the iot-fetcher container, by the same means as the
# VictoriaMetrics backup: it already has google-cloud-storage and the
# GOOGLE_SERVICE_ACCOUNT credential, so this box needs neither gcloud nor a second
# copy of a key. The destination is the same strftime template as
# GOOGLE_BACKUP_URI, set as AI_BRAIN_BACKUP_URI in the environment or in
# ai-brain/.env. See "Backing up /memory" in ai-brain/README.md.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)

MEMORY_DIR=${MEMORY_DIR:-$repo/volumes/ai-brain-memory}
ENV_FILE=${ENV_FILE:-$repo/ai-brain/.env}
FETCHER=${FETCHER_CONTAINER:-iot-fetcher}
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if [ ! -f "$MEMORY_DIR/constitution.md" ]; then
    # Backing up an empty directory over a good snapshot is the failure this
    # script exists to prevent: a volume that failed to mount looks exactly so.
    echo "no constitution.md in $MEMORY_DIR; is this the memory volume?" >&2
    exit 1
fi

uri=${AI_BRAIN_BACKUP_URI:-}
if [ -z "$uri" ] && [ -f "$ENV_FILE" ]; then
    uri=$(sed -n 's/^AI_BRAIN_BACKUP_URI=//p' "$ENV_FILE" | tail -n 1 | tr -d "\"'")
fi
if [ "$DRY_RUN" -eq 0 ] && [[ "$uri" != gcs://* ]]; then
    echo "AI_BRAIN_BACKUP_URI must be set to gcs://<bucket>/<prefix>/ (got '$uri')" >&2
    exit 1
fi

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
name="ai-brain-memory-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
archive="$workdir/$name"

# Left out because they come back on their own or are only ever useful fresh:
#   _repo     the code_* tools' snapshot of this public repo, re-fetched on boot
#   sessions  per-cycle logs, pruned at 30 days. Nightly full copies of them would
#             be most of every snapshot, and losing them loses no agent memory.
#             BACKUP_SESSIONS=1 keeps them.
#   *.tmp     a write caught halfway, by the temp-file-plus-rename every writer uses
excludes=(--exclude='./_repo' --exclude='*.tmp')
if [ "${BACKUP_SESSIONS:-0}" != "1" ]; then
    excludes+=(--exclude='*/sessions')
fi

# Exit 1 is tar saying a file changed as it read it, which on a live volume is an
# ordinary night, and every file it reads was written atomically. Anything else
# is a real failure, and the check below is what decides the archive is good.
status=0
tar -C "$MEMORY_DIR" --numeric-owner "${excludes[@]}" -czf "$archive" . || status=$?
if [ "$status" -gt 1 ]; then
    echo "tar failed with status $status" >&2
    exit 1
fi

# Before anything leaves the box: it opens, and it has what makes it worth having.
if ! listing=$(tar -tzf "$archive"); then
    echo "the archive does not read back" >&2
    exit 1
fi
if ! grep -qx './constitution.md' <<<"$listing"; then
    echo "the archive has no constitution.md" >&2
    exit 1
fi
size=$(wc -c <"$archive" | tr -d ' ')
files=$(grep -cv '/$' <<<"$listing")

if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry run: $name, $files files, $size bytes; nothing uploaded"
    exit 0
fi

if ! docker inspect "$FETCHER" >/dev/null 2>&1; then
    echo "no container named $FETCHER (try: sudo $0)" >&2
    exit 1
fi

docker exec -i "$FETCHER" python -c "$(cat "$here/backup_upload.py")" "$uri" "$name" "$size" <"$archive"
echo "uploaded $name, $files files, $size bytes"
