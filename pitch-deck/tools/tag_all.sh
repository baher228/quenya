#!/usr/bin/env bash
# Tag the local deck catalogue with the app's real LLM tagger, one process per shard.
cd "$(dirname "$0")/../../backend"
export DB_PATH="${DB_PATH:-/workspace/pitch-deck/.work/q.sqlite}" PYTHON_DOTENV_DISABLED=1 SHARDS="${SHARDS:-6}"
for ((i = 0; i < SHARDS; i++)); do SHARD=$i ../.venv/bin/python ../pitch-deck/tools/tag_all.py & done
wait
echo ALL-TAGGED
