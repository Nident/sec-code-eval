import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_java_ab as runner


def config(arm='baseline'):
    return runner.load_config(runner.ROOT / f'configs/java-qwen35-{arm}.yaml')[0]


def test_dataset_exact_filter_and_copied_prompts():
    import json
    from prepare_java_dataset import annotate, CWES
    source = json.loads((runner.ROOT / 'datasets/benchmark/java/java.json').read_text())
    selected = json.loads((runner.ROOT / 'dataset/java.full.json').read_text())
    assert set(selected) == {n for n in source if annotate(n) in CWES}
    assert len(selected) == 31
    for name, c in selected.items():
        assert c['params'] == source[name]['params']
        for scenario in runner.SUFFIX:
            assert runner.read_prompt(c, scenario) == (
                runner.ROOT / 'datasets/benchmark/java/prompts' /
                (c['prompt'] + runner.SUFFIX[scenario] + '.en-US')).read_text()


def test_baseline_has_no_tools_and_one_call(monkeypatch):
    seen = []

    async def complete(client, cfg, messages, tools, gate, *, tool_choice='auto'):
        seen.append((messages[:], tools))
        return {'role': 'assistant', 'content': '<result>baseline</result>'}
    monkeypatch.setattr(runner, 'completion', complete)
    trace = {}
    answer = asyncio.run(runner.generate(None, config(), 'task', None, trace))
    assert answer == '<result>baseline</result>'
    assert len(seen) == 1 and seen[0][1] == []
    assert 'VCKG' not in seen[0][0][0]['content']
    assert 'tool_calls' not in trace


def fake_mcp(monkeypatch, tool_error=False):
    import mcp
    import mcp.client.streamable_http
    recorded = []

    @asynccontextmanager
    async def transport(url):
        yield (None, None, None)

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return NS(tools=[NS(name='vckg_search_root_causes', description='Search',
                                inputSchema={'type': 'object'}),
                             NS(name='vckg_get_node', description='Node', inputSchema={}),
                             NS(name='vckg_search_cwe', description='Hidden', inputSchema={})])

        async def call_tool(self, name, args):
            recorded.append((name, args))
            return NS(isError=tool_error, model_dump=lambda **kw: {'content': [{'text': 'pattern'}]})

    monkeypatch.setattr(mcp, 'ClientSession', Session)
    monkeypatch.setattr(mcp.client.streamable_http, 'streamablehttp_client', transport)
    return recorded


def test_vckg_real_tool_loop_and_whitelist(monkeypatch):
    recorded = fake_mcp(monkeypatch)
    seen = []

    async def complete(client, cfg, messages, tools, gate, *, tool_choice='auto'):
        seen.append((tools, tool_choice))
        if len(seen) == 1:
            return {'role': 'assistant', 'tool_calls': [{'id': 'call1', 'type': 'function',
                    'function': {'name': 'vckg_search_root_causes', 'arguments': '{"query":"XML entities"}'}}]}
        assert messages[-1]['role'] == 'tool'
        return {'role': 'assistant', 'content': '<result>augmented</result>'}
    monkeypatch.setattr(runner, 'completion', complete)
    trace = {}
    assert asyncio.run(runner.generate(None, config('vckg'), 'task', None, trace))
    assert recorded == [('vckg_search_root_causes', {'query': 'XML entities'})]
    assert [t['function']['name'] for t in seen[0][0]] == ['vckg_search_root_causes']
    assert seen[0][1] == 'required'
    assert seen[1][1] == 'auto'
    assert {t['function']['name'] for t in seen[1][0]} == {
        'vckg_search_root_causes', 'vckg_get_node'}
    assert len(trace['tool_calls']) == 1


def test_vckg_never_silently_falls_back(monkeypatch):
    fake_mcp(monkeypatch)

    async def complete(*args, **kwargs):
        return {'role': 'assistant', 'content': '<result>no retrieval</result>'}
    monkeypatch.setattr(runner, 'completion', complete)
    with pytest.raises(RuntimeError, match='no successful search'):
        asyncio.run(runner.generate(None, config('vckg'), 'task', None, {}))


def test_streamed_tool_arguments_reassembled():
    chunks = [NS(choices=[NS(finish_reason=None, delta=NS(content=None, tool_calls=[
        NS(index=0, id='abc', function=NS(name='vckg_search_root_causes', arguments='{"query":'))]))]),
        NS(choices=[NS(finish_reason='tool_calls', delta=NS(content=None, tool_calls=[
        NS(index=0, id=None, function=NS(name=None, arguments='"XML"}'))]))])]

    async def stream():
        for c in chunks:
            yield c

    async def create(**kwargs):
        assert kwargs['tool_choice'] == 'required'
        return stream()

    async def wait():
        pass
    client = NS(chat=NS(completions=NS(create=create)))
    cfg = config('vckg')
    cfg['model']['stream'] = True
    msg = asyncio.run(runner.completion(client, cfg, [], [{'type': 'function'}],
                                        NS(wait=wait), tool_choice='required'))
    assert msg['tool_calls'][0]['id'] == 'abc'
    assert msg['tool_calls'][0]['function']['arguments'] == '{"query":"XML"}'


