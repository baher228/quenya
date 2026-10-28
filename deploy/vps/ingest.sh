#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
    echo "usage: $0 SOURCE [app.ingest arguments...]" >&2
    exit 64
fi

source=$1
shift
case "$source" in
    vinted|retail) ;;
    *)
        echo "source must be vinted or retail" >&2
        exit 64
        ;;
esac

exec flock -n "/run/quenya-ingest-$source.lock" \
    docker run --rm \
    --memory 1500m \
    --cpus 0.75 \
    --network container:quenya-quenya-api-1 \
    --env-file /etc/quenya/quenya.env \
    -w /app/backend \
    -e PYTHONPATH=/app/backend \
    -v quenya_quenya-cache:/cache \
    quenya-quenya-api \
    python -m app.ingest --source "$source" "$@"
