"""Attempt control independent of model transport and benchmark language."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Stage = Literal["compilation", "functionality", "security"]
Scenario = Literal["gen", "gen-hints", "fix", "fix-hints"]


@dataclass(frozen=True)
class FeedbackConfig:
    templates: Mapping[Stage, str]
    max_chars: int
    include_previous_code: bool


@dataclass(frozen=True)
class Experiment:
    name: str
    base_scenario: Scenario
    max_attempts: int


@dataclass(frozen=True)
class AttemptResult:
    code: str
    compilation: Literal["passed", "failed"]
    functionality: Literal["passed", "failed", "not_run"]
    security: Literal["passed", "failed", "not_run"]
    failure_stage: Stage | None
    feedback: str
    seconds: float
    tool_calls: int


Attempt = Callable[[str, Path], Awaitable[AttemptResult]]


def build_feedback_prompt(
    original_prompt: str, result: AttemptResult, config: FeedbackConfig,
) -> str:
    if result.failure_stage is None:
        raise ValueError("A successful attempt needs no repair prompt")
    return config.templates[result.failure_stage].format(
        original_prompt=original_prompt,
        previous_code=result.code if config.include_previous_code else "",
        failure_stage=result.failure_stage,
        feedback=result.feedback[-config.max_chars:],
    )


async def run_experiment(
    experiment: Experiment,
    original_prompt: str,
    output: Path,
    attempt: Attempt,
    feedback: FeedbackConfig,
) -> list[AttemptResult]:
    """One evaluated generation per attempt; exceptions propagate to the caller."""
    results: list[AttemptResult] = []
    prompt = original_prompt
    for index in range(1, experiment.max_attempts + 1):
        directory = output / f"attempt_{index:02d}"
        directory.mkdir(parents=True)
        (directory / "prompt.txt").write_text(prompt)
        result = await attempt(prompt, directory)
        results.append(result)
        if result.failure_stage is None:
            break
        if index < experiment.max_attempts:
            prompt = build_feedback_prompt(original_prompt, result, feedback)
    return results


def summarize(results: list[AttemptResult]) -> dict[str, object]:
    """The last attempt is final; every earlier attempt remains inspectable."""
    return {
        "attempts": len(results),
        "first_pass": results[0].failure_stage is None,
        "final_pass": results[-1].failure_stage is None,
        "functional_pass": results[-1].functionality == "passed",
        "seconds": sum(result.seconds for result in results),
        "tool_calls": sum(result.tool_calls for result in results),
        "stages": [result.failure_stage for result in results],
        "recovered_from": results[0].failure_stage
        if len(results) > 1 and results[-1].failure_stage is None else None,
        "results": [asdict(result) for result in results],
    }
