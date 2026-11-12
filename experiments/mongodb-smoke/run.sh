#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$TEST_ID" =~ ^dd_smoke_[0-9]+_[0-9]+$ ]]
test "$(uname -s)/$(uname -m)" = Linux/x86_64
for tool in docker python3 openssl timeout sudo; do command -v "$tool" >/dev/null; done
# Docker Hub manifest bytes independently verified; native AMD64 manifest pin.
MONGO_IMAGE='mongo:8.0.38-noble@sha256:9b73c38e39783bd21335f603a9f08be60fe385f5c28933ea32bb4a9fa1fd6d23'
python3 - <<'PY'
import shutil
memory = int(next(x.split()[1] for x in open('/proc/meminfo') if x.startswith('MemAvailable:'))) * 1024
free = shutil.disk_usage('.').free
print(f'Available memory={memory // 2**20} MiB; disk={free // 2**30} GiB')
assert memory >= 4 * 2**30 and free >= 10 * 2**30, 'Insufficient resources'
PY
work=$(mktemp -d "$RUNNER_TEMP/mongodb-smoke.XXXXXX")
chmod 700 "$work"
# Only these allowlisted sources enter the image build context. No .env or Git data.
mkdir -p "$work/build/backend" "$work/build/experiments/mongodb-smoke"
for file in document_storage.py storage_contract.py storage_schema.py; do
  cp "backend/$file" "$work/build/backend/"
done
cp experiments/mongodb-smoke/{Dockerfile,smoke.py} "$work/build/experiments/mongodb-smoke/"
# Public source files must remain readable by the image's nonroot test user.
chmod 644 "$work/build/backend/"*.py "$work/build/experiments/mongodb-smoke/"*
timeout --signal=TERM --kill-after=15s 8m docker build --platform linux/amd64 \
  --label "deepdocs.smoke=$TEST_ID" -t "deepdocs-smoke:$TEST_ID" \
  -f "$work/build/experiments/mongodb-smoke/Dockerfile" "$work/build"
timeout --signal=TERM --kill-after=15s 4m docker pull --platform linux/amd64 "$MONGO_IMAGE"
test "$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$MONGO_IMAGE")" = linux/amd64
export SMOKE_WORK="$work"
python3 - <<'PY'
import json, os, secrets
from pathlib import Path
p = Path(os.environ['SMOKE_WORK'])
c = dict(database=os.environ['TEST_ID'], replica=os.environ['TEST_ID'], password=secrets.token_hex(32))
(p/'app.json').write_text(json.dumps(c))
c['admin_password'] = secrets.token_hex(32)
(p/'bootstrap.json').write_text(json.dumps(c))
(p/'keyfile').write_text(secrets.token_urlsafe(512).replace('-', 'a').replace('_', 'b'))
PY
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj '/CN=Disposable smoke CA' \
  -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -keyout "$work/ca.key" -out "$work/ca.crt" >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -subj '/CN=localhost' \
  -keyout "$work/server.key" -out "$work/server.csr" >/dev/null 2>&1
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth\n' > "$work/extensions"
openssl x509 -req -in "$work/server.csr" -CA "$work/ca.crt" -CAkey "$work/ca.key" \
  -CAcreateserial -days 1 -extfile "$work/extensions" -out "$work/server.crt" >/dev/null 2>&1
