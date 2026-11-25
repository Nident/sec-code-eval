#!/usr/bin/env python3
"""Run YAML-defined experiments over the existing Java generation/verifier path."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

import httpx
from openai import APIError, AsyncOpenAI
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_java_ab as java  # noqa: E402
from sec_code_bench.experiments import (  # noqa: E402
    AttemptResult, Experiment, FeedbackConfig, Stage,
    run_experiment, summarize,
)


def exception_details(error: BaseException, secrets: tuple[str, ...]) -> list[str]:
    if isinstance(error, BaseExceptionGroup):
        return [detail for child in error.exceptions
                for detail in exception_details(child, secrets)]
    text = f"{type(error).__name__}: {error}"
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return [text]


def normalize(answer: str, raw: dict[str, Any], seconds: float,
              tool_calls: int) -> AttemptResult:
    row: dict[str, Any] = {}
    java.apply_verification(raw, row)
    stage: Stage | None
    if raw.get("failure_stage") == "compilation":
        stage = "compilation"
        detail = raw["stderr"]
    elif not row["functional_pass"]:
        stage = "functionality"
        detail = raw.get("function_stdout", "") + "\n" + raw.get("function_stderr", "")
    elif not row["security_pass"]:
        stage = "security"
        detail = raw.get("security_stdout", "") + "\n" + raw.get("security_stderr", "")
    else:
        stage, detail = None, ""
    # Counters provide feedback even when a verifier emits no textual diagnostics.
    if stage in ("functionality", "security"):
        key = "functional_result" if stage == "functionality" else "security_result"
        detail = json.dumps(raw["test_result"][key]) + "\n" + detail
    return AttemptResult(
        code=answer,
        compilation="failed" if stage == "compilation" else "passed",
        functionality="not_run" if stage == "compilation" else
        ("passed" if row["functional_pass"] else "failed"),
        security="not_run" if stage in ("compilation", "functionality") else
        ("passed" if row["security_pass"] else "failed"),
        failure_stage=stage, feedback=detail, seconds=seconds, tool_calls=tool_calls,
    )


def load_suite(path: Path, selected: list[str] | None) -> tuple[
    dict[str, Any], FeedbackConfig, list[Experiment],
]:
    suite = yaml.safe_load(path.read_text())
    feedback = FeedbackConfig(**suite["feedback"])
    experiments = [Experiment(name=name, **spec)
                   for name, spec in suite["experiments"].items()]
    for experiment in experiments:
        if experiment.base_scenario not in java.SUFFIX:
            raise ValueError(f"Unknown scenario: {experiment.base_scenario}")
        if experiment.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
    if feedback.max_chars < 1:
        raise ValueError("feedback.max_chars must be positive")
    if selected:
        by_name = {experiment.name: experiment for experiment in experiments}
        experiments = [by_name[name] for name in selected]
    return suite, feedback, experiments


async def run(config: Path, output: Path, selected: list[str] | None,
              dry_run: bool, retry_from: Path | None = None) -> None:
    suite, feedback, experiments = load_suite(config, selected)
    base = (config.parent / suite["base_config"]).resolve()
    loaded = [(experiment, *java.load_config(
        base, scenario=experiment.base_scenario, benchmark=suite["benchmark"],
    )) for experiment in experiments]
    if dry_run:
        for experiment, _, data in loaded:
            print(f"{experiment.name}: {len(data)} cases, "
                  f"max_attempts={experiment.max_attempts}")
        return
    output.mkdir(parents=True, exist_ok=False)
    (output / "suite.yaml").write_text(yaml.safe_dump(suite, sort_keys=False))
    if retry_from is not None:
        if not (retry_from / "suite.yaml").is_file():
            raise ValueError(f"Previous run not found: {retry_from}")
        java.dump(output / "retry_source.json", {"source": str(retry_from.resolve())})
        for summary in retry_from.glob("*/*/repeat_*/summary.json"):
            destination = output / summary.relative_to(retry_from)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(summary, destination)
    # Sequential experiments, bounded concurrent samples within each experiment.
    for experiment, cfg, data in loaded:
        samples = [(name, case, cycle) for cycle in range(cfg["cycles"])
                   for name, case in data.items()
                   if retry_from is None or not (
                       retry_from / experiment.name / name / f"repeat_{cycle:02d}"
                       / "summary.json").exists()]
        if not samples:
            continue
        m, j = cfg["model"], cfg["judge"]
        key, endpoint = java.credential(m, "api_key"), java.credential(m, "base_url")
        judge_key = java.credential(j, "api_key")
        judge_spec = "::".join([j["provider"], j["name"], judge_key,
                                java.credential(j, "base_url")])
        directory = output / experiment.name
        directory.mkdir(exist_ok=True)
        (directory / "resolved_config.yaml").write_text(yaml.safe_dump({
            "generation": cfg, "experiment": asdict(experiment),
            "feedback": asdict(feedback),
        }, sort_keys=False))
        java.dump(directory / "dataset.json", data)
        gate = java.RequestGate(cfg["rpm_limit"])
        semaphore = asyncio.Semaphore(cfg["concurrency"])
        service_errors: tuple[type[Exception], ...] = (
            java.GenerationTruncated, java.MCPToolFailed, APIError, httpx.HTTPError,
            TimeoutError,
        )
        if cfg.get("mcp", {}).get("enabled"):
            from mcp.shared.exceptions import McpError
            service_errors += (McpError,)
        async with AsyncOpenAI(api_key=key, base_url=endpoint,
                               max_retries=m["max_retries"],
                               timeout=m["timeout_seconds"]) as client, \
                httpx.AsyncClient(timeout=cfg["verify_timeout_seconds"]) as http:

            async def one(name: str, case: dict[str, Any], cycle: int
                          ) -> dict[str, Any]:
                async with semaphore:
                    sample = directory / name / f"repeat_{cycle:02d}"

                    async def attempt(prompt: str, path: Path) -> AttemptResult:
                        start = time.monotonic()
                        trace: dict[str, Any] = {}
                        # Preserve partial tool traces even if generation raises.
                        try:
                            answer = await java.generate(client, cfg, prompt, gate, trace)
                        finally:
                            java.dump(path / "trajectory.json", trace)
                        (path / "generated.xml").write_text(answer)
                        response = await http.post(
                            case["verify_urls"][experiment.base_scenario], json={
                                "token": "local-eval-token", "code": answer,
                                "prompt_path": case["remote_prompt_paths"][
                                    experiment.base_scenario],
                                "locale": "en-US", "judge_llm_list": [judge_spec],
                            })
                        response.raise_for_status()
                        raw = json.loads(json.dumps(response.json()).replace(
                            key, "[REDACTED]").replace(judge_key, "[REDACTED]"))
                        java.dump(path / "verification.json", raw)
                        result = normalize(answer, raw, time.monotonic() - start,
                                           len(trace.get("tool_calls", [])))
                        java.dump(path / "result.json", asdict(result))
                        print(f"{experiment.name}/{name}/{cycle}/{path.name}: "
                              f"{result.failure_stage or 'passed'}", flush=True)
                        return result

                    skipped = False
                    try:
                        results = await run_experiment(
                            experiment, java.read_prompt(case, experiment.base_scenario),
                            sample, attempt, feedback,
                        )
                    except* service_errors as errors:
                        # MCP wraps exceptions in nested ExceptionGroups.
                        # All unrelated exceptions still propagate.
                        skipped = True
                        java.dump(sample / "error.json", {
                            "case_id": name, "cycle": cycle,
                            "error": str(errors).replace(key, "[REDACTED]").replace(
                                judge_key, "[REDACTED]"),
                            "details": exception_details(errors, (key, judge_key)),
                        })
                    if skipped:
                        print(f"{experiment.name}/{name}/{cycle}: skipped (service/generation error)",
                              flush=True)
                        return {"case_id": name, "cycle": cycle, "skipped": True,
                                "first_pass": False, "final_pass": False,
                                "functional_pass": False, "attempts": 0,
                                "seconds": 0, "tool_calls": 0, "recovered_from": None}
                    summary = {"case_id": name, "cycle": cycle, **summarize(results)}
                    java.dump(sample / "summary.json", summary)
                    return summary

            # TaskGroup cancels other samples on an exception: no silent error rows.
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(one(name, case, cycle))
                         for name, case, cycle in samples]
            rows = [task.result() for task in tasks]
        weights = yaml.safe_load((ROOT / "system_config.yaml").read_text())[
            "score_weights"]["category_weights"]
        total_weight = sum(weights[data[row["case_id"]]["severity"]] for row in rows)
        summary = {
            "total": len(rows),
            "skipped": sum(row.get("skipped", False) for row in rows),
            "metrics_scope": "this invocation; skipped samples remain in pass-rate denominators",
            "first_pass_percent": 100 * sum(row["first_pass"] for row in rows) / len(rows),
            "final_pass_percent": 100 * sum(row["final_pass"] for row in rows) / len(rows),
            "functional_pass_percent": 100 * sum(row["functional_pass"] for row in rows) / len(rows),
            "weighted_secure_percent": 100 * sum(
                weights[data[row["case_id"]]["severity"]] * row["final_pass"]
                for row in rows) / total_weight,
            "mean_attempts": (sum(row["attempts"] for row in rows) /
                              sum(not row.get("skipped", False) for row in rows))
            if any(not row.get("skipped", False) for row in rows) else None,
            "seconds": sum(row["seconds"] for row in rows),
            "tool_calls": sum(row["tool_calls"] for row in rows),
            "recovered_by_stage": {stage: sum(row["recovered_from"] == stage for row in rows)
                                   for stage in ("compilation", "functionality", "security")},
            "rows": rows,
        }
        java.dump(directory / "summary.json", summary)
    pending = [{"experiment": experiment.name, "case_id": name, "cycle": cycle}
               for experiment, cfg, data in loaded
               for cycle in range(cfg["cycles"]) for name in data
               if not (output / experiment.name / name / f"repeat_{cycle:02d}"
                       / "summary.json").exists()
               and (retry_from is None or not (
                   retry_from / experiment.name / name / f"repeat_{cycle:02d}"
                   / "summary.json").exists())]
    java.dump(output / "pending.json", pending)
    (output / ("finish_with_skips" if pending else "finish")).touch()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiments", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-from", type=Path,
                        help="Run samples without a completed summary in this previous run")
    args = parser.parse_args()
    asyncio.run(run(args.config, args.output, args.experiments, args.dry_run, args.retry_from))


if __name__ == "__main__":
    main()
