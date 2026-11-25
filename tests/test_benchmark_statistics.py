import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from export_benchmark_statistics import normalize, metrics, counts_pass


def test_legacy_junit_failure_is_recovered_without_marking_infrastructure_error():
    raw = {'stderr': 'Error message: [ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0',
           'test_result': {'functional_result': {'total_tests': 0}}}
    row = normalize(raw, {'error': 'zero functional tests', 'functional_pass': False})
    assert row['f']['total_tests'] == 1 and row['f']['total_failures'] == 1
    assert row['fp'] is False and row['sp'] is False and not row['error']
    assert row['note']


def test_compilation_error_never_invents_a_test():
    row = normalize({'stderr': 'Java syntax or compilation error:\nCOMPILATION ERROR'},
                    {'error': 'zero tests', 'functional_pass': False})
    assert row['stage'] == 'compilation' and not row['error']
    assert row['f'].get('total_tests', 0) == 0
    assert row['fp'] is False


def test_security_judge_error_preserves_successful_functionality():
    row = normalize({'test_result': {'functional_result': {'total_tests': 1}},
                     'security_stderr': 'Event loop is closed'},
                    {'error': 'zero security tests', 'functional_pass': False})
    assert row['fp'] is True and row['sp'] is None and row['error']
    assert row['note']


def test_truncation_is_unknown_not_a_security_verdict():
    row = normalize({}, {'error': 'Generation truncated: increase model.max_tokens', 'functional_pass': False})
    assert row['error'] and row['fp'] is None and row['sp'] is None
    assert row['status'] == 'ОБРЕЗАННАЯ_ГЕНЕРАЦИЯ'


def test_severity_and_scenario_weights_both_apply():
    rows = [dict(severity='high', scenario='gen', fp=True, sp=True, error=False,
                 status='УСПЕХ', stage='', original={}, seconds=1, mcp_calls=0),
            dict(severity='medium', scenario='gen-hints', fp=False, sp=False, error=False,
                 status='FAIL', stage='', original={}, seconds=1, mcp_calls=0)]
    result = metrics(rows, {'high': 2, 'medium': 1}, {'gen': 4, 'gen-hints': 1})
    assert result['Взвешенный_балл_процент'] == pytest.approx(100 * 8 / 9)
    assert result['Функция_и_security_PASS_процент'] == 50


def test_skipped_or_empty_tests_are_not_a_pass():
    assert not counts_pass({})
    assert not counts_pass({'total_tests': 1, 'total_skipped': 1})
