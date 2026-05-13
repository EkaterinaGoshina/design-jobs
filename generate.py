#!/usr/bin/env python3
"""
Собирает финальный dashboard.html из template.html и jobs.json.

Запуск из корня проекта:
    python3 generate.py

Что делает:
    1. Читает jobs.json (список вакансий)
    2. Читает template.html (HTML-шаблон с плейсхолдерами)
    3. Подставляет JSON-данные и дату снимка в шаблон
    4. Пишет результат в dashboard.html (рядом со скриптом)

Чтобы вручную добавить вакансию: открой jobs.json, скопируй любую строку
из массива "jobs", поправь поля, сохрани, запусти этот скрипт.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

JOBS_FILE            = HERE / "jobs.json"
TEMPLATE_FILE        = HERE / "template.html"
OUTPUT_FILE          = HERE / "dashboard.html"
PIPELINE_TEMPLATE    = HERE / "pipeline-template.html"
PIPELINE_OUTPUT      = HERE / "pipeline.html"
INDEX_TEMPLATE       = HERE / "index-template.html"
INDEX_OUTPUT         = HERE / "index.html"


def main() -> int:
    if not JOBS_FILE.exists():
        print(f"ERROR: {JOBS_FILE} не найден", file=sys.stderr)
        return 1
    if not TEMPLATE_FILE.exists():
        print(f"ERROR: {TEMPLATE_FILE} не найден", file=sys.stderr)
        return 1

    data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    snapshot_date = data.get("snapshot_date", "—")

    # Сортируем: внутри каждого источника свежее (больше pid) идёт сверху
    jobs.sort(key=lambda j: (j["s"], -(j.get("pid") or 0)))

    template = TEMPLATE_FILE.read_text(encoding="utf-8")

    # Сериализуем компактно, но с unicode (русский остаётся читаемым)
    jobs_json = json.dumps(jobs, ensure_ascii=False, separators=(",", ":"))

    # Подставляем плейсхолдеры
    output = template.replace("__SNAPSHOT_DATE__", snapshot_date)
    output = output.replace("__JOBS_JSON__", jobs_json)

    OUTPUT_FILE.write_text(output, encoding="utf-8")

    # Генерируем pipeline.html
    if PIPELINE_TEMPLATE.exists():
        pl_tmpl = PIPELINE_TEMPLATE.read_text(encoding="utf-8")
        pl_out = pl_tmpl.replace("__SNAPSHOT_DATE__", snapshot_date).replace("__JOBS_JSON__", jobs_json)
        PIPELINE_OUTPUT.write_text(pl_out, encoding="utf-8")

    # Генерируем index.html (объединённый дашборд + трекер)
    if INDEX_TEMPLATE.exists():
        ix_tmpl = INDEX_TEMPLATE.read_text(encoding="utf-8")
        ix_out = ix_tmpl.replace("__SNAPSHOT_DATE__", snapshot_date).replace("__JOBS_JSON__", jobs_json)
        INDEX_OUTPUT.write_text(ix_out, encoding="utf-8")

    # Считаем по источникам — для отчёта
    counts: dict = {}
    for j in jobs:
        counts[j["s"]] = counts.get(j["s"], 0) + 1
    src_str = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))

    print(f"OK: {len(jobs)} вакансий → {OUTPUT_FILE}")
    print(f"Снимок: {snapshot_date}")
    print(f"По источникам: {src_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
