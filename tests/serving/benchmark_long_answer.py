#!/usr/bin/env python3
"""Counterbalanced long-answer diagnostics; unchanged caller schema, synthetic data.

Run next to probe_constrained.py on an isolated model endpoint. This separates
source content, test-only inference policy, and native thinking control. It is
not an application or clinical accuracy test. Each subprocess runs one F case;
raw requests and responses remain available for independent semantic review.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


CONDITIONS = [
    ('sparse-default', False, False, None),
    ('complete-default', True, False, None),
    ('sparse-policy-default', False, True, None),
    ('complete-policy-default', True, True, None),
    ('sparse-8192', False, False, 8192),
    ('complete-policy-8192', True, True, 8192),
    ('complete-policy-512', True, True, 512),
    ('complete-policy-off', True, True, 'off'),
]


def schedule(repeats):
    """Rotate/reverse condition order to distribute startup and cache effects."""
    for repeat in range(repeats):
        order = CONDITIONS[repeat % len(CONDITIONS):] + CONDITIONS[:repeat % len(CONDITIONS)]
        if repeat % 2:
            order = list(reversed(order))
        for condition in order:
            yield repeat + 1, condition


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    manifest = {'synthetic_only': True, 'max_tokens': 12000, 'repeats': args.repeats,
                'schema': 'final_answer_schema.json (unchanged)',
                'conditions': CONDITIONS, 'schedule': list(schedule(args.repeats)),
                'sha256': {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                           for name in ('probe_constrained.py', 'benchmark_long_answer.py',
                                        'final_answer_schema.json', 'grounded_sources.json',
                                        'source_grounding_policy.txt')}}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    results = []
    for repeat, (name, complete, policy, budget) in schedule(args.repeats):
        capture = args.output / f'{repeat:02d}-{name}'
        env = os.environ.copy()
        for field in ('SOURCE_FIXTURE', 'SYSTEM_POLICY', 'THINKING', 'THINKING_BUDGET'):
            env.pop(field, None)
        env.update(CASES='F', RUNS='1', MAX_TOKENS='12000', CAPTURE_DIR=str(capture))
        if complete:
            env['SOURCE_FIXTURE'] = str(here / 'grounded_sources.json')
        if policy:
            env['SYSTEM_POLICY'] = str(here / 'source_grounding_policy.txt')
        if budget == 'off':
            env['THINKING'] = 'off'
        elif budget is not None:
            env['THINKING_BUDGET'] = str(budget)
        started = time.monotonic()
        try:
            run = subprocess.run([sys.executable, str(here / 'probe_constrained.py'),
                                  str(here / 'final_answer_schema.json')],
                                 env=env, capture_output=True, text=True, timeout=180)
            capture.mkdir(exist_ok=True)
            (capture / 'probe.log').write_text(run.stdout + run.stderr)
            result = {'repeat': repeat, 'condition': name, 'requested_output_pass': run.returncode == 0,
                      'wall_seconds_including_client_start': time.monotonic() - started}
            path = capture / 'F-1.json'
            if path.exists():
                response = json.loads(path.read_text())
                choice = response['choices'][0]
                result.update(usage=response.get('usage'), finish_reason=choice.get('finish_reason'))
                calls = choice['message'].get('tool_calls') or []
                if len(calls) == 1:
                    value = json.loads(calls[0]['function']['arguments'])
                    if not isinstance(value, dict):
                        raise ValueError('Tool arguments are not an object')
                    answer = value.get('answer', value)
                    if not isinstance(answer, dict):
                        raise ValueError('Answer is not an object')
                    result.update(blocks=len(answer.get('evidence_review', [])),
                                  followups=len(answer.get('followups', [])))
            timing = capture / 'F-1-timing.json'
            if timing.exists():
                result.update(json.loads(timing.read_text()))
            results.append(result)
        except (subprocess.TimeoutExpired, KeyError, ValueError) as exc:
            results.append({'repeat': repeat, 'condition': name, 'requested_output_pass': False,
                            'error': type(exc).__name__})
        (args.output / 'results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(results[-1]), flush=True)
    summary = []
    for name, *_ in CONDITIONS:
        rows = [r for r in results if r['condition'] == name]
        seconds = [r['request_seconds'] for r in rows if 'request_seconds' in r]
        summary.append({'condition': name, 'passed': sum(r['requested_output_pass'] for r in rows),
                        'total': len(rows), 'mean_seconds': statistics.mean(seconds) if seconds else None})
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return int(any(not r['requested_output_pass'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
