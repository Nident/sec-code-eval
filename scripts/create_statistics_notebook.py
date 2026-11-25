#!/usr/bin/env python3
"""Create an executable audit companion for an existing CSV export (no model calls)."""
from pathlib import Path
import sys
import nbformat as nbf

out = Path(sys.argv[1]).resolve()
nb = nbf.v4.new_notebook()
nb.metadata['kernelspec'] = {'display_name': 'SecCodeBench analysis',
                             'language': 'python', 'name': 'sec-code-bench-analysis'}
nb.cells = [
 nbf.v4.new_markdown_cell('''# Проверка CSV-статистики SecCodeBench

Воспроизводимый аудит локального снимка. Источники и веса перечислены в `audit.json`.
Ноутбук не вызывает модели, MCP и verifier, не изменяет исходные прогоны.

## Методика

Сравниваются последние завершённые full suites: по 31 задаче × 4 сценария × 1 цикл.
Smoke/replay остаются в истории, но не входят в основной знаменатель. Статусы нормализуются
по сохранённым verifier JSON и логам; исходные ошибки остаются в CSV.

Это небольшой табличный аудит, поэтому графики не добавляются. Файловый результат относится
к проекту целиком, а не к отдельному независимому тесту каждого файла.'''),
 nbf.v4.new_code_cell('''from pathlib import Path
import hashlib, json, sys
import pandas as pd
EXPORT_DIR = Path.cwd()
ROOT = next(p for p in EXPORT_DIR.parents if (p / 'scripts/export_benchmark_statistics.py').exists())
sys.path.insert(0, str(ROOT / 'scripts'))
import export_benchmark_statistics as stats
audit = json.loads((EXPORT_DIR / 'audit.json').read_text())
for relative_path, digest in audit['source_sha256'].items():
    assert hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() == digest, relative_path
print(f"Исходные JSON совпадают со снимком: {len(audit['source_sha256'])}")
print('Снимок:', audit['created_moscow'])'''),
 nbf.v4.new_markdown_cell('## Полнота и уникальность'),
 nbf.v4.new_code_cell('''all_data = pd.read_csv(EXPORT_DIR / 'benchmark_all_runs.csv', sep=';', keep_default_na=False)
details = all_data[all_data['Тип_строки'] == 'ТЕСТ'].copy()
assert len(details) == audit['test_rows']
assert not details.duplicated(['Прогон', 'Задача', 'Сценарий', 'Цикл']).any()
selected = details[details['В_основном_сравнении'].astype(str) == '1'].copy()
assert selected.groupby(['Вариант', 'Сценарий']).size().eq(31).all()
assert len(selected) == 248
print('Исторических оценок:', len(details))
print('В основном сравнении:', len(selected))
print(selected.groupby(['Вариант', 'Сценарий']).size().to_string())'''),
 nbf.v4.new_markdown_cell('''## Независимый пересчёт по CSV

Полный PASS требует одновременно функционального и security PASS. Неизвестные вердикты
остаются в знаменателе с нулевым вкладом. Для общего балла применяются severity × scenario.'''),
 nbf.v4.new_code_cell('''selected['pass'] = ((selected['Функциональность'] == 'ПРОШЁЛ') &
                    (selected['Безопасность'] == 'ПРОШЁЛ')).astype(int)
selected['weight'] = selected['Severity'].map(audit['severity_weights']) * selected['Сценарий'].map(audit['scenario_weights'])
selected['earned'] = selected['weight'] * selected['pass']
summary = selected.groupby('Вариант').agg(оценок=('pass', 'size'), полный_PASS=('pass', 'sum'),
                                         вес=('weight', 'sum'), набрано=('earned', 'sum'))
summary['балл_процент'] = 100 * summary['набрано'] / summary['вес']
recorded = audit['comparisons'][-1]
assert abs(summary.loc['baseline', 'балл_процент'] - recorded['baseline_балл_процент']) < 1e-8
assert abs(summary.loc['vckg', 'балл_процент'] - recorded['vckg_балл_процент']) < 1e-8
print(summary.round(6).to_string())
print('Разница VCKG, п.п.:', round(summary.loc['vckg', 'балл_процент'] - summary.loc['baseline', 'балл_процент'], 6))'''),
 nbf.v4.new_markdown_cell('## Сравнение сценариев и неполученные вердикты'),
 nbf.v4.new_code_cell('''columns = ['Сценарий', 'baseline_полный_PASS', 'vckg_полный_PASS',
           'baseline_балл_процент', 'vckg_балл_процент', 'Разница_VCKG_пп']
comparison = pd.DataFrame(audit['comparisons'])
print(comparison[columns].round(4).to_string(index=False))
unknown = selected[selected['Техническая_ошибка'].astype(str) == '1']
print('Неполученные вердикты:')
print(unknown[['Вариант', 'Сценарий', 'Задача', 'Итог']].to_string(index=False))
print('Исторических расхождений в отчётности:', len(audit['normalization_discrepancies']))'''),
 nbf.v4.new_markdown_cell('## Парные результаты и файлы'),
 nbf.v4.new_code_cell('''pairs = pd.read_csv(EXPORT_DIR / 'comparison_baseline_vckg.csv', sep=';', keep_default_na=False)
pairs = pairs[pairs['Тип_строки'] == 'СРАВНЕНИЕ_ТЕСТА']
assert len(pairs) == audit['paired_test_rows'] == 124
assert not pairs.duplicated(['Задача', 'Сценарий', 'Цикл']).any()
assert (pairs['Оба_вердикта_получены'].astype(str) == '1').sum() == 123
files = pd.read_csv(EXPORT_DIR / 'generated_files.csv', sep=';', keep_default_na=False)
assert len(files) == audit['file_rows']
assert not files.duplicated(['Прогон', 'Задача', 'Сценарий', 'Цикл', 'Файл']).any()
print('Пар:', len(pairs), '; файловых строк:', len(files))
print('Парный исход:')
print(pairs.groupby('Вывод_при_полных_вердиктах').size().to_string())'''),
 nbf.v4.new_markdown_cell('''## Интерпретация

VCKG имеет больший общий взвешенный балл в этих прогонах; baseline лучше в генерации,
VCKG лучше в исправлении. Число полных PASS отличается всего на одну оценку.
У baseline одна генерация обрезана; sensitivity-результат на одинаковых 123 полных парах
приведён в `summary.csv` и `audit.json`. Один цикл не доказывает устойчивое превосходство.
Разные версии verifier во время раннего baseline и retrieval-инструкция ограничивают причинную интерпретацию.
Подробная методика и ограничения — `README.md`.''')]
nbf.validate(nb)
nbf.write(nb, out / 'analysis.ipynb')
