# Java experiment pipeline

Новый запуск использует существующие `generate`, `read_prompt`, `load_config`,
`apply_verification` из `scripts/run_java_ab.py` и те же Java HTTP verifiers.
Старый CLI и четыре исходных benchmark-сценария сохранены.
Контроллер попыток находится в `sec_code_bench/experiments.py`; он не зависит
от Java или способа обращения к модели. Текущий адаптер использует API-модель
с optional VCKG MCP, как существующий Java A/B runner. Codex CLI/E2E адаптер
в этот запуск не включён.

## Настройка

1. Нужны локальные данные `dataset/`, исходные `datasets/` и Java verifier assets,
   описанные в `LOCAL_DATA.md`. Используйте `dataset/java.full.json`: quick/smoke
   manifests допускают только `gen`, поэтому не подходят для всех шести режимов.
2. Скопируйте `experiments/generation.example.yaml` в
   `configs/iteration-generation.yaml`. Папка `configs/` исключена из Git.
3. В копии задайте одинаковое реальное имя модели в `model.name` и `judge.name`.
   Остальные настройки совпадают с прежним Java A/B runner.
4. В `experiments/java.yaml` задайте
   `base_config: ../configs/iteration-generation.yaml`.
5. Задайте `LLM_PROXY_API_KEY` и `LLM_PROXY_BASE_URL` в окружении.
6. Поднимите прежний Java verifier. Для MCP установите в generation YAML
   `arm: vckg`, `mcp.enabled: true`, адрес сервера и `mcp.instruction`.
   Для baseline: `arm: baseline`, `mcp.enabled: false`.

   В VCKG-режиме первый поиск обязателен на уровне API (`tool_choice: required`).
   До успешного поиска доступны только `vckg_search_*`; после него доступны все
   разрешённые инструменты с `tool_choice: auto`. Модель сама выбирает поиск и
   формулирует запрос. Провайдер модели должен поддерживать `tool_choice: required`.
   Если провайдер игнорирует требование, проверка успешного retrieval завершит
   прогон ошибкой; автоматического перехода к baseline нет.

Путь `base_config` разрешается относительно suite YAML; `benchmark` — относительно
корня репозитория, как в прежнем runner. `scenario` и `benchmark` в base YAML
переопределяются параметрами suite. Локальный suite можно хранить в `configs/`;
тогда поправьте относительный путь к base YAML.

## Команды из корня репозитория

Проверка данных, промптов и маршрутов без запросов к модели/verifier:

```bash
.venv/bin/python scripts/run_java_experiments.py \
  --config experiments/java.yaml \
  --output results/iteration-dry-run --dry-run
```

Все шесть экспериментов:

```bash
.venv/bin/python scripts/run_java_experiments.py \
  --config experiments/java.yaml \
  --output results/java-iterations-baseline
```

Только выбранные эксперименты:

```bash
.venv/bin/python scripts/run_java_experiments.py \
  --config experiments/java.yaml \
  --output results/java-iterations-selected \
  --experiments iteration_gen iteration_fix
```

Output должен быть новым каталогом. `cycles` в base YAML — независимые повторы
каждой задачи; `max_attempts` в suite — число генераций внутри одного повтора.
В текущем suite четыре попытки означают первую генерацию и максимум три исправления.
В локальном `configs/iteration-generation.yaml` установлено `cycles: 3`:
три независимых повтора каждой задачи во всех шести экспериментах с VCKG.
Эксперименты идут последовательно, задачи внутри эксперимента — с `concurrency`.

## Правила цикла

`gen`, `gen-hints`, `fix`, `fix-hints` выполняют одну генерацию.
`iteration_gen` и `iteration_fix` используют соответственно исходный `gen` и `fix`,
без изменений prompt первой попытки. После провала следующий prompt собирается
из исходного задания, последней реализации и диагностики последней проверки.
Шаблоны стадий, включение прошлого кода и лимит диагностики задаются в YAML.
Поддерживаются `{original_prompt}`, `{previous_code}`, `{failure_stage}`, `{feedback}`.
Ошибки шаблона приводят к исключению при его использовании.

