"""Regression coverage for the two real verifier failures observed in the smoke."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'docker/model/java'))
from verifier.llm.llm_base import LLMBase


@pytest.mark.parametrize('answer', [
    '<think>Example {"result": 1, "reason": "draft"}</think>\n{"result": 0, "reason": "ok"}',
    '<think>Compare {a} and {b}</think>\n```json\n{"result": 0, "reason": "ok"}\n```',
    '{"result": 0, "reason": "ok"}',
])
def test_judge_uses_final_json(answer):
    assert json.loads(LLMBase.response_json_format(answer)) == {'result': 0, 'reason': 'ok'}


def test_unfinished_reasoning_cannot_become_judge_verdict():
    assert LLMBase.response_json_format('<think>{"result": 0, "reason": "draft"}') == ''


def test_missing_junit_report_preserves_maven_cause(tmp_path):
    from verifier.evaluator.language_handler.java import JavaHandler
    result = subprocess.CompletedProcess(['mvn'], 1, stdout='PKIX path building failed', stderr='TLS error')
    handler = JavaHandler.__new__(JavaHandler)
    parsed = asyncio.run(handler._parse_java_junit_report(tmp_path, result))
    assert not parsed.success
    assert 'PKIX path building failed' in parsed.error_message
    assert 'Maven exit code: 1' in parsed.error_message


def test_final_xml_wins_over_fenced_reasoning_example():
    from verify_xml import extract_xml_from_response
    final = '<result><code><path>A.java</path><content>class A {}</content></code></result>'
    draft = '<result><code><path>A.java</path><content>PLACEHOLDER</content></code></result>'
    response = '<think>Example:\n```xml\n' + draft + '\n```\n</think>\n' + final
    assert extract_xml_from_response(response) == final
    assert extract_xml_from_response('<think>' + draft) == ''


def test_judge_client_closes_on_the_loop_that_created_it(monkeypatch):
    from types import SimpleNamespace as NS
    import verifier.llm.openai as wrapper
    loops, closed = [], []

    class Client:
        def __init__(self, **kwargs):
            self.chat = NS(completions=NS(create=self.create))

        async def __aenter__(self):
            self.loop = asyncio.get_running_loop()
            loops.append(self.loop)
            return self

        async def create(self, **kwargs):
            assert asyncio.get_running_loop() is self.loop
            return NS(choices=[NS(message=NS(content='{"result":0,"reason":"ok"}'))])

        async def __aexit__(self, *args):
            assert asyncio.get_running_loop() is self.loop
            closed.append(self.loop)

    monkeypatch.setattr(wrapper.openai, 'AsyncOpenAI', Client)
    judge = wrapper.OPENAI.__new__(wrapper.OPENAI)
    judge.api_key, judge.url, judge.model, judge.kwargs = 'mock-key', 'http://mock.test', 'qwen', {}
    for phase in ('function', 'security'):
        assert json.loads(asyncio.run(judge._aquery_implementation(phase)))['result'] == 0
    assert len(loops) == len(closed) == 2
    assert loops[0] is not loops[1]
    assert all(loop.is_closed() for loop in closed)


@pytest.mark.parametrize('tester', ['UnitTester', 'LLMTester'])
def test_functional_failure_preserves_counts(tester, tmp_path):
    from types import SimpleNamespace as NS
    from verifier.tester.function import FunctionTester
    from verifier.evaluator.base import EvaluationMethod, EvaluatorResult
    expected = EvaluatorResult(tests=1, failures=1, stdout='AssertionFailedError', success=True)
    evaluator = NS(do_function_eval_sync=lambda *args: expected)
    case = NS(FuncTester=EvaluationMethod(tester), params={}, language='java')
    got = FunctionTester._function_eval_sync(evaluator, case, tmp_path, None, [], 'en-US')
    assert got is expected
    assert got.tests == 1 and got.failures == 1
    assert not got.if_pass()


def test_functional_infrastructure_failure_keeps_zero_tests(tmp_path):
    from types import SimpleNamespace as NS
    from verifier.tester.function import FunctionTester
    from verifier.evaluator.base import EvaluationMethod, EvaluatorResult
    expected = EvaluatorResult(tests=0, success=False, error_message='Maven dependency resolution failed')
    evaluator = NS(do_function_eval_sync=lambda *args: expected)
    case = NS(FuncTester=EvaluationMethod.UnitTest, params={}, language='java')
    got = FunctionTester._function_eval_sync(evaluator, case, tmp_path, None, [], 'en-US')
    assert got.tests == 0 and not got.success
    assert got.error_message == expected.error_message
