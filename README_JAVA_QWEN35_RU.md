# Быстрый запуск Java: Qwen35 → Qwen35 + VCKG MCP

Новый pipeline из шести экспериментов с repair-попытками и YAML-шаблонами:
[инструкция запуска](experiments/README.md).

Подготовлено для `/root/sec-code-bench`. Сам бенчмарк и платные вызовы модели при подготовке не запускались.
Используются исходные задания и Java verifier этого checkout. Это проверка **генерации безопасного кода**,
а не поиск уязвимостей в сторонних коммитах.

## 1. Что находится в dataset

Исходник: `datasets/benchmark/java/java.json`, 53 Java-задачи.
В upstream JSON нет CWE-разметки. Поэтому добавлена **локальная, проверяемая разметка основных CWE**
по содержанию задач и тестов. Это не официальные CWE-метки авторов бенчмарка.

Применено точное совпадение основной CWE со списком пользователя; автоматического включения всех
потомков абстрактных CWE-20, CWE-74 и CWE-200 нет. Например, command injection имеет основную
CWE-78 и исключена, хотя относится к общему семейству injection; hardcoded credentials (CWE-798)
не переименована в CWE-522; утечка через лог (CWE-532) не переименована в CWE-200.
Если нужна выборка с раскрытием родительских категорий, её следует сформировать отдельно и одинаково
использовать в обоих прогонах.

| CWE | Java-задач |
|---|---:|
| CWE-79 | 1 |
| CWE-200 | 2 |
| CWE-611 | 12 |
| CWE-22 | 3 |
| CWE-502 | 6 |
| CWE-89 | 7 |
| Всего | 31 |

Для CWE-863, CWE-20, CWE-522, CWE-400, CWE-287, CWE-862, CWE-319, CWE-74 задач
с такой основной классификацией в текущей Java-выборке нет.

Файлы:

- `dataset/java.smoke.json`: **6 задач**, по одной на каждую представленную CWE; сценарий `gen`.
- `dataset/java.quick.json`: **все 31 задача**, только `gen`.
- `dataset/java.full.json`: те же 31 задача, сохранены все четыре исходных сценария.
- `dataset/prompts/java/`: реальные копии английских prompt-файлов для всех четырёх сценариев.
- `dataset/templates/java/`: реальные копии исходных Java-проектов и тестов выбранных задач.
- `dataset/manifest.json`: полный список 53 задач, локальная CWE, включение/исключение,
  ссылки на исходные задания/тесты, SHA-256 исходного benchmark JSON.

В smoke входят `XSSSpringMVC`, `InfoLeakSpringBootPropertiesExposure`, `XXEDocumentBuilder`,
`PathTraversalRead`, `DeserializationObjectInputStream`, `JDBCInjection`.

Пересобрать выборку из этого checkout:

```bash
cd /root/sec-code-bench
python3 scripts/prepare_java_dataset.py
```

Этот скрипт пересобирает manifest и JSON; для изменения CWE-политики отредактируйте явное
сопоставление `annotate()` в нём. Тесты проверяют соответствие фильтру и побайтовое совпадение prompt-копий.

## 2. Конфиги и режим сравнения

- `configs/java-qwen35-baseline.yaml`: прямая Qwen35, без инструментов и без MCP.
- `configs/java-qwen35-vckg.yaml`: та же Qwen35, доступ только к шести разрешённым VCKG MCP tools.

Оба файла предназначены **для `scripts/run_java_ab.py`**, а не для Trae CLI или штатного `sec_code_bench.eval`.
Штатный API runner не исполняет MCP tool calls. Поэтому добавлен небольшой общий runner, который
использует один и тот же способ вызова модели и исходный Java verifier в обеих ветках.
Trae, Codebadger, bash-инструменты, CPG и содержимое тестов модели не передаются.

Из `/root/Vuln_agent-main/trae_config.yaml` перенесены:

- модель **`tgpt/qwen35-397b-a17b-fp8`**, OpenAI-compatible API;
- переменные `LLM_PROXY_API_KEY`, `LLM_PROXY_BASE_URL`;
- `max_tokens: 8192`, `temperature: 0.6`, `top_p: 0.95`;
- `parallel_tool_calls: false` при использовании MCP;
- адрес VCKG `http://127.0.0.1:4250/mcp`.

