#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$script_dir/lib.sh"

QUENYA_DIR=${QUENYA_DIR:-/opt/quenya}
cd "$QUENYA_DIR"

release_dir=$QUENYA_DIR/.releases
current_file=$release_dir/current
history_file=$release_dir/history
compose_file=deploy/vps/docker-compose.yml

if [ "${1-}" = "--list" ]; then
    if [ "$#" -ne 1 ]; then
        printf 'Usage: sh deploy/vps/rollback.sh [tag|--list]\n' >&2
        exit 2
    fi
    current_tag=
    if [ -f "$current_file" ]; then
        current_tag=$(sed -n '1p' "$current_file")
    fi
    if [ -f "$history_file" ]; then
        awk '{ entries[NR] = $0 } END { for (i = NR; i > 0; i--) print entries[i] }' \
            "$history_file" |
            while IFS= read -r history_tag; do
                [ -n "$history_tag" ] || continue
                if [ "$history_tag" = "$current_tag" ]; then
                    printf '* %s\n' "$history_tag"
                else
                    printf '  %s\n' "$history_tag"
                fi
            done
    fi
    exit 0
fi

if [ "$#" -gt 1 ]; then
    printf 'Usage: sh deploy/vps/rollback.sh [tag|--list]\n' >&2
    exit 2
fi

if [ "$#" -eq 1 ]; then
    target=$1
else
    if [ ! -f "$current_file" ] || [ ! -f "$history_file" ]; then
        printf 'No previous release is recorded.\n' >&2
        exit 1
    fi
    current_tag=$(sed -n '1p' "$current_file")
    previous_history_tag=
    target=
    found_current=0
    while IFS= read -r history_tag || [ -n "$history_tag" ]; do
        if [ "$history_tag" = "$current_tag" ]; then
            target=$previous_history_tag
            found_current=1
        fi
        previous_history_tag=$history_tag
    done < "$history_file"
    if [ "$found_current" -ne 1 ] || [ -z "$target" ]; then
        printf 'No previous release is recorded before current release %s.\n' "$current_tag" >&2
        exit 1
    fi
fi

export QUENYA_IMAGE_TAG="$target"
if ! docker image inspect "quenya-quenya-api:$target" >/dev/null 2>&1; then
    printf 'Release image quenya-quenya-api:%s does not exist.\n' "$target" >&2
    exit 1
fi

if [ -f "$current_file" ]; then
    previous_current=$(sed -n '1p' "$current_file")
else
    previous_current=none
fi

QUENYA_IMAGE_TAG="$target" docker compose -f "$compose_file" up -d quenya-api quenya-worker
if wait_for_health; then
    mkdir -p "$release_dir"
    printf '%s\n' "$target" > "$current_file.tmp.$$"
    mv "$current_file.tmp.$$" "$current_file"
    printf '%s\n' "$target" >> "$history_file"
    printf 'Rolled back to %s (previous: %s)\n' "$target" "$previous_current"
    exit 0
fi
exit 1
