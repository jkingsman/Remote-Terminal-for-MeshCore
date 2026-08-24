#!/usr/bin/env bash
set -e

OPTIONS=/data/options.json

if [ -f "$OPTIONS" ]; then
  # keys are already uppercase -> export as-is
  eval "$(jq -r 'to_entries | .[] | "export \(.key)=\(.value | @sh)"' "$OPTIONS")"
fi

exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
