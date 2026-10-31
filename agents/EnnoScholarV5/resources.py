"""Disjoint commitments from the canonical V5 call ledger; no provider or database IO."""
from datetime import datetime, timezone

ACTIVE = {'reserved', 'uncertain'}


def balance(data):
    result = dict(confirmed_usd=0., reserved_usd=0., uncertain_usd=0., input_tokens=0,
        output_tokens=0, reserved_tokens=0, uncertain_tokens=0, tools=int(data.get('tools', 0)), web=int(data.get('web', 0)))
    for e in data.get('entries', []):
        status = e.get('status')
        if status == 'settled':
            result['confirmed_usd'] += float(e.get('cost_usd', 0))
            result['input_tokens'] += int(e.get('input_tokens', 0))
            result['output_tokens'] += int(e.get('output_tokens', 0))
        elif status in ACTIVE:
            result[status+'_usd'] += float(e.get('reserved_usd', 0))
            result[status+'_tokens'] += int(e.get('reserved_tokens', 0))
            result['web'] += int(e.get('web', False))
            result['tools'] += int(e.get('web', False))
    result['confirmed_tokens'] = result['input_tokens']+result['output_tokens']
    result['tokens'] = result['confirmed_tokens']+result['reserved_tokens']+result['uncertain_tokens']
    result['committed_usd'] = sum(result[k] for k in ('confirmed_usd', 'reserved_usd', 'uncertain_usd'))
    return result


def combine(values):
    total = balance({})
    for value in values:
        for key in total:
            total[key] += value.get(key, 0)
    return total


def remaining_seconds(mission, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(mission['started_at'].replace('Z', '+00:00'))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0., float(mission['max_seconds'])-(now-started).total_seconds())
    except (ValueError, TypeError, KeyError):
        return 0.  # Unknown deadlines never grant a fresh duration.


def apply_mission_usage(job, usage):
    job['resources'] = dict(usage)
    job.setdefault('mission', {}).update({k: usage[k] for k in
        ('tokens', 'confirmed_tokens', 'reserved_tokens', 'uncertain_tokens', 'tools', 'web')})
    return job
