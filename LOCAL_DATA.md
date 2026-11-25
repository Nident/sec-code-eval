# Local benchmark assets

This repository contains the runner, verifier services, tests and documentation.
Benchmark datasets and experiment outputs are maintained separately and are not
included in Git. Keep the following local directories when running experiments:

- `dataset/` and `datasets/`: prompts, manifests and project templates.
- `docker/model/java/datasets/`: Java verifier benchmark assets.
- `configs/`: machine-specific experiment configurations.
- `results/`: generated code, trajectories and evaluation results.

Copy `config.example.yaml` to a local configuration and supply your own credentials.
`system_config.yaml` contains public scoring and language settings and is tracked.
Restore benchmark assets from the matching upstream SecCodeBench revision
`bf0c4d72` or an existing local copy before running the benchmark. The custom
`dataset/` can be regenerated with `scripts/prepare_java_dataset.py` after restoring
the upstream data. Existing run instructions assume these assets are present.
