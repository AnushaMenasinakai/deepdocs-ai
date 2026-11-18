"""Fail-closed Gemini resource screen. No deployment approval from simulation."""
from assessment import LIMIT
from gemini_application_probe import STAGES
import math


def assess_gemini(record):
    events = record.get('events', [])
    ends = [e for e in events if e.get('event') == 'end']
    names = [e.get('stage') for e in ends]
    valid = (set(names) == STAGES and len(names) == len(STAGES)
        and record.get('verified_limits') == {'memory_bytes': LIMIT, 'swap_bytes': 0, 'cpus': .1}
        and record.get('complete') is True and record.get('exit_code') == 0
        and record.get('oom_killed') is False and record.get('timed_out') is False)
    peaks = [record.get('kernel_peak_bytes')]
    for e in ends:
        peaks.append(e.get('lifetime_peak_bytes'))
        valid = valid and all(type(e.get(k)) is int and e[k] > 0 for k in ('current_bytes', 'lifetime_peak_bytes'))
        valid = valid and type(e.get('seconds')) in (float, int) and math.isfinite(e['seconds']) and 0 <= e['seconds'] < 60
        valid = valid and isinstance(e.get('cpu_delta'), dict) and all(type(e['cpu_delta'].get(k)) is int and e['cpu_delta'][k] >= 0 for k in ('usage_usec', 'nr_throttled', 'throttled_usec'))
        if type(e.get('current_bytes')) is int and type(e.get('lifetime_peak_bytes')) is int:
            valid = valid and e['current_bytes'] <= e['lifetime_peak_bytes']
    valid = valid and all(type(p) is int and p > 0 for p in peaks)
    try:
        memory = dict(line.split() for line in record['memory_events'].splitlines())
        valid = valid and all(int(memory[k]) == 0 for k in ('oom', 'oom_kill'))
    except (KeyError, ValueError, AttributeError): valid = False
    valid = valid and any(e.get('event') == 'safeguards' and all(e.get(k) is True for k in ('private_access','budgets','growth','gridfs','no_local_ml')) and e.get('embedding_dimension') == 768 for e in events)
    valid = valid and sum(e.get('event') == 'request' for e in events) >= 20
    valid = valid and any(e.get('event') == 'complete' for e in events)
    valid = valid and all(e.get('status') in (200,201) for e in events if e.get('event') == 'request')
    valid = valid and any(e.get('event') == 'application_complete' for e in events) and not any(e.get('event')=='failed' for e in events)
    peak = max((p for p in peaks if type(p) is int and p > 0), default=0)
    idle = {e['stage']:e.get('current_bytes') for e in ends if e.get('stage','').startswith('idle_')}
    baseline = next((e.get('current_bytes') for e in ends if e.get('stage')=='startup_idle'), None)
    growth = None
    if valid:
        growth = idle['idle_3'] - idle['idle_1']
        # Warm-up is recorded separately; reject >8 MiB retained growth over repeats,
        # or three steadily increasing samples totaling more than 1 MiB.
        stable = growth <= 8*2**20 and not (idle['idle_1'] < idle['idle_2'] < idle['idle_3'] and growth > 2**20)
    else: stable = False
    verdict = ('incomplete_or_failed' if not valid else 'insufficient_headroom' if peak > LIMIT*.8
               else 'retained_memory_growth' if not stable else 'synthetic_screen_pass')
    return {'screen':verdict, 'peak_bytes':peak or None, 'headroom_bytes':LIMIT-peak if peak else None,
            'required_headroom_bytes':LIMIT*.2, 'idle_bytes':idle, 'startup_idle_bytes':baseline,
            'repeated_growth_bytes':growth, 'deployment_approved':False}
