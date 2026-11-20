#!/usr/bin/env bash
set -euo pipefail
umask 077
# Host-only, standard-library evidence/cleanup utilities. No application imports.
if [[ "${1:-}" == --cleanup || "${1:-}" == --verify-evidence || "${1:-}" == --sources ]]; then
python3 - "$1" <<'HOSTPY'
import hashlib, json, math, os, re, subprocess, sys, time
from pathlib import Path

BACKEND = 'document_storage.py storage_contract.py storage_schema.py gridfs_storage.py storage_intents.py storage_reconciliation.py pdf_processing.py usage_budgets.py config.py growth_limits.py gridfs_growth.py gridfs_growth_recovery.py policy_readiness.py'.split()
HARNESS = 'Dockerfile smoke.py lifecycle.py budgets.py combined.py run.sh bootstrap.js README.md'.split()
D3B = 'gridfs_deletion.md gridfs_growth.md gridfs_growth_recovery.py gridfs_uploads.md gridfs_uploads.py storage_intents.py storage_reconciliation.md storage_reconciliation.py tests/test_gridfs_growth.py tests/test_gridfs_uploads.py tests/test_storage_intents.py tests/test_storage_reconciliation.py'.split()
SOURCES = sorted(set(['backend/' + p for p in BACKEND + D3B] +
    ['experiments/mongodb-smoke/' + p for p in HARNESS] + ['.github/workflows/mongodb-replica-smoke.yml']))
EXPECTED = {
    'preparation_atomic_rollback', 'preparation_missing_foreign_kb',
    'preparation_real_concurrent_kb_delete_conflict', 'preparation_simulated_lost_ack_retains_reservation',
} | {prefix + suffix for suffix in ('growth_off', 'growth_on') for prefix in (
    'preparation_commit_duplicate_identity_', 'publication_corrupt_incomplete_identity_metadata_rejection_',
    'publication_maintenance_stale_heartbeat_changed_review_', 'publication_real_rollback_retains_capacity_',
    'publication_matching_metadata_simulated_ack_idempotency_', 'deletion_orphan_vector_claim_maintenance_rejection_',
    'deletion_real_rollback_', 'deletion_simulated_ack_exactly_once_release_')}

def require(condition, reason):
    if not condition:
        raise ValueError(reason)