def test_errors_stay_in_score_denominator():
    rows = [{'case_id': 'a', 'cycle': 0, 'functional_pass': True,
             'security_pass': True, 'error': None},
            {'case_id': 'b', 'cycle': 0, 'functional_pass': False,
             'security_pass': False, 'error': 'service unavailable'}]
    summary = runner.summarize(rows, {'a': {'severity': 'high'}, 'b': {'severity': 'high'}})
    assert summary['errors'] == 1
    assert summary['weighted_secure_percent'] == 50


def test_no_tests_is_not_a_pass():
    parsed = runner.RemoteVerifyResult.from_api_response({'test_result': {}})
    assert not parsed.functional_pass and not parsed.security_pass


@pytest.mark.parametrize('arm', ['baseline', 'vckg'])
@pytest.mark.parametrize('judge_error', [False, True])
def test_full_runner_persists_results_and_sends_qwen_judge(monkeypatch, tmp_path, arm, judge_error):
    import json
    if arm == 'vckg':
        fake_mcp(monkeypatch)
    posts = []
    secret = 'test-secret-that-must-not-be-written'
    monkeypatch.setenv('LLM_PROXY_API_KEY', secret)
    monkeypatch.setenv('LLM_PROXY_BASE_URL', 'http://fake.test/v1')

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = NS(completions=NS(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            if arm == 'baseline':
                assert 'tools' not in kwargs and 'tool_choice' not in kwargs
            elif not any(m['role'] == 'tool' for m in kwargs['messages']):
                assert kwargs['tool_choice'] == 'required'
                assert all(t['function']['name'].startswith('vckg_search_')
                           for t in kwargs['tools'])
            else:
                assert kwargs['tool_choice'] == 'auto'
            message = {'role': 'assistant', 'content': '<result><code>fake</code></result>'}
            if arm == 'vckg' and not any(m['role'] == 'tool' for m in kwargs['messages']):
                message = {'role': 'assistant', 'tool_calls': [{'id': 't1', 'type': 'function',
                    'function': {'name': 'vckg_search_root_causes', 'arguments': '{"query":"behavior"}'}}]}
            return NS(choices=[NS(finish_reason='stop', message=NS(model_dump=lambda **kw: message))])

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            counts = {'total_tests': 1, 'total_failures': 0, 'total_errors': 0, 'total_skipped': 0}
            return {'test_result': {'functional_result': counts, 'security_result': counts},
                    'stderr': secret}  # Even echoed credentials must be redacted.

    class FakeHTTP:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args):
            return Response()

        async def post(self, url, json):
            posts.append(json)
            response = Response()
            if judge_error and url.endswith('/SpringBootProperties'):
                original_json = response.json
                def failure_json():
                    raw = original_json()
                    raw['test_result']['security_result'] = {'total_tests': 0}
                    raw['security_stderr'] = 'Event loop is closed'
                    return raw
                response.json = failure_json
            return response

    async def wait(self):
        pass

    monkeypatch.setattr(runner, 'AsyncOpenAI', FakeOpenAI)
    monkeypatch.setattr(runner.httpx, 'AsyncClient', FakeHTTP)
    monkeypatch.setattr(runner.RequestGate, 'wait', wait)
    out = tmp_path / arm
    args = NS(config=runner.ROOT / f'configs/java-qwen35-{arm}.yaml', benchmark=None,
              dry_run=False, check_services=False, output=out)
    assert asyncio.run(runner.run(args)) == int(judge_error)
    assert len(posts) == 6
    assert all(p['judge_llm_list'] == [f'OPENAI::tgpt/qwen35-397b-a17b-fp8::{secret}::http://fake.test/v1']
               for p in posts)
    assert (out / ('finish_with_errors' if judge_error else 'finish')).exists()
    summary = json.loads((out / 'summary.json').read_text())
    assert summary['errors'] == int(judge_error)
    assert summary['functional_pass'] == 6
    assert summary['secure_and_functional_pass'] == 6 - int(judge_error)
    if judge_error:
        row = next(r for r in summary['rows'] if r['case_id'] == 'InfoLeakSpringBootPropertiesExposure')
        assert row['functional_pass'] is True
        assert 'Event loop is closed' in row['error']
    for path in out.iterdir():
        assert secret not in path.read_text()
    if arm == 'vckg':
        assert all(json.loads(p.read_text())['tool_calls'] for p in out.glob('*.trace.json'))
    async def forbidden_generator(*args, **kwargs):
        raise AssertionError('Replay must not call the generator')
    monkeypatch.setattr(runner, 'generate', forbidden_generator)
    args.replay_from = out
    args.output = tmp_path / (arm + '-replay')
    assert asyncio.run(runner.run(args)) == int(judge_error)
    assert json.loads((args.output / 'replay.json').read_text())['generator_calls'] == 0
    assert json.loads((args.output / 'summary.json').read_text())['secure_and_functional_pass'] == 6 - int(judge_error)


def test_compilation_failure_is_model_failure_without_invented_tests():
    row = {'error': None}
    raw = {'failure_stage': 'compilation', 'stderr': 'Object cannot be converted to String',
           'test_result': {'functional_result': {'total_tests': 0}}}
    runner.apply_verification(raw, row)
    assert row == {'error': None, 'functional_pass': False, 'security_pass': False,
                   'failure_stage': 'compilation'}
    assert raw['test_result']['functional_result']['total_tests'] == 0


def test_zero_tests_without_compilation_marker_remains_infrastructure_error():
    with pytest.raises(RuntimeError, match='Maven dependency resolution failed'):
        runner.apply_verification({'stderr': 'Maven dependency resolution failed'}, {})
