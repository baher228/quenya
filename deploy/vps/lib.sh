#!/bin/sh

wait_for_health() {
    health_timeout=${QUENYA_HEALTH_TIMEOUT_S:-180}
    case "$health_timeout" in
        ''|*[!0-9]*) health_timeout=180 ;;
    esac
    health_interval=${QUENYA_HEALTH_POLL_INTERVAL_S:-2}
    case "$health_interval" in
        ''|*[!0-9]*) health_interval=2 ;;
    esac

    deadline=$(( $(date +%s) + health_timeout ))
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
                return 0
            fi
        fi
        sleep "$health_interval"
    done

    printf 'API did not become healthy within %s seconds.\n' "$health_timeout" >&2
    if [ -n "$last_health" ]; then
        printf '%s\n' "$last_health" | python3 -c '
import json
import sys

print(json.dumps(json.load(sys.stdin).get("schema"), sort_keys=True))
' >&2 2>/dev/null || true
    fi
    return 1
}
