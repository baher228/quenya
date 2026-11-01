#!/bin/sh
set -eu

cd /opt/quenya

docker compose -f deploy/vps/docker-compose.yml build quenya-api
docker compose -f deploy/vps/docker-compose.yml run --rm --no-deps quenya-api \
    python -m app.migrate "$@"
docker compose -f deploy/vps/docker-compose.yml up -d quenya-api

deadline=$(( $(date +%s) + 180 ))
last_health=
while [ "$(date +%s)" -lt "$deadline" ]; do
    if last_health=$(curl -fsS --max-time 5 http://127.0.0.1:8100/api/health); then
        if printf '%s\n' "$last_health" | python3 -c '
import json
import sys

health = json.load(sys.stdin)
schema = health.get("schema")
sys.exit(0 if health.get("ok") is True and schema and schema.get("ok") is True else 1)
' 2>/dev/null; then
            printf '%s\n' "$last_health" | python3 -c '
import json
import sys

print(json.dumps(json.load(sys.stdin).get("schema"), sort_keys=True))
'
            exit 0
        fi
    fi
    sleep 2
done

printf '%s\n' "API did not become healthy within 180 seconds." >&2
if [ -n "$last_health" ]; then
    printf '%s\n' "$last_health" | python3 -c '
import json
import sys

print(json.dumps(json.load(sys.stdin).get("schema"), sort_keys=True))
' >&2 || true
fi
exit 1
