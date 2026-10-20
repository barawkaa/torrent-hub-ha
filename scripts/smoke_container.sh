#!/bin/sh
set -eu
IMAGE="${1:-torrent-hub-ci:arm64}"
NAME="torrent-hub-ci-smoke"
docker rm -f "$NAME" >/dev/null 2>&1 || true
trap 'docker logs --tail 80 "$NAME" || true; docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$NAME" -p 18765:8765 --tmpfs /data:rw,size=512m "$IMAGE" >/dev/null
for n in $(seq 1 30); do
  if ! docker inspect --format '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; then
    echo "Container exited before passing checks"; exit 1
  fi
  if curl -fsS --max-time 4 http://127.0.0.1:18765/healthz >/dev/null 2>&1 && \
     curl -fsS --max-time 4 http://127.0.0.1:18765/api/status | python3 -c 'import json,sys; assert json.load(sys.stdin)["engine_key_ready"]' 2>/dev/null; then
    if curl -fsS --max-time 8 http://127.0.0.1:18765/api/indexer-catalog | python3 -c '
import json,sys,re
rows=json.load(sys.stdin)
ids={re.sub("[^a-z0-9]","",str(x.get("definitionName") or x.get("name","" )).lower()) for x in rows}
assert {"rutor","nonameclub","megapeer","kinozal","rustorka"} <= ids,(len(ids),sorted(ids)[:10])
' 2>/dev/null; then
      echo "PASS: ARM64 process started, Prowlarr catalog includes 5 Russian trackers"
      if curl -fsS --max-time 8 http://127.0.0.1:18765/api/indexers | python3 -c '
import json,sys
items=json.load(sys.stdin)["indexers"]
assert any(x.get("enable") and x.get("name","").lower() in ("rutor","megapeer","noname club","noname-club") for x in items),items
' 2>/dev/null; then
        echo "PASS: at least one public tracker was automatically configured"
        exit 0
      fi
    fi
  fi
  sleep 3
done
echo 'FAIL: Prowlarr catalog or automatic tracker setup did not become ready'
docker logs --tail 100 "$NAME" || true
docker exec "$NAME" sh -c 'tail -120 /data/torrent-hub/engine.log' || true
exit 1