cat "$work/server.key" "$work/server.crt" > "$work/server.pem"
openssl verify -CAfile "$work/ca.crt" -purpose sslserver -verify_hostname localhost "$work/server.crt" >/dev/null
openssl verify -CAfile "$work/ca.crt" -purpose sslserver -verify_ip 127.0.0.1 "$work/server.crt" >/dev/null
sudo chown 999:999 "$work/keyfile" "$work/server.pem"
sudo chmod 400 "$work/keyfile" "$work/server.pem"
chmod 444 "$work/app.json" "$work/bootstrap.json" "$work/ca.crt"
docker run -d --name "$TEST_ID" --label "deepdocs.smoke=$TEST_ID" \
  --network none --user 999:999 --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 2g --memory-swap 2g --cpus 2 --pids-limit 128 \
  --tmpfs /data/db:rw,nosuid,noexec,size=1g,uid=999,gid=999,mode=700 \
  --tmpfs /data/configdb:rw,nosuid,noexec,size=16m,uid=999,gid=999,mode=700 \
  --tmpfs /tmp:rw,nosuid,noexec,size=64m,mode=1777 \
  --mount "type=bind,src=$work/keyfile,dst=/run/keyfile,readonly" \
  --mount "type=bind,src=$work/server.pem,dst=/run/server.pem,readonly" \
  --mount "type=bind,src=$work/ca.crt,dst=/run/ca.crt,readonly" \
  --mount "type=bind,src=$work/bootstrap.json,dst=/run/bootstrap.json,readonly" \
  --mount "type=bind,src=$PWD/experiments/mongodb-smoke/bootstrap.js,dst=/run/bootstrap.js,readonly" \
  --entrypoint mongod "$MONGO_IMAGE" --replSet "$TEST_ID" --bind_ip 127.0.0.1 \
  --keyFile /run/keyfile --tlsMode requireTLS --tlsCertificateKeyFile /run/server.pem \
  --tlsCAFile /run/ca.crt --tlsAllowConnectionsWithoutCertificates \
  --wiredTigerCacheSizeGB 0.256 --nounixsocket >/dev/null
# No published ports, no bridge, no external routes. Test shares only this namespace.
test "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$TEST_ID")" = none
docker inspect "$TEST_ID" | python3 -c 'import json,sys; c=json.load(sys.stdin)[0]; assert not c["HostConfig"].get("PortBindings"); assert c["HostConfig"]["NetworkMode"] == "none"'
# mongosh must connect before executing the script; bound initial listener wait.
ready=false
for i in $(seq 1 30); do
  test "$(docker inspect -f '{{.State.Running}}' "$TEST_ID")" = true
  if timeout 5s docker exec --env HOME=/tmp "$TEST_ID" mongosh --quiet --host localhost --tls \
    --tlsCAFile /run/ca.crt --eval 'quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 1)' >/dev/null 2>&1; then
    ready=true; break
  fi
  sleep 1
done
test "$ready" = true
echo 'Listener ready; initializing disposable replica and scoped user.'
timeout --signal=TERM --kill-after=5s 150s docker exec --env HOME=/tmp "$TEST_ID" \
  mongosh --quiet --host localhost --tls --tlsCAFile /run/ca.crt /run/bootstrap.js
# MongoDB 8.0 requires 500 MiB free in dbPath before index builds. Host disk
# preflight cannot measure this separate tmpfs. Keep 100 MiB additional headroom.
available_kib=$(timeout 5s docker exec "$TEST_ID" df -Pk /data/db | awk 'NR == 2 {print $4}')
[[ "$available_kib" =~ ^[0-9]+$ ]]
echo "MongoDB dbPath available KiB=$available_kib; required preflight KiB=614400"
if (( available_kib < 614400 )); then
  echo 'Insufficient disposable dbPath space for safe index creation.'
  exit 1
fi
timeout --signal=TERM --kill-after=5s 120s docker run --name "${TEST_ID}_test" \
  --label "deepdocs.smoke=$TEST_ID" --network "container:$TEST_ID" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 256m --memory-swap 256m --cpus 1 --pids-limit 64 \
  --tmpfs /tmp:rw,nosuid,noexec,size=16m \
  --mount "type=bind,src=$work/app.json,dst=/run/app.json,readonly" \
  --mount "type=bind,src=$work/ca.crt,dst=/run/ca.crt,readonly" "deepdocs-smoke:$TEST_ID"
docker stop --time 30 "$TEST_ID" >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' "$TEST_ID")" = 0