Для скорости: `cycles: 1`, `concurrency: 2`, `rpm_limit: 60`, SDK retries **2 вместо 15**.
`max_steps: 150` заменён ограничением 4 раунда MCP / 6 tool calls; это генерация через API, без Trae agent.
`top_k: 0` не передаётся: это не стандартное OpenAI-поле; если шлюз требует его, добавьте
`model.extra_body: {top_k: 0}` одинаково в оба файла.

**На задачу приходится одна генерация и одна отправка verifier. Автоматического исправления по результатам
тестов нет** (в штатном API runner таких попыток до трёх). Все ошибки остаются в знаменателе результата.
Максимум токенов — лимит каждого ответа, а не всего прогона. Встроенные повторы SDK при сетевых ошибках
могут повторять запросы. RPM runner ограничивает запросы генератора; внутренние повторы SDK и запросы
судьи из verifier в этот лимит не входят.

В обоих вариантах задаётся **один judge той же модели Qwen35**, без VCKG. Java verifier сам выбирает
UnitTester либо LLMTester в зависимости от задачи. Поэтому judge API вызывается только там, где
предусмотрен LLMTester, а не на каждой задаче. Параметры sampling/лимит judge остаются штатными
параметрами Java verifier: формат `judge_llm_list` передаёт provider/model/key/endpoint, но не sampling.

В VCKG-ветке есть дополнительная системная инструкция о retrieval и исполнение настоящих MCP-вызовов:
`initialize → tools/list → tools/call`. CWE-search скрыт. В запросы предлагается передавать описание
поведения, а не CWE/CVE/GHSA. Это инструкция модели, не программная проверка текста запросов.
Если модель не сделала успешный VCKG search или MCP сломался, задача помечается ошибкой;
тихого перехода в baseline нет. `no_relevant_match` считается состоявшимся retrieval с пустым результатом.
Все tool calls и результаты сохраняются в trace.

Два запуска сравнивают **Qwen35** и **Qwen35 + retrieval-инструкция + VCKG**. Влияние самой инструкции
отдельно не измеряется. Smoke из шести задач и одного цикла проверяет работоспособность и даёт
предварительное сравнение; статистическую значимость по нему утверждать нельзя.

## 3. Окружение

Уже создана `/root/sec-code-bench/.venv` с Python 3.12 и зависимостями.
Исправлена упаковка проекта и добавлены недостающие runtime-зависимости.
Для MCP client зафиксирован совместимый набор `mcp==1.12.4`, `pydantic>=2.8,<2.11`:
SDK 1.12.4 использует внутренний Pydantic API, отсутствующий в новых версиях.
На этой машине уже есть Docker image `java-verifier:v1.0`; Java verifier запущен отдельно,
остальные языковые verifier не запускаются. VCKG уже отвечает на порту 4250.

Восстановление окружения при необходимости:

```bash
cd /root/sec-code-bench
uv venv .venv
uv pip install --python .venv/bin/python -e '.[quick]' pytest
```

Проверки без модели, ключа и сетевых запросов:

```bash
.venv/bin/python scripts/run_java_ab.py --config configs/java-qwen35-baseline.yaml --dry-run
.venv/bin/python scripts/run_java_ab.py --config configs/java-qwen35-vckg.yaml --dry-run
.venv/bin/python -m pytest -q tests/test_java_ab.py
```

Проверено при подготовке: **9 тестов прошли**; dry-run для 6 и 31 задачи прошёл;
Java `/health` и настоящий VCKG MCP `initialize/tools/list` отвечают успешно.
Тесты API-цикла используют mock-модель и mock-verifier, реальную Qwen35 без ключа проверить нельзя.

## 4. API-доступ

В исходном Trae config хранятся имена переменных, а не сами ключ и URL.
В окружении подготовки они отсутствовали. Задайте их в том же терминале, откуда будете запускать runner:

```bash
cd /root/sec-code-bench
export LLM_PROXY_BASE_URL='https://ВАШ-ШЛЮЗ/v1'
read -rsp 'LLM proxy API key: ' LLM_PROXY_API_KEY
export LLM_PROXY_API_KEY
printf '\n'
```