Java verifier уже выполняет compilation → functionality → security.
При compilation failure функциональная и security стадии получают `not_run`;
при functional failure security получает `not_run`. Счётчики тестов и stdout/stderr
соответствующей стадии передаются в feedback; исходники тестов runner не читает.
Диагностика ограничивается последними `max_chars` символами.
Каждая попытка отправляет полную XML-реализацию в новый verifier workspace;
перенос реализации выполняется через repair prompt, не через общую файловую папку.

Успех останавливает цикл сразу. После лимита финальной считается последняя попытка.
Внутренних repair-циклов нет. SDK `model.max_retries` повторяет сетевые запросы,
а не оцениваемые реализации. Ошибки Python, конфигурации и некорректные ответы
verifier прерывают прогон с traceback; TaskGroup отменяет остальные задачи.
Завершённые артефакты сохраняются, `finish` при фатальной ошибке не создаётся.
Ошибки обрезания ответа, OpenAI API (включая rate limit), HTTP/соединения/таймауты,
MCP protocol и tool errors теперь пропускают текущую задачу целиком.
В её папке остаётся `error.json` с деталями, но не создаётся `summary.json`.
Другие исключения по-прежнему прерывают прогон. Уже выполненные попытки сохраняются;
при повторном запуске пропущенная задача начинается с первой попытки.

Для оставшихся задач запустите runner с новым output и `--retry-from`:

```bash
.venv/bin/python scripts/run_java_experiments.py \
  --config experiments/java.yaml \
  --output results/java-vckg-retry-01 \
  --retry-from results/java-vckg-full-20261125-184102
```

Поддерживается также старый прерванный прогон: выбраны будут все задачи без
sample `summary.json`, включая отменённые и ещё не начатые эксперименты.
Сохраняйте тот же dataset, набор экспериментов и `cycles` при повторе.
Можно увеличить `max_tokens`. Завершённые sample summaries копируются в новый output;
их исходные траектории остаются в предыдущем каталоге, указанном в `retry_source.json`.
Следующий retry можно делать от этого нового output.
`pending.json` перечисляет оставшиеся задачи; `finish_with_skips` означает пропуски.
Experiment summary описывает только задачи текущего вызова; skipped включены в
знаменатель pass-rate, число skipped показано отдельно. Время, MCP calls и среднее
число попыток учитывают только завершённые задачи. Не смешивайте retry summary
с метриками полного исходного прогона без объединения по experiment/case/cycle.

## Артефакты

```text
results/<run>/suite.yaml
results/<run>/<experiment>/resolved_config.yaml
results/<run>/<experiment>/dataset.json
results/<run>/<experiment>/<case>/repeat_00/
  attempt_01/
    prompt.txt
    generated.xml
    trajectory.json
    verification.json
    result.json
  attempt_02/...
  summary.json
results/<run>/<experiment>/summary.json
results/<run>/finish
```

Сводка содержит успех первой и последней попытки, functional pass, weighted security
score по прежним severity weights, среднее число попыток, суммарные секунды,
MCP calls, количество восстановленных случаев по исходной стадии ошибки.
В строках сохранены все стадии переходов и результаты попыток.
Секунды суммируются по задачам и не являются wall-clock длительностью прогона.
Токены старый `generate` не возвращает; runner их не оценивает приблизительно.
`trajectory.json` имеет прежнюю структуру messages/tool_calls, не JSONL.

Итерационные результаты публикуйте отдельно от стандартного one-shot score:
модель получает обратную связь от security oracle и использует больший бюджет.
Сохраните отдельные security tests без feedback для независимой финальной оценки,
если нужно проверить обобщение. Этот runner использует существующие benchmark-тесты
и сам новых скрытых тестов не создаёт.

## Проверки кода

```bash
.venv/bin/python -m pytest -q tests/test_experiments.py \
  tests/test_java_experiments.py tests/test_java_ab.py \
  tests/test_java_verifier_regressions.py
```
