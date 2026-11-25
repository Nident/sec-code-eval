#!/usr/bin/env python3
"""Export all Java A/B results and a paired full-suite comparison, without changing runs."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ('gen', 'gen-hints', 'fix', 'fix-hints')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
COUNTS = re.compile(r'Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)')
PASS, FAIL, NOT_RUN, UNKNOWN = 'ПРОШЁЛ', 'НЕ_ПРОШЁЛ', 'НЕ_ЗАПУСКАЛСЯ', 'НЕТ_ВЕРДИКТА'


def read(p):
    return json.loads(p.read_text())


def relative(p):
    return str(p.resolve().relative_to(ROOT))


def clean(text):
    return ANSI.sub('', text or '').strip()


def counts_pass(c):
    return c.get('total_tests', 0) > 0 and all(c.get(k, 0) == 0 for k in
        ('total_failures', 'total_errors', 'total_skipped'))


def normalize(raw, original):
    """Use verifier evidence, retaining original error separately. Never invent test counts."""
    raw = raw or {}
    result = raw.get('test_result', {})
    f, s = dict(result.get('functional_result', {})), dict(result.get('security_result', {}))
    diagnostic = clean('\n'.join(str(raw.get(k) or '') for k in
                                ('stderr', 'function_stderr', 'security_stderr')))
    note = ''
    stage = raw.get('failure_stage')
    if stage != 'compilation' and diagnostic.startswith('Java syntax or compilation error:'):
        stage = 'compilation'
        note = 'Историческая ошибка runner: компиляция была записана как техническая ошибка'
    if not f.get('total_tests') and not stage:
        matches = COUNTS.findall(diagnostic)
        if matches and diagnostic.startswith('Error message:'):
            n, fail, err, skip = map(int, matches[-1])
            if n > 0 and fail + err > 0:
                f = dict(zip(('total_tests', 'total_failures', 'total_errors', 'total_skipped'),
                             (n, fail, err, skip)))
                note = 'Счётчики JUnit восстановлены из сохранённого Maven-лога; исходный JSON не изменён'
    error = False
    if stage == 'compilation':
        fp, sp, fs, ss = False, False, 'НЕ_КОМПИЛИРУЕТСЯ', NOT_RUN
        status = 'ОШИБКА_КОМПИЛЯЦИИ_МОДЕЛИ'
    elif f.get('total_tests', 0) > 0:
        fp = counts_pass(f)
        fs = PASS if fp else FAIL
        if not fp:
            sp, ss, status = False, NOT_RUN, 'ФУНКЦИОНАЛЬНЫЙ_ТЕСТ_НЕ_ПРОШЁЛ'
        elif s.get('total_tests', 0) > 0:
            sp = counts_pass(s)
            ss, status = (PASS, 'УСПЕХ') if sp else (FAIL, 'SECURITY_ТЕСТ_НЕ_ПРОШЁЛ')
        else:
            sp, ss, status, error = None, UNKNOWN, 'ОШИБКА_SECURITY_ОЦЕНКИ', True
    else:
        fp, sp, fs, ss, error = None, None, UNKNOWN, UNKNOWN, True
        status = 'ОБРЕЗАННАЯ_ГЕНЕРАЦИЯ' if 'Generation truncated' in (original.get('error') or '') else 'ОШИБКА_ОЦЕНКИ'
    if original.get('functional_pass') != fp and fp is True:
        note = (note + '; ' if note else '') + 'Функциональный PASS подтверждён verifier, несмотря на старый result.json'
    if original.get('error') and not error and not note:
        note = 'Исходный error не является техническим сбоем: сохранённый verifier содержит завершённые тесты'
    return {'fp': fp, 'sp': sp, 'fs': fs, 'ss': ss, 'status': status, 'error': error,
            'f': f, 's': s, 'stage': stage or '', 'note': note,
            'diagnostic': diagnostic or original.get('error') or ''}


def final_files(answer):
    if not answer:
        return []
    answer = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', answer, flags=re.S | re.I)
    if re.search(r'<think\b', answer, re.I):
        return []
    candidates = re.findall(r'<result>.*?</result>', answer, flags=re.S)
    if not candidates:
        return []
    try:
        return sorted({(c.findtext('path') or '').strip() for c in ET.fromstring(candidates[-1]).findall('code')} - {''})
    except ET.ParseError:
        return []


def load_runs(source):
    mapping = {r['case_id']: r['primary_cwe'] for r in read(ROOT / 'dataset/manifest.json')['cases']}
    runs = []
    for config_path in sorted(source.rglob('config.json')):
        folder = config_path.parent
        if not (folder / 'dataset.json').exists():
            continue
        cfg, data = read(config_path), read(folder / 'dataset.json')
        if cfg.get('arm') not in ('baseline', 'vckg'):
            continue
        rows = []
        expected = len(data) * cfg['cycles']
        for name, case in data.items():
            for cycle in range(cfg['cycles']):
                stem = f'{name}.{cycle}'
                path = folder / (stem + '.result.json')
                if not path.exists():
                    continue
                original = read(path)
                vp, xp, tp = (folder / (stem + x) for x in ('.verification.json', '.xml', '.trace.json'))
                raw = read(vp) if vp.exists() else {}
                normalized = normalize(raw, original)
                trace = read(tp) if tp.exists() else {}
                calls = trace.get('tool_calls', [])
                rows.append({'run': relative(folder), 'folder': folder, 'suite': relative(folder.parent),
                             'arm': cfg['arm'], 'scenario': cfg['scenario'], 'cycle': cycle,
                             'case': name, 'cwe': mapping[name], 'severity': case['severity'],
                             'case_data': case, 'model': cfg['model']['name'],
                             'judge': cfg['judge']['name'], 'original': original,
                             'seconds': original.get('seconds', 0), 'raw': raw,
                             'verification_path': relative(vp) if vp.exists() else '',
                             'result_path': relative(path), 'answer_path': relative(xp) if xp.exists() else '',
                             'trace_path': relative(tp) if tp.exists() else '',
                             'files': final_files(xp.read_text()) if xp.exists() else [],
                             'mcp_calls': len(calls),
                             'mcp_searches': sum(c['name'].startswith('vckg_search_') for c in calls),
                             **normalized})
        complete = ((folder / 'finish').exists() or (folder / 'finish_with_errors').exists()) and len(rows) == expected
        runs.append({'folder': folder, 'run': relative(folder), 'cfg': cfg, 'data': data, 'rows': rows,
                     'complete': complete, 'expected': expected, 'selected': False,
                     'provenance': read(folder / 'provenance.json'),
                     'mtime': max(p.stat().st_mtime for p in folder.glob('*.result.json')) if rows else 0})
    return runs


def select_full(runs):
    """Latest completed *suite*, never cherry-pick the best task/retry or mix smoke with full."""
    selected = {}
    wanted = set(read(ROOT / 'dataset/java.full.json'))
    for arm in ('baseline', 'vckg'):
        candidates = {}
        for run in runs:
            if run['cfg']['arm'] == arm and run['complete'] and set(run['data']) == wanted:
                candidates.setdefault(run['folder'].parent, []).append(run)
        candidates = {p: rs for p, rs in candidates.items()
                      if len(rs) == 4 and {r['cfg']['scenario'] for r in rs} == set(SCENARIOS)}
        if not candidates:
            raise ValueError(f'No complete four-scenario suite for {arm}')
        suite = max(candidates, key=lambda p: max(r['mtime'] for r in candidates[p]))
        for run in candidates[suite]:
            run['selected'] = True
            selected[(arm, run['cfg']['scenario'])] = run
    for sc in SCENARIOS:
        a, b = selected[('baseline', sc)], selected[('vckg', sc)]
        if a['provenance'] != b['provenance']:
            raise ValueError(f'Unmatched dataset/prompt provenance for {sc}')
        for key in ('model', 'judge', 'cycles', 'concurrency', 'rpm_limit'):
            if a['cfg'][key] != b['cfg'][key]:
                raise ValueError(f'Unmatched config {key} for {sc}')
        if {(r['case'], r['cycle']) for r in a['rows']} != {(r['case'], r['cycle']) for r in b['rows']}:
            raise ValueError('Unmatched case/cycle population')
    return selected


def metrics(rows, severity_weights, scenario_weights, combined=True):
    def weight(row):
        return severity_weights[row['severity']] * (scenario_weights[row['scenario']] if combined else 1)
    denominator = sum(weight(r) for r in rows)
    return {'Оценок': len(rows), 'Функция_PASS': sum(r['fp'] is True for r in rows),
            'Функция_FAIL': sum(r['fp'] is False for r in rows),
            'Функция_неизвестно': sum(r['fp'] is None for r in rows),
            'Функция_и_security_PASS': sum(r['fp'] is True and r['sp'] is True for r in rows),
            'Технические_ошибки': sum(r['error'] for r in rows),
            'Обрезанные_генерации': sum(r['status'] == 'ОБРЕЗАННАЯ_ГЕНЕРАЦИЯ' for r in rows),
            'Ошибки_компиляции': sum(r['stage'] == 'compilation' for r in rows),
            'Исходных_error': sum(bool(r['original'].get('error')) for r in rows),
            'Функция_PASS_процент': 100 * sum(r['fp'] is True for r in rows) / len(rows),
            'Функция_и_security_PASS_процент': 100 * sum(r['fp'] is True and r['sp'] is True for r in rows) / len(rows),
            'Взвешенный_балл_процент': 100 * sum(weight(r) * (r['fp'] is True and r['sp'] is True) for r in rows) / denominator,
            'Вес_знаменатель': denominator, 'Сумма_секунд_задач': sum(r['seconds'] for r in rows),
            'MCP_вызовов': sum(r['mcp_calls'] for r in rows)}


def csv_write(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fields, delimiter=';', lineterminator='\n')
        w.writeheader()
        for row in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()})
    # Read the export back: no dropped/malformed rows or shifted columns.
    with path.open(encoding='utf-8-sig', newline='') as f:
        checked = list(csv.DictReader(f, delimiter=';'))
    assert len(checked) == len(rows) and all(None not in r for r in checked)


def detail(row, selected):
    r = {'Тип_строки': 'ТЕСТ', 'Прогон': row['run'], 'Вариант': row['arm'],
         'Сценарий': row['scenario'], 'Цикл': row['cycle'], 'Задача': row['case'],
         'CWE': row['cwe'], 'Severity': row['severity'],
         'В_основном_сравнении': int(selected), 'Модель': row['model'], 'Судья': row['judge'],
         'Функциональность': row['fs'], 'Безопасность': row['ss'],
         'Итог': row['status'], 'Техническая_ошибка': int(row['error']),
         'Исходный_function_PASS': int(row['original']['functional_pass']),
         'Исходный_security_PASS': int(row['original']['security_pass']),
         'Исходный_error': clean(row['original'].get('error')),
         'Уточнение_статуса': row['note'], 'Стадия_ошибки': row['stage'],
         'Секунд': row['seconds'], 'MCP_вызовов': row['mcp_calls'], 'MCP_поисков': row['mcp_searches'],
         'Ожидаемые_файлы': ' | '.join(row['case_data']['params'].values()),
         'Сгенерированные_файлы': ' | '.join(row['files']),
         'Причина': clean(row['diagnostic'])[-4000:] if row['status'] != 'УСПЕХ' else '',
         'JSON_результат': row['result_path'], 'JSON_verifier': row['verification_path'],
         'XML_ответ': row['answer_path'], 'JSON_trace': row['trace_path']}
    for label, key in [('Функциональных', 'f'), ('Security', 's')]:
        c = row[key]
        for title, raw in [('всего', 'total_tests'), ('провалено', 'total_failures'),
                           ('ошибок', 'total_errors'), ('пропущено', 'total_skipped')]:
            r[label + '_' + title] = c.get(raw, 0)
        r[label + '_прошло'] = max(0, c.get('total_tests', 0) - sum(c.get(k, 0) for k in
                                   ('total_failures', 'total_errors', 'total_skipped')))
    return r


def verdict_winner(a, b):
    return 'VCKG' if b > a else ('baseline' if a > b else 'НИЧЬЯ')


def export(source, out):
    runs = load_runs(source)
    assert runs and len({r['run'] for r in runs}) == len(runs)
    selected = select_full(runs)
    weights = yaml.safe_load((ROOT / 'system_config.yaml').read_text())['score_weights']
    sw, scw = weights['category_weights'], weights['scenario_weights']
    all_rows, file_rows, totals, paired, comparisons, warnings = [], [], [], [], [], []
    for run in runs:
        keys = {(r['case'], r['cycle']) for r in run['rows']}
        assert len(keys) == len(run['rows'])
        total = {'Тип_строки': 'ИТОГ_ПРОГОНА', 'Прогон': run['run'],
                 'Вариант': run['cfg']['arm'], 'Сценарий': run['cfg']['scenario'],
                 'В_основном_сравнении': int(run['selected']), 'Прогон_завершён': int(run['complete']),
                 **metrics(run['rows'], sw, scw, combined=False)}
        totals.append(total)
        for row in run['rows']:
            d = detail(row, run['selected'])
            all_rows.append(d)
            for path in sorted(set(row['case_data']['params'].values()) | set(row['files'])):
                file_rows.append({'Прогон': row['run'], 'Вариант': row['arm'], 'Сценарий': row['scenario'],
                    'Цикл': row['cycle'], 'Задача': row['case'], 'CWE': row['cwe'], 'Файл': path,
                    'Ожидался_заданием': int(path in row['case_data']['params'].values()),
                    'Есть_в_финальном_XML': int(path in row['files']),
                    'Функциональность_проекта': row['fs'], 'Безопасность_проекта': row['ss'],
                    'Итог_проекта': row['status'], 'Самостоятельный_вердикт_файла': 'НЕТ: тестируется проект целиком',
                    'В_основном_сравнении': int(run['selected']),
                    'XML_ответ': row['answer_path'], 'JSON_verifier': row['verification_path']})
        original_summary = read(run['folder'] / 'summary.json') if (run['folder'] / 'summary.json').exists() else None
        if original_summary:
            if original_summary['errors'] != total['Технические_ошибки'] or original_summary['functional_pass'] != total['Функция_PASS']:
                warnings.append({'run': run['run'], 'original_errors': original_summary['errors'],
                                 'normalized_errors': total['Технические_ошибки'],
                                 'original_functional_pass': original_summary['functional_pass'],
                                 'normalized_functional_pass': total['Функция_PASS']})
            assert abs(original_summary['weighted_secure_percent'] - total['Взвешенный_балл_процент']) < 1e-8
    populations = {arm: [row for sc in SCENARIOS for row in selected[(arm, sc)]['rows']]
                   for arm in ('baseline', 'vckg')}
    for arm, rows in populations.items():
        for sc in SCENARIOS:
            totals.append({'Тип_строки': 'ИТОГ_ВАРИАНТА_СЦЕНАРИЙ', 'Вариант': arm, 'Сценарий': sc,
                           'В_основном_сравнении': 1, **metrics([r for r in rows if r['scenario'] == sc], sw, scw)})
        totals.append({'Тип_строки': 'ИТОГ_ВАРИАНТА_ВСЕ_СЦЕНАРИИ', 'Вариант': arm, 'Сценарий': 'ВСЕ',
                       'В_основном_сравнении': 1, **metrics(rows, sw, scw)})
    for sc in SCENARIOS:
        a = {(r['case'], r['cycle']): r for r in selected[('baseline', sc)]['rows']}
        b = {(r['case'], r['cycle']): r for r in selected[('vckg', sc)]['rows']}
        for key in sorted(a):
            x, y = a[key], b[key]
            success_a, success_b = x['fp'] is True and x['sp'] is True, y['fp'] is True and y['sp'] is True
            comparable = not x['error'] and not y['error']
            row = {'Тип_строки': 'СРАВНЕНИЕ_ТЕСТА', 'Задача': key[0], 'Цикл': key[1],
                   'Сценарий': sc, 'CWE': x['cwe'], 'Severity': x['severity'],
                   'Вес': sw[x['severity']] * scw[sc], 'Оба_вердикта_получены': int(comparable),
                   'Победитель_по_результату': verdict_winner(success_a, success_b),
                   'Вывод_при_полных_вердиктах': verdict_winner(success_a, success_b) if comparable else 'НЕПОЛНАЯ_ПАРА',
                   'Изменение_балла_VCKG': int(success_b) - int(success_a)}
            for arm, r in [('baseline', x), ('vckg', y)]:
                row.update({arm + '_функция': r['fs'], arm + '_security': r['ss'],
                            arm + '_итог': r['status'], arm + '_техническая_ошибка': int(r['error']),
                            arm + '_файлы': ' | '.join(r['case_data']['params'].values()),
                            arm + '_секунд': r['seconds'], arm + '_MCP_вызовов': r['mcp_calls'],
                            arm + '_JSON_результат': r['result_path'], arm + '_JSON_verifier': r['verification_path']})
            paired.append(row)
    for sc in (*SCENARIOS, 'ВСЕ'):
        a = [r for r in populations['baseline'] if sc == 'ВСЕ' or r['scenario'] == sc]
        b = [r for r in populations['vckg'] if sc == 'ВСЕ' or r['scenario'] == sc]
        ma, mb = metrics(a, sw, scw), metrics(b, sw, scw)
        pairs = [r for r in paired if sc == 'ВСЕ' or r['Сценарий'] == sc]
        comparable_keys = {(r['Задача'], r['Сценарий'], r['Цикл']) for r in pairs if r['Оба_вердикта_получены']}
        aa = [r for r in a if (r['case'], r['scenario'], r['cycle']) in comparable_keys]
        bb = [r for r in b if (r['case'], r['scenario'], r['cycle']) in comparable_keys]
        ca, cb = metrics(aa, sw, scw), metrics(bb, sw, scw)
        comp = {'Тип_строки': 'СРАВНЕНИЕ_ИТОГОВ', 'Сценарий': sc, 'Пар_оценок': len(pairs),
                'Пар_без_технических_ошибок': len(aa),
                'baseline_функция_PASS': ma['Функция_PASS'], 'vckg_функция_PASS': mb['Функция_PASS'],
                'baseline_полный_PASS': ma['Функция_и_security_PASS'], 'vckg_полный_PASS': mb['Функция_и_security_PASS'],
                'baseline_балл_процент': ma['Взвешенный_балл_процент'],
                'vckg_балл_процент': mb['Взвешенный_балл_процент'],
                'Разница_VCKG_пп': mb['Взвешенный_балл_процент'] - ma['Взвешенный_балл_процент'],
                'Победитель_по_взвешенному_баллу': verdict_winner(ma['Взвешенный_балл_процент'], mb['Взвешенный_балл_процент']),
                'baseline_ошибок': ma['Технические_ошибки'], 'vckg_ошибок': mb['Технические_ошибки'],
                'VCKG_улучшил_задач': sum(r['Изменение_балла_VCKG'] == 1 for r in pairs),
                'VCKG_ухудшил_задач': sum(r['Изменение_балла_VCKG'] == -1 for r in pairs),
                'baseline_балл_только_полные_пары': ca['Взвешенный_балл_процент'],
                'vckg_балл_только_полные_пары': cb['Взвешенный_балл_процент'],
                'Разница_только_полные_пары_пп': cb['Взвешенный_балл_процент'] - ca['Взвешенный_балл_процент']}
        comparisons.append(comp)
    out.mkdir(parents=True, exist_ok=False)
    csv_write(out / 'benchmark_all_runs.csv', all_rows + totals + comparisons)
    csv_write(out / 'comparison_baseline_vckg.csv', paired + comparisons)
    csv_write(out / 'generated_files.csv', file_rows)
    csv_write(out / 'summary.csv', totals + comparisons)
    files = {p for run in runs for p in run['folder'].glob('*.json')}
    audit = {'created_moscow': datetime.now(ZoneInfo('Europe/Moscow')).isoformat(),
             'source': relative(source), 'run_count': len(runs), 'test_rows': len(all_rows),
             'paired_test_rows': len(paired), 'file_rows': len(file_rows),
             'selected_suites': {arm: relative(selected[(arm, 'gen')]['folder'].parent) for arm in populations},
             'normalization_discrepancies': warnings,
             'normalization_notes': dict(Counter(r['note'] for run in runs for r in run['rows'] if r['note'])),
             'severity_weights': sw, 'scenario_weights': scw,
             'source_sha256': {relative(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
             'comparisons': comparisons}
    (out / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    write_readme(out, audit)
    print(json.dumps({k: audit[k] for k in ('run_count', 'test_rows', 'paired_test_rows', 'file_rows', 'comparisons')}, ensure_ascii=False, indent=2))
    return audit


def write_readme(out, audit):
    text = '''# Статистика Java benchmark

- `benchmark_all_runs.csv` — общий файл: все исторические тестовые случаи, итоги прогонов, итоги двух вариантов и сравнение.
- `comparison_baseline_vckg.csv` — 124 пары задача × сценарий × цикл, baseline и VCKG рядом; в конце 5 строк итогов.
- `generated_files.csv` — все ожидаемые/фактически извлечённые имена файлов с результатом их проекта.
- `summary.csv` — только итоги и сравнение.
- `audit.json` — выбранные прогоны, разночтения со старой отчётностью, веса и SHA-256 исходных JSON.
- `analysis.ipynb` — воспроизводимая проверка экспорта и расчётов.

CSV: UTF-8 BOM, разделитель `;`, первая строка — русские заголовки. Пустое поле означает неприменимость.
Общий CSV содержит разные типы строк: для списка отдельных оценок установите фильтр `Тип_строки=ТЕСТ`.
Для основного сравнения добавьте `В_основном_сравнении=1`: smoke/replay не увеличивают итоговый знаменатель.
Итоговые строки нельзя суммировать вместе с детализацией — это один и тот же результат на разных уровнях.

## Методика

Источники — локальные result.json, verification.json, config.json, dataset.json и provenance.json.
Три smoke-прогона сохранены в истории. Для сравнения выбраны последние завершённые full suites,
по 31 задаче × 4 сценария × 1 цикл на вариант. Хеши prompt/выборки и конфиги моделей/судьи сопоставлены.
Первичный ключ детали: прогон + задача + сценарий + цикл; пары: задача + сценарий + цикл.

PASS безопасности засчитывается только вместе с PASS функциональности. Если функция не прошла,
security не запускается. Это отрицательный итог модели, а не неполученный security-вердикт.
При ошибке компиляции не придумываются JUnit-тесты: функция FAIL, выполненных тестов 0.
Для старых записей основной статус восстановлен из фактического verifier-ответа/сохранённого Maven-лога;
исходные флаги и исходный error сохранены отдельными колонками. Исходные результаты не переписывались.
Исторические компиляционные ошибки распознаются по явному сообщению производителя `Java syntax or compilation error`;
провал JUnit с потерянными счётчиками — по финальным счётчикам Tests run/Failures/Errors/Skipped в его логе.

Обрезанная генерация — неполученный вердикт, а не доказательство уязвимости. В основном балле остаётся
в знаменателе с нулевым вкладом, как в runner. Дополнительно приведён балл на одинаковых полных парах,
исключающий пару с неполученным вердиктом в любой ветке. Это анализ чувствительности, не замена основного балла.

Балл сценария = 100 × сумма весов severity успешных задач / сумма весов severity всех задач.
Общий балл = 100 × сумма (severity × scenario × полный PASS) / сумма (severity × scenario).
Используются веса system_config.yaml: gen/fix=4, gen-hints/fix-hints=1; severity приведены в audit.json.
Простой процент успешных задач и взвешенный балл — разные показатели. Smoke не суммируется с full.
`Сумма_секунд_задач` — сумма длительностей с учётом параллельных задач, не wall-clock время прогона.

## Ограничения

Один цикл на задачу: результат описывает эти прогоны, не доказывает статистически устойчивое превосходство.
Вариант VCKG включает MCP и дополнительную retrieval-инструкцию. Их эффекты отдельно не измеряются.
Verifier исправлялся во время раннего baseline; восстановление отчётности отмечено, одинаковая версия
всех частей окружения на протяжении обоих прогонов не гарантируется.

Результат относится к ПРОЕКТУ/ЗАДАЧЕ. При нескольких файлах каждому в файловой таблице показан результат
всего проекта; независимый file-level verdict отсутствует. Файловые строки нельзя суммировать как число задач.
Полные Surefire XML с именами всех успешных JUnit-методов не сохранялись; доступны агрегированные счётчики
и сообщения о проваленных методах. Имена якобы прошедших методов не выдумываются.
Наличие файла в финальном XML — отдельный признак, не доказательство самостоятельного PASS этого файла.
Причины ограничены последними 4000 символами; полный stdout/stderr доступен по пути JSON_verifier.
Все пути относительно /root/sec-code-bench. Программный расчёт: scripts/export_benchmark_statistics.py.
'''
    text += '\n## Снимок и выбранные прогоны\n\n' + audit['created_moscow'] + '\n\n'
    for arm, suite in audit['selected_suites'].items():
        text += f'- {arm}: `{suite}`\n'
    text += '\n## Итоговый взвешенный балл\n\n| Сценарий | baseline | VCKG | Разница VCKG, п.п. |\n|---|---:|---:|---:|\n'
    for c in audit['comparisons']:
        text += (f"| {c['Сценарий']} | {c['baseline_балл_процент']:.2f}% | "
                 f"{c['vckg_балл_процент']:.2f}% | {c['Разница_VCKG_пп']:+.2f} |\n")
    total = audit['comparisons'][-1]
    text += (f"\nПолный PASS: baseline {total['baseline_полный_PASS']}/124, "
             f"VCKG {total['vckg_полный_PASS']}/124. Функциональный PASS: "
             f"baseline {total['baseline_функция_PASS']}/124, VCKG {total['vckg_функция_PASS']}/124.\n")
    text += '''\nПересобрать статистику в новой директории (исходные прогоны не изменяются):

```bash
cd /root/sec-code-bench
.venv/bin/python scripts/export_benchmark_statistics.py
```

Последующий необязательный notebook-аудит:

```bash
uv pip install --python .venv/bin/python nbformat nbclient ipykernel
.venv/bin/python scripts/create_statistics_notebook.py <директория_нового_экспорта>
```
'''
    (out / 'README.md').write_text(text, encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT / 'results/java-ab')
    p.add_argument('--output', type=Path, default=ROOT / 'results' / ('benchmark-statistics-' +
                   datetime.now(ZoneInfo('Europe/Moscow')).strftime('%Y%m%d-%H%M%S')))
    args = p.parse_args()
    export(args.source, args.output)


if __name__ == '__main__':
    main()
