#!/bin/bash
# Usage: run_experiment.sh <tag> <label>   — assumes release already pushed.
TAG=$1; LABEL=$2
DIR="$(cd "$(dirname "$0")" && pwd)"
until [ "$(gh api repos/VitaDAO/vita-agent-model-tinfoil-config/releases/latest -q '.tag_name' 2>/dev/null)" = "$TAG" ]; do sleep 10; done
echo "release $TAG live"
tinfoil container stop vita-agent-model >/dev/null 2>&1
sleep 8
tinfoil container start vita-agent-model --tag "$TAG" >/dev/null 2>&1
for i in $(seq 1 120); do
  S=$(tinfoil container get vita-agent-model -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','?'), d.get('current_tag',''))" 2>/dev/null)
  case "$S" in
    "ready $TAG") echo "READY after $((i*20))s"; break;;
    failed*|error*) echo "FAILED: $S"; exit 1;;
  esac
  sleep 20
done
[ "$S" = "ready $TAG" ] || { echo "TIMEOUT still: $S"; exit 1; }
pkill -f "tinfoil container connect vita-agent-model" 2>/dev/null
sleep 3
tinfoil container connect vita-agent-model -p 3301 >/dev/null 2>&1 &
PROXY=$!
for i in $(seq 1 15); do curl -sf -m 5 http://localhost:3301/health >/dev/null 2>&1 && break; sleep 2; done
python3 "$DIR/bench_protocol.py" "$LABEL"
kill $PROXY 2>/dev/null
echo "EXPERIMENT $LABEL COMPLETE"