Используйте реальный base URL из вашего рабочего окружения Trae, обычно с `/v1`,
без `/chat/completions` в конце. У шлюза должна быть доступна модель `tgpt/qwen35-397b-a17b-fp8`;
для второго прогона требуется поддержка OpenAI tool calling.

Этот же URL должен быть доступен **из Java Docker-контейнера**, где работает judge.
Если шлюз локальный, `127.0.0.1` внутри контейнера означает сам контейнер. Используйте доступный
из хоста и контейнера адрес шлюза или отдельно задайте для судьи переменную
`JUDGE_PROXY_BASE_URL` и замените `judge.base_url_env` на неё в **обоих** конфигах.

Секреты не записываются в YAML и не передаются runner через аргументы командной строки.

## 5. Java verifier

Запустить или убедиться, что он работает:

```bash
cd /root/sec-code-bench
docker compose -f docker-compose-verifiers.yml up -d java-verifier
docker compose -f docker-compose-verifiers.yml ps java-verifier
curl -fsS http://127.0.0.1:24687/health
```

Если образ отсутствует или вы изменили код verifier, пересоберите только Java:

```bash
docker compose -f docker-compose-verifiers.yml up -d --build java-verifier
```

Существующие локальные изменения Java Dockerfile, включая corporate CA bundle, сохранены.
Первая загрузка Maven-зависимостей может быть заметно медленнее следующих запусков.
Копии шаблонов в `dataset/templates` предназначены для просмотра и воспроизводимости;
проверки исполняются на исходных verifier-шаблонах `docker/model/java/datasets/templates/java/2_2_1`.
Связь имён задач с verifier routes сохранена из upstream JSON, корректность наличия routes проверяется dry-run.
Это исходная схема проекта: prompt/template клиента имеют версию `2_1_0`, verifier — `2_2_1`.

Проверить сервис без отправки задания:

```bash
.venv/bin/python scripts/run_java_ab.py --config configs/java-qwen35-baseline.yaml --check-services
```

## 6. Первый быстрый прогон — чистая Qwen35

```bash
cd /root/sec-code-bench
.venv/bin/python -u scripts/run_java_ab.py \
  --config configs/java-qwen35-baseline.yaml \
  --output results/java-ab/baseline-smoke
```

По умолчанию это **6 задач × gen × 1 цикл**. Baseline вообще не подключается к VCKG:
наличие работающего MCP-сервера на машине на него не влияет.
`--output` должен указывать на ещё не существующую директорию. Если параметр опущен,
создаётся уникальная директория с московской датой/временем и именем ветки.

## 7. Второй быстрый прогон — та же Qwen35 + VCKG

Поднять VCKG, если он остановлен:

```bash
export VCKG_TOOL_CONFIG=/root/Vuln_agent-main/vckg/vckg_tool_config.yaml
/root/Vuln_agent-main/vckg/vckg_mcp_ctl.sh start
curl -fsS http://127.0.0.1:4250/health
```

Если меняли VCKG config у уже работающего сервера, примените `restart` вместо `start`.
Ожидается preset EP/PC/RC/FI с отключённым CWE search. После этого:

```bash
cd /root/sec-code-bench
.venv/bin/python scripts/run_java_ab.py --config configs/java-qwen35-vckg.yaml --check-services
.venv/bin/python -u scripts/run_java_ab.py \
  --config configs/java-qwen35-vckg.yaml \
  --output results/java-ab/vckg-smoke
```

`--check-services` делает настоящий MCP handshake и проверяет список tools; вызовов модели и генерации нет.
Подключать MCP в глобальную конфигурацию Codex/Trae не требуется: runner подключается к HTTP endpoint сам.

## 8. Сравнение и все 31 задача

Сравнить smoke:

```bash
.venv/bin/python scripts/compare_java_ab.py \
  results/java-ab/baseline-smoke results/java-ab/vckg-smoke
```

Скрипт проверяет совпадение модели, судьи, сценария, набора задач, циклов и хешей prompt.
Показывает изменение weighted score в процентных пунктах и задачи с улучшением/ухудшением.

