import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_java_experiments as runner


def verification(stage: str | None) -> dict[str, Any]:
    def counts(failures: int) -> dict[str, int]:
        return {"total_tests": 1, "total_failures": failures,
                "total_errors": 0, "total_skipped": 0}

    if stage == "compilation":
        return {"failure_stage": stage, "stderr": "compiler error"}
    return {
        "test_result": {
            "functional_result": counts(int(stage == "functionality")),
            "security_result": counts(int(stage == "security"))
            if stage != "functionality" else {},
        },
        "function_stderr": "functional diagnostic",
        "security_stderr": "security diagnostic",
    }


@pytest.mark.parametrize("stage", [None, "compilation", "functionality", "security"])
def test_verifier_stages(stage: str | None) -> None:
    result = runner.normalize("XML", verification(stage), 1.0, 0)
    assert result.failure_stage == stage
    if stage in ("compilation", "functionality"):
        assert result.security == "not_run"
    if stage == "compilation":
        assert result.functionality == "not_run"
    if stage:
        assert "diagnostic" in result.feedback or "compiler error" in result.feedback


def test_bad_verifier_response_raises() -> None:
    with pytest.raises(RuntimeError, match="zero functional tests"):
        runner.normalize("XML", {"stderr": "service broken"}, 1.0, 0)


def test_suite_end_to_end_with_fake_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        "model": {"max_retries": 0, "timeout_seconds": 10},
        "judge": {"provider": "OPENAI", "name": "test"},
        "cycles": 1, "rpm_limit": 60, "concurrency": 1,
        "verify_timeout_seconds": 10,
    }
    case = {"severity": "high", "verify_urls": {"gen": "http://fake/verify"},
            "remote_prompt_paths": {"gen": "prompt"}}
    monkeypatch.setattr(runner.java, "load_config", lambda *a, **kw: (cfg, {"case": case}))
    monkeypatch.setattr(runner.java, "credential", lambda spec, field: "test-credential")
    monkeypatch.setattr(runner.java, "read_prompt", lambda *a: "original task")
    prompts: list[str] = []

    async def generate(client: Any, config: Any, prompt: str, gate: Any,
                       trace: dict[str, Any]) -> str:
        prompts.append(prompt)
        return "XML"

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, url: str, json: Any) -> "Client":
            return self

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return verification("security" if len(prompts) == 1 else None)

    monkeypatch.setattr(runner.java, "generate", generate)
    monkeypatch.setattr(runner, "AsyncOpenAI", Client)
    monkeypatch.setattr(runner.httpx, "AsyncClient", Client)
    suite = yaml.safe_load((runner.ROOT / "experiments/java.yaml").read_text())
    path = tmp_path / "suite.yaml"
    path.write_text(yaml.safe_dump(suite))
    out = tmp_path / "output"
    asyncio.run(runner.run(path, out, ["iteration_gen"], False))
    assert prompts[0] == "original task"
    assert "security diagnostic" in prompts[1]
    summary = json.loads((out / "iteration_gen/summary.json").read_text())
    assert summary["first_pass_percent"] == 0
    assert summary["final_pass_percent"] == 100
    assert summary["mean_attempts"] == 2
    assert summary["recovered_by_stage"]["security"] == 1
    assert (out / "finish").exists()

    async def truncated(*args: Any) -> str:
        raise ExceptionGroup("MCP transport", [runner.java.GenerationTruncated("length")])

    monkeypatch.setattr(runner.java, "generate", truncated)
    skipped = tmp_path / "skipped"
    asyncio.run(runner.run(path, skipped, ["iteration_gen"], False))
    assert (skipped / "finish_with_skips").exists()
    assert len(json.loads((skipped / "pending.json").read_text())) == 1
    assert (skipped / "iteration_gen/case/repeat_00/error.json").exists()
    monkeypatch.setattr(runner.java, "generate", generate)
    retried = tmp_path / "retried"
    asyncio.run(runner.run(path, retried, ["iteration_gen"], False, skipped))
    assert json.loads((retried / "pending.json").read_text()) == []
    assert (retried / "finish").exists()

    async def http_failure(*args: Any) -> str:
        raise runner.httpx.ConnectError("server unavailable")

    monkeypatch.setattr(runner.java, "generate", http_failure)
    asyncio.run(runner.run(path, tmp_path / "http-error", ["iteration_gen"], False))
    assert (tmp_path / "http-error/finish_with_skips").exists()

    async def bug(*args: Any) -> str:
        raise TypeError("programming error")

    monkeypatch.setattr(runner.java, "generate", bug)
    with pytest.raises(ExceptionGroup):
        asyncio.run(runner.run(path, tmp_path / "bug", ["iteration_gen"], False))
