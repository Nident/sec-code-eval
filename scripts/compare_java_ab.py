#!/usr/bin/env python3
"""Compare two complete, matching quick runs. Errors are never dropped."""
import argparse
import json
from pathlib import Path


def compare(baseline, treatment):
    def read(folder, name):
        return json.loads((folder / name).read_text())
    a, b = read(baseline, 'summary.json'), read(treatment, 'summary.json')
    ca, cb = read(baseline, 'config.json'), read(treatment, 'config.json')
    if ca['arm'] != 'baseline' or cb['arm'] != 'vckg':
        raise ValueError('Expected baseline directory followed by vckg directory')
    for key in ('model', 'judge', 'scenario', 'cycles', 'concurrency', 'rpm_limit'):
        if ca[key] != cb[key]:
            raise ValueError(f'Configurations differ: {key}')
    if read(baseline, 'provenance.json') != read(treatment, 'provenance.json'):
        raise ValueError('Datasets, prompts, scenarios or cycles differ')
    ra = {(r['case_id'], r['cycle']): r for r in a['rows']}
    rb = {(r['case_id'], r['cycle']): r for r in b['rows']}
    if ra.keys() != rb.keys():
        raise ValueError('Case/cycle sets differ')
    return {'baseline': {k: v for k, v in a.items() if k != 'rows'},
            'vckg': {k: v for k, v in b.items() if k != 'rows'},
            'weighted_delta_percentage_points': b['weighted_secure_percent'] - a['weighted_secure_percent'],
            'improved': [list(k) for k in ra if not ra[k]['security_pass'] and rb[k]['security_pass']],
            'regressed': [list(k) for k in ra if ra[k]['security_pass'] and not rb[k]['security_pass']],
            'interpretation': 'Smoke comparison only; errors confound model quality' if a['errors'] or b['errors']
                              else 'Single-cycle smoke comparison; not statistical evidence of improvement'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('vckg', type=Path)
    args = p.parse_args()
    print(json.dumps(compare(args.baseline, args.vckg), ensure_ascii=False, indent=2))