def source_hashes(root):
    result = {}
    for name in SOURCES:
        p = root / name
        require(not p.is_symlink() and p.is_file() and p.stat().st_size <= 2**20, 'source_input_invalid')
        result[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result

def unique_object(pairs):
    result = {}
    for k, v in pairs:
        require(k not in result, 'duplicate_json_key')
        result[k] = v
    return result

def read_evidence(directory, name):
    p = directory / name
    require(not p.is_symlink() and p.is_file() and 0 < p.stat().st_size <= 262144, 'evidence_missing_or_unbounded')
    value = json.loads(p.read_text(), object_pairs_hook=unique_object)
    require(type(value) is dict, 'evidence_structure')
    return value

def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum

def duration(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum

def targets(test_id):
    require(re.fullmatch(r'dd_smoke_[0-9]+_[0-9]+', test_id) is not None, 'invalid_test_identity')
    return [('container', test_id + '_test'), ('container', test_id), ('image', 'deepdocs-smoke:' + test_id)]

def validate_evidence(directory, root, actual_sha, expected_sha, test_id):
    require(re.fullmatch('[0-9a-f]{40}', actual_sha) is not None and actual_sha == expected_sha, 'commit_mismatch')
    source, resource, recovery, execution, cleanup = [read_evidence(directory, n + '.json')
        for n in ('sources', 'resources', 'recovery', 'execution', 'cleanup')]
    require(source.get('commit') == actual_sha and source.get('sha256') == source_hashes(root), 'source_manifest_mismatch')
    for measured, required, limit in (
        ('available_memory_bytes', 'required_memory_bytes', 4 * 2**30),
        ('free_disk_bytes', 'required_disk_bytes', 10 * 2**30),
        ('dbpath_available_kib', 'dbpath_required_kib', 614400)):
        require(type(resource.get(required)) is int and resource[required] == limit and
                integer(resource.get(measured), limit, 2**60), 'resource_preflight_invalid')
    require(recovery.get('mongodb_version') == '8.0.38' and type(recovery.get('replica_members')) is int and
            recovery['replica_members'] == 1 and recovery.get('tls_scoped_identity_verified') is True, 'topology_invalid')
    require(recovery.get('scope') == 'real_transactions_gridfs_primitives' and
            recovery.get('acknowledgement_loss') == 'simulated_after_real_commit' and
            recovery.get('concurrency') == 'separate_authenticated_clients_server_write_conflict', 'coverage_identity_invalid')
    rows = recovery.get('scenarios')
    require(type(rows) is list and len(rows) == 20, 'scenario_inventory_invalid')
    require(all(type(r) is dict and type(r.get('name')) is str for r in rows), 'scenario_structure')
    names = [r['name'] for r in rows]
    require(len(set(names)) == 20 and set(names) == EXPECTED, 'scenario_identity_invalid')
    require(all(r.get('outcome') == 'passed' and duration(r.get('seconds'), 300) for r in rows), 'scenario_not_passed')
    for name, expected in {'passed': 20, 'failed': 0, 'skipped': 0, 'planned': 20, 'not_run': 0}.items():
        require(type(recovery.get(name)) is int and recovery[name] == expected, 'scenario_counts_invalid')
    require(execution.get('complete') is True and type(execution.get('exit_code')) is int and
            execution['exit_code'] == 0 and duration(execution.get('seconds'), 1080), 'execution_incomplete')
    require(cleanup.get('owned_containers_and_tester_removed') is True and
            type(cleanup.get('exit_code')) is int and cleanup['exit_code'] == 0 and
            cleanup.get('scope') == 'exact_names_and_matching_ownership_labels' and
            duration(cleanup.get('seconds'), 120), 'cleanup_unconfirmed')
    records = cleanup.get('resources')
    require(type(records) is list and len(records) == 3, 'cleanup_inventory_invalid')
    for row, (kind, name) in zip(records, targets(test_id)):
        require(type(row) is dict and row.get('kind') == kind and row.get('name') == name and
                row.get('status') in ('absent', 'removed') and row.get('absence_verified') is True,
                'cleanup_resource_unconfirmed')
        require('error_category' not in row and row.get('phase') == (
            'inventory' if row['status'] == 'absent' else 'post_removal'), 'cleanup_phase_contradiction')
        if row['status'] == 'removed':
            require(row.get('ownership_verified') is True, 'cleanup_ownership_unconfirmed')

def docker_command(args):
    try:
        p = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('docker_api_unavailable') from None
    require(p.returncode == 0, 'docker_command_failed')
    require(len(p.stdout) <= 262144, 'docker_output_unbounded')
    return p.stdout

def inventory(kind, command):
    # Successful inventories establish absence; failed inspection never does.
    args = ['container', 'ls', '--all', '--no-trunc', '--format', '{{json .}}'] if kind == 'container' else [
        'image', 'ls', '--no-trunc', '--format', '{{json .}}']
    rows = [json.loads(line) for line in command(args).splitlines() if line]
    require(len(rows) <= 1000 and all(type(r) is dict for r in rows), 'docker_inventory_invalid')
    if kind == 'container':
        require(all(type(r.get('Names')) is str for r in rows), 'docker_inventory_invalid')
        return {r['Names'] for r in rows}
    require(all(type(r.get('Repository')) is str and type(r.get('Tag')) is str for r in rows), 'docker_inventory_invalid')
    return {r['Repository'] + ':' + r['Tag'] for r in rows}

def cleanup_resources(test_id, command=docker_command):
    start = time.monotonic()
    rows = [dict(kind=k, name=n, status='unknown', absence_verified=False) for k, n in targets(test_id)]
    success = False
    try:
        for row in rows:
            kind, name = row['kind'], row['name']
            row['phase'] = 'inventory'
            if name not in inventory(kind, command):
                row.update(status='absent', absence_verified=True)
                continue
            row['phase'] = 'inspection'
            value = json.loads(command([kind, 'inspect', name]))
            require(type(value) is list and len(value) == 1 and type(value[0]) is dict, 'inspection_invalid')
            value = value[0]
            labels = (value.get('Config') or {}).get('Labels') or {}
            if labels.get('deepdocs.smoke') != test_id:
                row['status'] = 'ownership_mismatch'
                raise ValueError('ownership_mismatch')
            require((kind == 'container' and value.get('Name') == '/' + name) or
                    (kind == 'image' and name in (value.get('RepoTags') or [])), 'resource_identity_mismatch')
            row['ownership_verified'] = True
            # Exact resource names only; never prune or remove an unverified object.
            row['phase'] = 'removal'
            command(['rm', '-f', '-v', name] if kind == 'container' else ['image', 'rm', name])
            row['phase'] = 'post_removal'
            require(name not in inventory(kind, command), 'post_removal_unconfirmed')
            row.update(status='removed', absence_verified=True)
        success = True
    except (ValueError, TypeError, KeyError) as error:
        # Raw Docker errors/inspection data are deliberately never serialized.
        allowed = {'docker_api_unavailable', 'docker_command_failed', 'docker_output_unbounded',
            'docker_inventory_invalid', 'inspection_invalid', 'ownership_mismatch',
            'resource_identity_mismatch', 'post_removal_unconfirmed'}
        row['error_category'] = str(error) if str(error) in allowed else 'invalid_response'
        if row['status'] != 'ownership_mismatch':
            row['status'] = 'failed' if row['phase'] == 'removal' else 'unknown'
    return dict(exit_code=0 if success else 1, owned_containers_and_tester_removed=success,
        scope='exact_names_and_matching_ownership_labels', resources=rows,
        seconds=round(time.monotonic()-start, 3))

def main():
    directory = Path(os.environ['EVIDENCE_DIR'])
    mode = sys.argv[1]
    if mode == '--cleanup':
        result = cleanup_resources(os.environ['TEST_ID'])
        (directory / 'cleanup.json').write_text(json.dumps(result))
        return result['exit_code']
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, timeout=5).strip()
    if mode == '--sources':
        (directory / 'sources.json').write_text(json.dumps({'commit': sha, 'sha256': source_hashes(Path('.'))}))
    else:
        tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', '--', *SOURCES],
            capture_output=True, timeout=5)
        unchanged = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *SOURCES],
            capture_output=True, timeout=5)
        require(tracked.returncode == 0 and unchanged.returncode == 0, 'checkout_sources_not_committed')
        validate_evidence(directory, Path('.'), sha, os.environ['GITHUB_SHA'], os.environ['TEST_ID'])
        print('PASS: complete candidate, resource, recovery, execution and cleanup evidence.')
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('FAIL: sanitized evidence/cleanup validation; inspect allowlisted evidence.')
        raise SystemExit(1) from None
