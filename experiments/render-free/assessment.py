"""Conservative synthetic-workload screening, never deployment approval."""
import math

LIMIT = 512 * 1024**2
REQUIRED_STAGES = {'application_import_idle', 'startup_idle', 'upload',
    'post_local_requests_idle', 'gridfs_lease_drain', 'application_startup', 'model_initialization', 'inference',
    'pdf_extraction_and_chunking', 'indexing', 'rag_request_1', 'rag_request_2',
    'rag_request_3', 'gridfs_adapter_upload', 'gridfs_materialization_and_reprocessing',
    'post_requests_idle', 'application_shutdown'}


def assess(record):
    ends = [e for e in record.get('events', []) if e.get('event') == 'end']
    positive_int = lambda value: type(value) is int and value > 0
    nonnegative = lambda value: type(value) in (int, float) and math.isfinite(value) and value >= 0
    peaks = [record.get('kernel_peak_bytes')] + [e.get('lifetime_peak_bytes') for e in ends]
    peak = max((p for p in peaks if positive_int(p)), default=0)
    names = [e.get('stage') for e in ends]
    required = [e for e in ends if e.get('stage') in REQUIRED_STAGES]
    measurements_valid = (
        positive_int(record.get('kernel_peak_bytes')) and
        REQUIRED_STAGES <= set(names) and len(names) == len(set(names)) and
        all(positive_int(e.get('current_bytes')) and positive_int(e.get('lifetime_peak_bytes'))
            and e['current_bytes'] <= e['lifetime_peak_bytes']
            and nonnegative(e.get('seconds'))
            and isinstance(e.get('cpu_delta'), dict)
            and all(nonnegative(e['cpu_delta'].get(k)) for k in
                    ('usage_usec', 'nr_throttled', 'throttled_usec')) for e in required))
    try:
        memory = {k: int(v) for k, v in (line.split() for line in record['memory_events'].splitlines())}
        cpu = {k: int(v) for k, v in (line.split() for line in record['cpu_stat'].splitlines())}
        measurements_valid = (measurements_valid and all(memory[k] == 0 for k in ('oom', 'oom_kill'))
            and all(cpu[k] >= 0 for k in ('usage_usec', 'nr_throttled', 'throttled_usec')))
    except (KeyError, ValueError, TypeError, AttributeError):
        measurements_valid = False
    expected_limits = {'memory_bytes': 3 * 1024**3 if record.get('calibration') else LIMIT,
                       'swap_bytes': 0, 'cpus': 2 if record.get('calibration') else .1}
    complete = (record.get('complete') is True and record.get('exit_code') == 0
                and record.get('oom_killed') is False and record.get('timed_out') is False
                and record.get('verified_limits') == expected_limits and measurements_valid
                and any(e.get('event') == 'application_complete' for e in record.get('events', []))
                and any(e.get('event') == 'complete' for e in record.get('events', [])))
    latency_ok = all(nonnegative(e.get('seconds')) and e['seconds'] < 60
                     for e in required if e['stage'].startswith('rag_request_'))
    if record.get('calibration'):
        verdict = 'calibration_only'
    elif not complete or not peak:
        verdict = 'incomplete_or_failed; unsuitable_for_approval'
    elif peak > LIMIT * .8:
        verdict = 'insufficient_20_percent_headroom'
    elif not latency_ok:
        verdict = 'request_latency_exceeds_frontend_budget'
    else:
        verdict = 'synthetic_screen_pass; live_full_backend_validation_still_required'
    return {'screen': verdict, 'peak_bytes': peak or None,
            'headroom_bytes': LIMIT - peak if peak and not record.get('calibration') else None,
            'required_headroom_bytes': LIMIT * .2,
            'cpu_stat': record.get('cpu_stat'), 'memory_events': record.get('memory_events')}