Быстрый прогон на **всех 31 отобранных задачах** — задайте один и тот же override в обеих ветках:

```bash
.venv/bin/python -u scripts/run_java_ab.py \
  --config configs/java-qwen35-baseline.yaml \
  --benchmark dataset/java.quick.json \
  --output results/java-ab/baseline-31

.venv/bin/python -u scripts/run_java_ab.py \
  --config configs/java-qwen35-vckg.yaml \
  --benchmark dataset/java.quick.json \
  --output results/java-ab/vckg-31

.venv/bin/python scripts/compare_java_ab.py \
  results/java-ab/baseline-31 results/java-ab/vckg-31
```

Для `fix` создайте копии двух конфигов, поменяйте `benchmark: dataset/java.full.json`
и `scenario: fix`. Один запуск runner всегда исполняет **один** сценарий из конфига;
само наличие четырёх сценариев в full JSON не запускает их все.
Для более устойчивой оценки увеличьте `cycles` одинаково в обоих файлах; объём работы растёт линейно.

## 9. Результаты, ошибки и остановка

В каждой директории запуска:

- `config.json`: настройки и имена env-переменных без раскрытых секретов;
- `dataset.json`, `provenance.json`: фактическая выборка, хеши prompt и параметры сценария;
- `CASE.CYCLE.xml`: ответ генератора;
- `CASE.CYCLE.trace.json`: диалог; в VCKG-ветке также tool calls и полные MCP results;
- `CASE.CYCLE.verification.json`: исходный ответ Java verifier;
- `CASE.CYCLE.result.json`: результат задачи и ошибка, если была;
- `summary.json`: функция, безопасность, ошибки, итог и построчные результаты;
- `finish`: все задачи завершены без инфраструктурных/API-ошибок;
- `finish_with_errors`: прогон завершён с ошибками; exit code 1.

Непрохождение функционального/безопасностного теста — обычный отрицательный результат, не ошибка запуска.
Ноль функциональных тестов или ноль security tests после успешных functional tests считается ошибкой.
`security_pass` означает прохождение **и функции, и безопасности**.
`weighted_secure_percent` использует исходные веса severity из `system_config.yaml`;
это результат выбранного сценария и поднабора, **не итог полного SecCodeBench leaderboard**.

Если процесс прерван или не пройдена стартовая health-проверка, finish-файла не будет.
Автоматического resume нет: повторный запуск выполняйте в новой директории.

Для диагностики:

```bash
docker compose -f docker-compose-verifiers.yml logs --tail 100 java-verifier
/root/Vuln_agent-main/vckg/vckg_mcp_ctl.sh status
```

Ошибки 401/404 модели: проверьте ключ, URL и точное имя модели.
Если gateway принимает только stream, поменяйте `model.stream: true` в обоих конфигах:
runner собирает как текст, так и фрагментированные tool calls из stream.
Если gateway требует параметр отключения thinking, задайте его в `model.extra_body`
одинаково в обеих ветках **только в формате, который поддерживает ваш шлюз**.
При обрезанном ответе увеличьте `model.max_tokens` одинаково в двух файлах.
Если VCKG trace отсутствует или поиск не выполнен, задача не засчитывается как корректный VCKG-прогон.

Остановить Java verifier после экспериментов:

```bash
docker compose -f docker-compose-verifiers.yml stop java-verifier
```

При необходимости остановить также VCKG:

```bash
/root/Vuln_agent-main/vckg/vckg_mcp_ctl.sh stop
```

## Исправление обнаруженного падения verifier

В первом реальном smoke выявились два независимых сбоя окружения:

1. `PKIX path building failed`: переменная `SSL_CERT_FILE` настраивает Python, но не Java/Maven.
   Добавлен импорт CA из того же корпоративного bundle в Java trust store (`scripts/install_java_ca.py`
   внутри `docker/model/java`). Проверка TLS не отключается.
2. `No judge LLM has valid responses`: Qwen возвращает reasoning в `<think>`, в котором тоже может
   находиться JSON. Verifier теперь удаляет завершённый reasoning-блок перед извлечением итогового JSON.
   Незавершённый `<think>` не принимается как вердикт.

