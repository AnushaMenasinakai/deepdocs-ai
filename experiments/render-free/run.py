"""Host-side bounded Docker experiment; stdlib only. Never runs on developer laptop."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ['RUNNER_TEMP']) / 'render-experiment'
OUT.mkdir(exist_ok=False)
PREFIX = 'deepdocs-render-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
IMAGE = os.environ['EXPERIMENT_IMAGE']

def docker(*args, check=True):
    return subprocess.run(['docker', *args], text=True, capture_output=True, timeout=20, check=check)

def run(runtime, calibration=False):
    label = runtime + ('-calibration' if calibration else '-limited')
    name = PREFIX + '-' + label
    record = {'runtime': runtime, 'calibration': calibration, 'samples': []}
    try:
        docker('run', '-d', '--name', name, '--network', 'none', '--read-only', '--no-healthcheck',
               '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--memory', '3g' if calibration else '512m',
               '--memory-swap', '3g' if calibration else '512m',
               '--cpus', '2' if calibration else '0.1', '--pids-limit', '128',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m,mode=1777',
               '--tmpfs', '/tmp/suite:rw,noexec,nosuid,size=64m,mode=1777',
               '--mount', f'type=bind,src={ROOT / "backend/tests"},dst=/tmp/suite/backend/tests,readonly',
               '-e', 'PYTHONPATH=/app/backend:/tmp/suite/backend/tests',
               '-e', 'TOKENIZERS_PARALLELISM=false', IMAGE, runtime)
        info = json.loads(docker('inspect', name).stdout)[0]
        pid = info['State']['Pid']
        group = Path(f'/proc/{pid}/cgroup').read_text().strip()
        assert group.startswith('0::'), 'cgroup v2 required for measurements'
        cg = Path('/sys/fs/cgroup') / group.split('::', 1)[1].lstrip('/')
        assert (cg / 'memory.max').read_text().strip() == str((3 * 1024 if calibration else 512) * 1024**2)
        assert (cg / 'memory.swap.max').read_text().strip() == '0'
        quota, period = map(int, (cg / 'cpu.max').read_text().split())
        assert quota / period == (2 if calibration else 0.1)
        deadline = time.monotonic() + (300 if calibration else 480)
        record['timed_out'] = False
        while time.monotonic() < deadline:
            try:
                record['samples'].append({'time': time.monotonic(),
                    'bytes': int((cg / 'memory.current').read_text())})
                record['kernel_peak_bytes'] = int((cg / 'memory.peak').read_text())
                record['memory_events'] = (cg / 'memory.events').read_text()
                record['cpu_stat'] = (cg / 'cpu.stat').read_text()
            except FileNotFoundError:
                break
            if len(record['samples']) % 20 == 0:
                if docker('inspect', '-f', '{{.State.Running}}', name).stdout.strip() != 'true':
                    break
            time.sleep(0.05)
        else:
            record['timed_out'] = True
            docker('stop', '-t', '10', name, check=False)
        state = json.loads(docker('inspect', name).stdout)[0]['State']
        record['exit_code'], record['oom_killed'] = state['ExitCode'], state['OOMKilled']
        logs = docker('logs', name, check=False)
        (OUT / (label + '.log')).write_text(logs.stdout + logs.stderr)
        record['events'] = []
        for line in logs.stdout.splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict) and 'event' in value:
                    record['events'].append(value)
            except ValueError:
                pass
        for end in [e for e in record['events'] if e['event'] == 'end']:
            start = next(e for e in record['events'] if e['event'] == 'start' and e['stage'] == end['stage'])
            samples = [s['bytes'] for s in record['samples'] if start['time'] <= s['time'] <= end['time']]
            end['sampled_stage_peak_bytes'] = max(samples, default=None)
        record['complete'] = any(e['event'] == 'complete' for e in record['events']) and state['ExitCode'] == 0
        return record
    finally:
        docker('rm', '-f', name, check=False)
        (OUT / (label + '.json')).write_text(json.dumps(record, indent=2))

def compare(a, b):
    import math
    def vectors(r):
        return next(e for e in r['events'] if e['event'] == 'vectors')
    av, bv = vectors(a), vectors(b)
    x, y = av['values'], bv['values']
    assert len(x) == len(y) and all(len(v) == 384 for v in x + y)
    assert all(math.isfinite(z) for v in x + y for z in v)
    dot = lambda u, v: sum(i*j for i, j in zip(u, v))
    norm_error = max(abs(math.sqrt(dot(v, v))-1) for v in x+y)
    count = av['corpus_count']
    rows = []
    for i in range(count, len(x)):
        sx, sy = [dot(x[i], v) for v in x[:count]], [dot(y[i], v) for v in y[:count]]
        rank = lambda scores: sorted(range(count), key=lambda j: -scores[j])[:5]
        rows.append({'question_index': i-count, 'torch_scores': sx, 'onnx_scores': sy,
                     'top5_equal': rank(sx) == rank(sy),
                     'primary_gate_equal': (max(sx) >= .5) == (max(sy) >= .5),
                     'secondary_membership_equal': [s >= .3 for s in sx] == [s >= .3 for s in sy],
                     'minimum_threshold_margin': min(abs(s-t) for s in sx+sy for t in (.3,.5))})
    return {'dimension':384, 'max_norm_error':norm_error,
            'minimum_paired_cosine':min(dot(u,v)/math.sqrt(dot(u,u)*dot(v,v)) for u,v in zip(x,y)),
            'queries':rows, 'scope':'synthetic corpus only; not migration approval'}

if __name__ == '__main__':
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub runners only'
    results = []
    try:
        # Calibration is explicitly outside the Render envelope. It establishes the reference
        # even when the unchanged PyTorch implementation OOMs in the limited run.
        for calibration in (True, False):
            for runtime in ('torch', 'onnx'):
                results.append(run(runtime, calibration))
        if all(r['complete'] for r in results[:2]):
            (OUT / 'compatibility.json').write_text(json.dumps(compare(*results[:2]), indent=2))
    finally:
        summary = [{'runtime':r['runtime'], 'calibration':r['calibration'], 'complete':r.get('complete'),
                    'oom':r.get('oom_killed'), 'timeout':r.get('timed_out'),
                    'peak_bytes':r.get('kernel_peak_bytes'),
                    'stages':[e for e in r.get('events',[]) if e['event']=='end']} for r in results]
        print(json.dumps(summary, indent=2))
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('Resource experiment (not a deployment approval)\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
        (OUT / 'summary.json').write_text(json.dumps(summary,indent=2))