HOSTPY
exit $?
fi
[[ "$TEST_ID" =~ ^dd_smoke_[0-9]+_[0-9]+$ ]]
test "$(uname -s)/$(uname -m)" = Linux/x86_64
for tool in docker python3 openssl timeout sudo; do command -v "$tool" >/dev/null; done
export EVIDENCE_DIR="$RUNNER_TEMP/${TEST_ID}_evidence"
mkdir "$EVIDENCE_DIR"
chmod 755 "$EVIDENCE_DIR"
started=$SECONDS
record_exit() {
  result=$?
  trap - EXIT
  sudo chown "$(id -u):$(id -g)" "$EVIDENCE_DIR"
  RESULT="$result" DURATION="$((SECONDS-started))" python3 - <<'PY'
import json, os
from pathlib import Path
Path(os.environ['EVIDENCE_DIR'], 'execution.json').write_text(json.dumps({
    'exit_code': int(os.environ['RESULT']), 'seconds': int(os.environ['DURATION']),
    'complete': os.environ['RESULT'] == '0'}))
PY
  exit "$result"
}
trap record_exit EXIT
# Docker Hub manifest bytes independently verified; native AMD64 manifest pin.
MONGO_IMAGE='mongo:8.0.38-noble@sha256:9b73c38e39783bd21335f603a9f08be60fe385f5c28933ea32bb4a9fa1fd6d23'
python3 - <<'PY'
import shutil, json, os
from pathlib import Path
memory = int(next(x.split()[1] for x in open('/proc/meminfo') if x.startswith('MemAvailable:'))) * 1024
free = shutil.disk_usage('.').free
print(f'Available memory={memory // 2**20} MiB; disk={free // 2**30} GiB')
Path(os.environ['EVIDENCE_DIR'], 'resources.json').write_text(json.dumps({
    'available_memory_bytes': memory, 'free_disk_bytes': free,
    'required_memory_bytes': 4 * 2**30, 'required_disk_bytes': 10 * 2**30}))
assert memory >= 4 * 2**30 and free >= 10 * 2**30, 'Insufficient resources'
PY
work=$(mktemp -d "$RUNNER_TEMP/mongodb-smoke.XXXXXX")
chmod 700 "$work"
# Only these allowlisted sources enter the image build context. No .env or Git data.
mkdir -p "$work/build/backend" "$work/build/experiments/mongodb-smoke"
for file in document_storage.py storage_contract.py storage_schema.py gridfs_storage.py storage_intents.py storage_reconciliation.py pdf_processing.py usage_budgets.py config.py growth_limits.py gridfs_growth.py gridfs_growth_recovery.py policy_readiness.py; do
  cp "backend/$file" "$work/build/backend/"
done
cp experiments/mongodb-smoke/{Dockerfile,smoke.py,lifecycle.py,budgets.py,combined.py} "$work/build/experiments/mongodb-smoke/"
# Public source files must remain readable by the image's nonroot test user.
chmod 644 "$work/build/backend/"*.py "$work/build/experiments/mongodb-smoke/"*
bash "$0" --sources
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
DBPATH_KIB="$available_kib" python3 - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ['EVIDENCE_DIR'], 'resources.json')
r = json.loads(p.read_text())
r.update(dbpath_available_kib=int(os.environ['DBPATH_KIB']), dbpath_required_kib=614400)
p.write_text(json.dumps(r))
PY
if (( available_kib < 614400 )); then
  echo 'Insufficient disposable dbPath space for safe index creation.'
  exit 1
fi
# Extra real transactions and bounded conflict waits; job deadline remains 20m.
sudo chown 10001:10001 "$EVIDENCE_DIR"
timeout --signal=TERM --kill-after=5s 300s docker run --name "${TEST_ID}_test" \
  --label "deepdocs.smoke=$TEST_ID" --network "container:$TEST_ID" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 256m --memory-swap 256m --cpus 1 --pids-limit 64 \
  --tmpfs /tmp:rw,nosuid,noexec,size=16m \
  --mount "type=bind,src=$EVIDENCE_DIR,dst=/evidence" \
  --mount "type=bind,src=$work/app.json,dst=/run/app.json,readonly" \
  --mount "type=bind,src=$work/ca.crt,dst=/run/ca.crt,readonly" "deepdocs-smoke:$TEST_ID"
docker stop --time 30 "$TEST_ID" >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' "$TEST_ID")" = 0