Также runner теперь выводит причину из `stderr` verifier при нуле тестов, а Java verifier сохраняет
вывод Maven в ошибке отсутствующего JUnit report.

Текущий compose использует исправленный образ `java-verifier:qwen35-fix`. На этой машине он собран
и контейнер пересоздан. Быстро воспроизвести исправление поверх прежнего локального образа:

```bash
cd /root/sec-code-bench
docker build -t java-verifier:qwen35-fix \
  -f docker/model/java/Dockerfile.qwen35-fix docker/model/java
docker compose -f docker-compose-verifiers.yml up -d java-verifier
```

При чистой сборке без старого `java-verifier:v1.0` используйте полный Dockerfile:

```bash
docker compose -f docker-compose-verifiers.yml up -d --build java-verifier
```

Старый `baseline-smoke` с шестью инфраструктурными ошибками не измеряет качество модели.
Сохраните его для диагностики, новый прогон направьте в другую директорию, например
`results/java-ab/baseline-smoke-fixed`. Сравнивайте baseline и VCKG, выполненные на одной
исправленной версии verifier.

Дополнительно исправлено извлечение кода: XML-примеры внутри `<think>` теперь исключаются,
прежде чем verifier ищет `<result>`. Иначе он мог компилировать placeholder из reasoning вместо
финального ответа модели.

Чтобы повторно проверить **уже сгенерированные ответы** после исправления verifier и не платить
за генерацию повторно, используйте replay. Он проверяет совпадение модели, ветки, сценария,
циклов, выборки и хешей prompt. Исходные результаты остаются без изменений:

```bash
.venv/bin/python -u scripts/run_java_ab.py \
  --config configs/java-qwen35-baseline.yaml \
  --replay-from results/java-ab/baseline-smoke \
  --output results/java-ab/baseline-smoke-fixed
```

Replay не вызывает генератор, но **LLM-судья по-прежнему вызывается** для LLMTester-задач;
API-переменные должны быть заданы. После него VCKG запускать обычным способом в отдельную директорию.

### Исправление LLMTester: `Event loop is closed`

В `baseline-smoke-fixed` обнаружено ещё одно падение только на security-фазе LLMTester.
Verifier запускает функциональную и security-проверку в разных `asyncio.run()`;
раньше один `AsyncOpenAI` с общим пулом соединений переживал закрытие первого event loop.
Теперь асинхронный клиент создаётся и закрывается внутри каждого запроса на его текущем loop.
В ошибках отсутствующего вердикта сохраняется исходная причина ошибки API/парсинга.

Runner также сохраняет успешный `functional_pass`, даже если последующий security judge упал.
В старом summary этот случай ошибочно превращал успешную функцию в `false`.
Исходные результаты не переписываются. Перепроверка сохранённых ответов после обновления образа:

```bash
.venv/bin/python -u scripts/run_java_ab.py \
  --config configs/java-qwen35-baseline.yaml \
  --replay-from results/java-ab/baseline-smoke-fixed \
  --output results/java-ab/baseline-smoke-fixed2
```

Отрицательные security-тесты XSS/JDBC — результаты модели, а не сбой запуска;
исправлять сгенерированный код перед A/B-сравнением нельзя. Проверки runner/verifier после
исправления жизненного цикла клиента: 18 tests passed.

## Все 31 задача во всех четырёх сценариях

Подготовлены восемь отдельных конфигов в `configs/full/` (два варианта модели × четыре сценария).
В каждом `benchmark: dataset/java.full.json`, один цикл и один явно выбранный сценарий.
Обёртка запускает последовательно `gen`, `gen-hints`, `fix`, `fix-hints`, сохраняя отдельные результаты.
Это 124 оценки для baseline и ещё 124 для VCKG; судейские запросы и MCP-раунды добавляются сверх этого.

Сначала завершите smoke после последнего исправления `Event loop is closed` командой replay выше.
Убедитесь, что в новом `summary.json` поле `errors` равно 0. Отрицательные security-тесты при этом допустимы:
они отражают качество ответа модели, а не техническую ошибку.

Проверить восемь конфигов без API-вызовов:

