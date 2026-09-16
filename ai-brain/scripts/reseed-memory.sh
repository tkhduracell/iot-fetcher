#!/usr/bin/env bash
# Put the image's seed identity and constitution back on a running brain.
#
# The seed is copied to the volume on first boot only -- after that the files
# are the deployment's, and pulling a new image never touches them. So a change
# to seed/personas/brain.md reaches a box that has already booted once only if
# somebody puts it there, which is what this does.
#
# Run it on the machine hosting the container (rpi5), from anywhere:
#     ./ai-brain/scripts/reseed-memory.sh            # identity + constitution
#     ./ai-brain/scripts/reseed-memory.sh --goals    # ...and clear goals.md
#
# Every file it replaces is backed up next to itself with a timestamp first.
# The agent rewrites identity.md and goals.md itself over time, so what you are
# overwriting may be its own words, not yours.
set -euo pipefail

CONTAINER=${CONTAINER:-ai-brain}
MEMORY=${MEMORY_ROOT:-/memory}
SEED=${SEED_ROOT:-/app/seed}
CLEAR_GOALS=0

for arg in "$@"; do
    case "$arg" in
        --goals) CLEAR_GOALS=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "no container named $CONTAINER (try: sudo $0)" >&2
    exit 1
fi

stamp=$(date +%Y%m%dT%H%M%S)

backup_and_copy() {
    local src=$1 dst=$2
    docker exec "$CONTAINER" sh -c "
        set -e
        [ -f '$src' ] || { echo 'missing in image: $src' >&2; exit 1; }
        if [ -f '$dst' ]; then cp '$dst' '$dst.$stamp.bak'; fi
        cp '$src' '$dst'
    "
    echo "wrote $dst (previous copy kept as $dst.$stamp.bak)"
}

backup_and_copy "$SEED/personas/brain.md" "$MEMORY/brain/identity.md"
backup_and_copy "$SEED/constitution.md" "$MEMORY/constitution.md"

if [ "$CLEAR_GOALS" = 1 ]; then
    docker exec "$CONTAINER" sh -c "
        set -e
        if [ -f '$MEMORY/brain/goals.md' ]; then
            cp '$MEMORY/brain/goals.md' '$MEMORY/brain/goals.md.$stamp.bak'
        fi
        : > '$MEMORY/brain/goals.md'
    "
    echo "cleared $MEMORY/brain/goals.md (previous copy kept)"
fi

echo
echo "The running process reads these files at the top of every cycle, so the"
echo "next cycle picks them up -- no restart needed."
