import asyncio
from pathlib import Path

import pytest

from sec_code_bench.experiments import (
    AttemptResult, Experiment, FeedbackConfig, Stage, build_feedback_prompt,
    run_experiment, summarize,
)


FEEDBACK = FeedbackConfig(
    templates={stage: "{original_prompt}\n{previous_code}\n{failure_stage}\n{feedback}"
               for stage in ("compilation", "functionality", "security")},
    max_chars=10, include_previous_code=True,
)


def result(stage: Stage | None) -> AttemptResult:
    return AttemptResult(
        code="previous XML", compilation="failed" if stage == "compilation" else "passed",
        functionality="not_run" if stage == "compilation" else
        ("failed" if stage == "functionality" else "passed"),
        security="not_run" if stage in ("compilation", "functionality") else
        ("failed" if stage == "security" else "passed"),
        failure_stage=stage, feedback="long diagnostic message", seconds=1, tool_calls=2,
    )


@pytest.mark.parametrize("stages", [
    [None], ["compilation", None], ["functionality", None], ["security", None],
    ["compilation", "functionality", "security"],
])
def test_stop_and_attempt_limit(tmp_path: Path, stages: list[Stage | None]) -> None:
    prompts: list[str] = []

    async def attempt(prompt: str, path: Path) -> AttemptResult:
        assert (path / "prompt.txt").read_text() == prompt
        prompts.append(prompt)
        return result(stages[len(prompts) - 1])

    results = asyncio.run(run_experiment(
        Experiment("iteration_gen", "gen", 3), "original task", tmp_path, attempt, FEEDBACK,
    ))
    assert len(results) == len(stages)
    assert prompts[0] == "original task"
    if len(prompts) > 1:
        assert "previous XML" in prompts[1]
        assert prompts[1].endswith("ic message")
    assert summarize(results)["final_pass"] == (stages[-1] is None)


def test_native_never_repairs(tmp_path: Path) -> None:
    async def attempt(prompt: str, path: Path) -> AttemptResult:
        assert prompt == "native task"
        return result("security")

    results = asyncio.run(run_experiment(
        Experiment("gen", "gen", 1), "native task", tmp_path, attempt, FEEDBACK,
    ))
    assert len(results) == 1


def test_initial_generation_and_three_repairs(tmp_path: Path) -> None:
    calls = 0

    async def attempt(prompt: str, path: Path) -> AttemptResult:
        nonlocal calls
        calls += 1
        return result("security")

    results = asyncio.run(run_experiment(
        Experiment("iteration_gen", "gen", 4), "task", tmp_path, attempt, FEEDBACK,
    ))
    assert calls == len(results) == 4
    assert not (tmp_path / "attempt_05").exists()


def test_errors_propagate(tmp_path: Path) -> None:
    async def attempt(prompt: str, path: Path) -> AttemptResult:
        raise RuntimeError("broken verifier")

    with pytest.raises(RuntimeError, match="broken verifier"):
        asyncio.run(run_experiment(
            Experiment("iteration_fix", "fix", 3), "task", tmp_path, attempt, FEEDBACK,
        ))
    assert not (tmp_path / "attempt_02").exists()


def test_feedback_can_omit_previous_code() -> None:
    feedback = FeedbackConfig(FEEDBACK.templates, 10, False)
    assert "previous XML" not in build_feedback_prompt("task", result("security"), feedback)
