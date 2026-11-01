#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$script_dir/lib.sh"

QUENYA_DIR=${QUENYA_DIR:-/opt/quenya}
cd "$QUENYA_DIR"

compose_file=deploy/vps/docker-compose.yml
release_dir=$QUENYA_DIR/.releases
current_file=$release_dir/current
history_file=$release_dir/history
first_release_run=0
if [ ! -d "$release_dir" ]; then
    first_release_run=1
fi
mkdir -p "$release_dir"

tag_candidate=${QUENYA_RELEASE-}
case "$tag_candidate" in
    ''|*[!A-Za-z0-9._-]*)
        tag=$(date -u +%Y%m%d%H%M%S)
        ;;
    *)
        if [ "${#tag_candidate}" -le 64 ]; then
            tag=$tag_candidate
        else
            tag=$(date -u +%Y%m%d%H%M%S)
        fi
        ;;
esac
export QUENYA_IMAGE_TAG="$tag"

prev=
if [ -f "$current_file" ]; then
    prev=$(sed -n '1p' "$current_file")
fi
bootstrap_previous=0
if [ -z "$prev" ] && [ "$first_release_run" -eq 1 ]; then
    if docker image inspect quenya-quenya-api:latest >/dev/null 2>&1; then
        prev=pre-$tag
        docker image tag quenya-quenya-api:latest "quenya-quenya-api:$prev"
        bootstrap_previous=1
    fi
fi

QUENYA_IMAGE_TAG=$tag docker compose -f "$compose_file" build quenya-api
QUENYA_IMAGE_TAG=$tag docker compose -f "$compose_file" run --rm --no-deps \
    quenya-api python -m app.migrate "$@"
QUENYA_IMAGE_TAG=$tag docker compose -f "$compose_file" up -d quenya-api quenya-worker

if wait_for_health; then
    docker image tag "quenya-quenya-api:$tag" quenya-quenya-api:latest
    if [ "$bootstrap_previous" -eq 1 ]; then
        printf '%s\n' "$prev" >> "$history_file"
    fi
    printf '%s\n' "$tag" > "$current_file.tmp.$$"
    mv "$current_file.tmp.$$" "$current_file"
    printf '%s\n' "$tag" >> "$history_file"
    printf 'Deployed %s (previous: %s)\n' "$tag" "${prev:-none}"

    keep_history=$(tail -n 5 "$history_file" 2>/dev/null || true)
    image_list=$(docker image ls --format '{{.Repository}}:{{.Tag}}' quenya-quenya-api 2>/dev/null || true)
    printf '%s\n' "$image_list" | while IFS= read -r image; do
        case "$image" in
            quenya-quenya-api:*) image_tag=${image#quenya-quenya-api:} ;;
            *) continue ;;
        esac
        case "$image_tag" in
            ''|'<none>'|latest|"$tag"|"$prev") continue ;;
        esac
        case "
$keep_history
" in
            *"
$image_tag
"*) continue ;;
        esac
        docker image rm "$image" >/dev/null 2>&1 || true
    done
    exit 0
fi

if [ -n "$prev" ]; then
    printf 'Deployment %s failed health checks; rolling back to %s.\n' "$tag" "$prev" >&2
    export QUENYA_IMAGE_TAG="$prev"
    if docker compose -f "$compose_file" up -d quenya-api quenya-worker; then
        :
    else
        printf 'Could not start rollback release %s.\n' "$prev" >&2
    fi
    if wait_for_health; then
        docker image tag "quenya-quenya-api:$prev" quenya-quenya-api:latest
        printf 'Rollback to %s is healthy; deployment %s remains failed.\n' "$prev" "$tag" >&2
    else
        printf 'Rollback to %s did not become healthy.\n' "$prev" >&2
    fi
else
    printf 'Deployment %s failed health checks; no previous release is available.\n' "$tag" >&2
fi
exit 1
