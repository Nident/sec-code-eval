#!/usr/bin/env python3
"""Create Excel workbook and comma-delimited CSV copies of the statistics export."""
import csv
from pathlib import Path
import re
import sys

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

folder = Path(sys.argv[1] if len(sys.argv) > 1 else 'results/benchmark-statistics')
files = ['benchmark_all_runs.csv', 'comparison_baseline_vckg.csv', 'generated_files.csv', 'summary.csv']
data = {}
comma_dir = folder / 'csv_comma'
comma_dir.mkdir(exist_ok=True)
for name in files:
    with (folder / name).open(encoding='utf-8-sig', newline='') as f:
        data[name] = list(csv.reader(f, delimiter=';'))
    with (comma_dir / name).open('w', encoding='utf-8-sig', newline='') as f:
        csv.writer(f, delimiter=',').writerows(data[name])
    with (comma_dir / name).open(encoding='utf-8-sig', newline='') as f:
        assert list(csv.reader(f)) == data[name]


def value(cell):
    if re.fullmatch(r'-?\d+', cell):
        return int(cell)
    if re.fullmatch(r'-?\d+\.\d+', cell):
        return float(cell)
    return cell


book = Workbook()
book.remove(book.active)


def add_sheet(title, rows):
    sheet = book.create_sheet(title)
    for row in rows:
        sheet.append([value(c) if isinstance(c, str) else c for c in row])
    sheet.freeze_panes = 'D2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 56
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='17365D')
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    for column, header in enumerate(rows[0], 1):
        width = 25
        if any(s in str(header) for s in ['Задача', 'Файл', 'Прогон', 'Итог', 'Причина', 'JSON', 'XML']):
            width = 42
        sheet.column_dimensions[get_column_letter(column)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top')
            if cell.data_type == 'f':
                cell.data_type = 's'  # Source text must never become an Excel formula.
            if isinstance(cell.value, float):
                cell.number_format = '0.00'
            if cell.value in ('ПРОШЁЛ', 'УСПЕХ'):
                cell.fill = PatternFill('solid', fgColor='E2F0D9')
            elif cell.value in ('НЕ_ПРОШЁЛ', 'НЕ_КОМПИЛИРУЕТСЯ', 'ОШИБКА_КОМПИЛЯЦИИ_МОДЕЛИ'):
                cell.fill = PatternFill('solid', fgColor='FCE4D6')
            elif cell.value in ('НЕТ_ВЕРДИКТА', 'ОБРЕЗАННАЯ_ГЕНЕРАЦИЯ', 'НЕПОЛНАЯ_ПАРА'):
                cell.fill = PatternFill('solid', fgColor='FFF2CC')
    return sheet


comparison_rows = [dict(zip(data[files[1]][0], r)) for r in data[files[1]][1:]]
totals = [r for r in comparison_rows if r['Тип_строки'] == 'СРАВНЕНИЕ_ИТОГОВ']
headers = ['Сценарий', 'Baseline: балл, %', 'VCKG: балл, %', 'Разница VCKG, п.п.',
           'Baseline: полный PASS', 'VCKG: полный PASS', 'Оценок на вариант',
           'Baseline: функция PASS', 'VCKG: функция PASS', 'Победитель по баллу']
keys = ['Сценарий', 'baseline_балл_процент', 'vckg_балл_процент', 'Разница_VCKG_пп',
        'baseline_полный_PASS', 'vckg_полный_PASS', 'Пар_оценок',
        'baseline_функция_PASS', 'vckg_функция_PASS', 'Победитель_по_взвешенному_баллу']
sheet = add_sheet('Итоги', [headers] + [[r[k] for k in keys] for r in totals])
sheet.auto_filter.ref = 'A1:J6'
notes = [
    'Полный PASS = одновременно прошли функциональность и безопасность.',
    'Общий балл учитывает веса severity и сценария; smoke не входит в full-сравнение.',
    'Один ответ baseline обрезан: неполученный вердикт, в основном балле нулевой вклад.',
    'Результат файла относится к проекту целиком; независимого file-level verdict нет.',
    'Все исходные строки и исторические ошибки доступны на листе «Все прогоны».',
]
for index, text in enumerate(notes, 9):
    sheet.merge_cells(start_row=index, start_column=1, end_row=index, end_column=10)
    cell = sheet.cell(index, 1, text)
    cell.alignment = Alignment(wrap_text=True, vertical='top')
    sheet.row_dimensions[index].height = 30

add_sheet('Сравнение тестов', data['comparison_baseline_vckg.csv'])
add_sheet('Все прогоны', data['benchmark_all_runs.csv'])
add_sheet('Файлы', data['generated_files.csv'])
add_sheet('Подробная сводка', data['summary.csv'])
path = folder / 'benchmark_statistics.xlsx'
book.save(path)
verified = load_workbook(path, read_only=True, data_only=True)
assert verified['Итоги']['B6'].value == value(totals[-1]['baseline_балл_процент'])
assert verified['Итоги']['C6'].value == value(totals[-1]['vckg_балл_процент'])
for sheet_name, name in [('Сравнение тестов', files[1]), ('Все прогоны', files[0]),
                         ('Файлы', files[2]), ('Подробная сводка', files[3])]:
    assert verified[sheet_name].max_row == len(data[name])
verified.close()
print(path)
print('Five Excel sheets and four comma-separated CSV copies verified.')