```bash
bash scripts/run_java_full.sh baseline --dry-run
bash scripts/run_java_full.sh vckg --dry-run
```

Полный baseline на отобранной CWE-выборке:

```bash
cd /root/sec-code-bench
bash scripts/run_java_full.sh baseline
```

Затем полный VCKG:

```bash
bash scripts/run_java_full.sh vckg
```

Результаты: `results/java-ab/full-<baseline|vckg>-<timestamp>-<pid>/<scenario>/summary.json`.
Скрипт выводит фактическую директорию в конце и продолжает оставшиеся сценарии, если один из них
завершился с ошибками. При любой ошибке возвращается ненулевой exit code; провал security-теста
без технической ошибки сам по себе не делает exit code ненулевым. Автоматических повторов
неуспешных сценариев нет. Сравнивать варианты следует отдельно по каждому одноимённому сценарию
через `scripts/compare_java_ab.py`. Это все 31 отобранная Java-задача, а не все 53 upstream-задачи.

### Отрицательный функциональный тест ошибочно выглядел как `zero functional tests`

На полном наборе выявлен случай `DeserializationXMLDecoder`: JUnit выполнил один тест с одним
провалом, но `FunctionTester` выбрасывал исключение, теряя счётчики до записи ответа API.
Теперь отрицательные функциональные вердикты возвращаются как `EvaluatorResult` с исходными
счётчиками. Такой случай получает `function=False`, `security=False`, `error=None`; security
не запускается, поскольку не пройдена функциональность. Настоящие нулевые результаты при сбое
Maven/API по-прежнему считаются технической ошибкой.

Исправление скопировано в работающий контейнер без рестарта активного прогона и включено в
образ `java-verifier:qwen35-fix`. Уже записанные файлы результатов не меняются автоматически.
Повторная проверка сохранённого `DeserializationXMLDecoder` без API модели сохраняется в
`results/java-ab/verifier-diagnostics/DeserializationXMLDecoder.functional-failure-fixed.json`.
Регрессионные проверки после исправления: 21 tests passed.

Ошибки компиляции сгенерированного Java-кода также отделены от технических сбоев:
verifier возвращает явный `failure_stage: compilation`, а runner записывает отрицательный
функциональный результат без `error`. Количество реально выполненных JUnit-тестов остаётся 0:
фиктивные тесты не добавляются. Ошибка зависимостей Maven без признака compilation по-прежнему
считается техническим сбоем.

Если исправление применено во время уже запущенного сценария, его Python-процесс продолжает
работать с загруженным старым runner. Новый runner подхватывается со следующим сценарием/запуском.
Уже сохранённые результаты не исправляются автоматически. Для перепроверки завершённого сценария
без новой генерации используйте его исходный full-конфиг и replay, например:

```bash
.venv/bin/python -u scripts/run_java_ab.py \
  --config configs/full/java-qwen35-baseline-gen.yaml \
  --replay-from results/java-ab/full-baseline-20261123-144804-31784/gen \
  --output results/java-ab/gen-rechecked
```

После разделения отрицательных вердиктов, compilation и инфраструктурных ошибок проходят
23 регрессионных теста. Исходные модельные ответы и эталонные тесты не изменены.

## Общая CSV-статистика по выполненным прогонам

Готовый снимок находится в `results/benchmark-statistics/`:

- `benchmark_all_runs.csv`: 266 исторических оценок плюс итоги и сравнения;
- `comparison_baseline_vckg.csv`: 124 пары задача × сценарий с baseline/VCKG рядом, плюс 5 строк итогов;
- `generated_files.csv`: 266 файловых строк с результатом проекта;
- `summary.csv`: краткие итоги;
- `README.md`, `audit.json`, `analysis.ipynb`: методика, источники, нормализация старой отчётности и выполненный аудит.

CSV записаны в UTF-8 BOM с разделителем `;`. Архив всех файлов — `results/benchmark-statistics.zip`.
Исторические исходные JSON не изменены. Smoke и replay не увеличивают знаменатель full-сравнения.
Новый снимок статистики можно получить без запуска модели:

```bash
.venv/bin/python scripts/export_benchmark_statistics.py
```
