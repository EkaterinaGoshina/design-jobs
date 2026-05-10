#!/usr/bin/env python3
"""
Парсит сырые ответы web_fetch в структурированные вакансии для jobs.json.

Поток данных при обновлении дашборда (выполняет Claude):
    1. Claude вызывает web_fetch для каждого URL из sources.json
    2. Claude складывает сырые ответы в design-jobs/raw/<source_name>.txt
    3. Запускается этот скрипт:  python3 design-jobs/parser.py
    4. Скрипт пишет design-jobs/jobs.json с обновлёнными данными
    5. Запускается generate.py — собирает финальный HTML

Парсер сохраняет существующие jobs.json, если в raw/ нет соответствующего файла,
поэтому можно обновлять источники по одному.

Структура поля каждой вакансии:
    s    - имя источника (например "uxwork", "HireHi")
    pid  - id поста / id вакансии в источнике (больше = новее)
    time - HH:MM (только для TG-постов)
    t    - title
    c    - company
    lvl  - middle | junior | senior | lead | unknown
    fmt  - remote | hybrid | office | unknown
    role - product | ux | web | other
    sal  - короткая строка о зарплате/деталях
    note - дополнительная пометка (опционально)
    url  - ссылка на источник вакансии
"""
from __future__ import annotations
import json
import re
import sys
import datetime
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).parent
RAW_DIR = HERE / "raw"
SOURCES_FILE = HERE / "sources.json"
JOBS_FILE = HERE / "jobs.json"

# === Паттерны ===

# Конец TG-поста: "1.5K views[10:15](https://t.me/CHANNEL/12345)"
TG_POST_END = re.compile(
    r'([\d.,]+\s*[KMК]?)\s+views(?:\s*edited)?\s*\['
    r'(\d{2}:\d{2})\]\(https://t\.me/([^/\s)]+)/(\d+)\)'
)

# Карточка HireHi: "[middle Product Designer в Sputnik8, ~ 203 000 ₽, удалённо](https://hirehi.ru/...)"
HIREHI_LINK = re.compile(
    r'\[(intern|junior|middle|senior|lead|head)\s+([^\]]+?)\]\((https://hirehi\.ru/[^)\s"]+)',
    re.IGNORECASE,
)


def detect_level(text: str) -> str:
    t = text.lower()
    if re.search(r'\b(lead|head|principal|тимлид)\b', t):
        return "lead"
    if re.search(r'\bsenior\b|\bсеньор\b|\bстарш', t):
        return "senior"
    if re.search(r'\bmiddle\b|\bмидл\b', t):
        return "middle"
    if re.search(r'\bjunior\b|\bджун\b|\bстаж[её]р\b|\bначинающ', t):
        return "junior"
    return "unknown"


def detect_format(text: str) -> str:
    t = text.lower()
    if re.search(r'удал[её]н|remote', t):
        return "remote"
    if re.search(r'гибрид|hybrid', t):
        return "hybrid"
    if re.search(r'офис|onsite|on-site', t):
        return "office"
    return "unknown"


def detect_role(text: str, role_keywords: dict) -> str:
    t = text.lower()
    for role, kws in role_keywords.items():
        for kw in kws:
            if kw in t:
                return role
    return "other"


def extract_company(title: str) -> str:
    m = re.search(r'\bв\s+([A-ZА-ЯЁ][\wа-яё&.\- ]{2,40})', title)
    if m:
        return re.sub(r'[.,;].*$', '', m.group(1).strip())
    return "—"


def is_excluded(title: str, body: str, exclude_keywords: list[str]) -> bool:
    haystack = (title + " " + body[:500]).lower()
    return any(kw in haystack for kw in exclude_keywords)


def clean_tg_block(block: str) -> str:
    """Убирает фото-заголовки, репосты, эмодзи-реакции из markdown TG-поста."""
    block = re.sub(r'\[\*!\[\]\([^)]*\)\*\]\([^)]*\)', '', block)
    block = re.sub(r'!\[\]\(data:image[^)]*\)', '', block)
    block = re.sub(r'!\[\]\([^)]*\)', '', block)
    block = re.sub(r'\[(?:Channel|UX Work|Заказы для дизайнеров[^]]*|Секретные[^]]*|Connectable[^]]*|A-Teams[^]]*|careerspace|young\.relocate)[^\]]*\]\([^)]*\)', '', block, flags=re.I)
    block = re.sub(r'\[Forwarded from [^\]]*\]\([^)]*\)', '', block)
    block = re.sub(r'Please open Telegram to view this post.*?\[VIEW IN TELEGRAM\]\([^)]*\)', '', block, flags=re.S)
    block = re.sub(r'\[​\]\([^)]*\)', '', block)
    block = re.sub(r'\*{3,}[^*]*\*{3,}\d+', '', block)  # эмодзи-реакции
    block = re.sub(r'\n{3,}', '\n\n', block)
    return block.strip()


def first_meaningful_line(block: str) -> str | None:
    for raw in block.split('\n'):
        line = re.sub(r'\*+', '', raw).strip()
        line = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', line)  # развернуть [text](url) → text
        line = re.sub(r'^[\s\W_]+', '', line)
        if 5 < len(line) < 200 and not line.lower().startswith('forwarded'):
            return line
    return None


def parse_telegram(text: str, channel: str, exclude_keywords: list[str], role_keywords: dict) -> list[dict]:
    jobs: list[dict] = []
    last_end = 0
    for m in TG_POST_END.finditer(text):
        block = text[last_end:m.start()]
        last_end = m.end()
        time_, ch, post_id = m.group(2), m.group(3), int(m.group(4))
        cleaned = clean_tg_block(block)
        if len(cleaned) < 50:
            continue
        title = first_meaningful_line(cleaned)
        if not title:
            continue
        if is_excluded(title, cleaned, exclude_keywords):
            continue
        # Ищем ссылку для отклика — первая внешняя ссылка с ключевыми словами
        apply_url = f"https://t.me/{ch}/{post_id}"
        m2 = re.search(
            r'\[([^\]]*?(?:откликн|ваканс|подроб|описан|hh\.ru|career|jobs)[^\]]*?)\]\((https?://[^)]+)\)',
            cleaned, re.I,
        )
        if m2:
            apply_url = m2.group(2)
        # Описание: вторая+третья непустая строка после заголовка
        lines = [l for l in cleaned.split('\n') if l.strip() and l.strip() != title]
        desc = " ".join(lines[:2])[:240]
        desc = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', desc)
        desc = re.sub(r'\*+', '', desc).strip()

        jobs.append({
            "s": channel,
            "pid": post_id,
            "time": time_,
            "t": title[:140],
            "c": extract_company(title),
            "lvl": detect_level(title + " " + cleaned[:300]),
            "fmt": detect_format(cleaned[:500]),
            "role": detect_role(title + " " + cleaned[:200], role_keywords),
            "sal": desc or None,
            "url": apply_url,
        })
    return jobs


def parse_hirehi(text: str, role_keywords: dict) -> list[dict]:
    jobs: list[dict] = []
    for m in HIREHI_LINK.finditer(text):
        level = m.group(1).lower()
        body = m.group(2)
        url = m.group(3)
        # body = "Position в Company, salary, format[, location]"
        parts = [p.strip() for p in body.split(',')]
        title_part = parts[0]
        salary = parts[1] if len(parts) > 1 else ""
        format_part = ", ".join(parts[2:]) if len(parts) > 2 else ""
        title_split = title_part.split(' в ', 1)
        title = title_split[0].strip()
        company = title_split[1].strip() if len(title_split) > 1 else "—"
        # Извлекаем pid из url
        m_pid = re.search(r'-(\d+)$', url)
        pid = int(m_pid.group(1)) if m_pid else 0
        jobs.append({
            "s": "HireHi",
            "pid": pid,
            "t": title,
            "c": company,
            "lvl": level,
            "fmt": detect_format(format_part),
            "role": detect_role(title, role_keywords),
            "sal": (salary + (", " + format_part if format_part else "")).strip(", "),
            "url": url,
        })
    return jobs


def main() -> int:
    if not SOURCES_FILE.exists():
        print(f"ERROR: {SOURCES_FILE} не найден", file=sys.stderr)
        return 1
    sources_cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    role_keywords = sources_cfg["role_keywords"]
    exclude_keywords = sources_cfg["exclude_keywords"]

    # Существующие jobs — оставляем как fallback для источников без свежих raw-файлов
    existing: dict[str, list[dict]] = {}
    if JOBS_FILE.exists():
        old = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        for j in old.get("jobs", []):
            existing.setdefault(j["s"], []).append(j)

    if not RAW_DIR.exists():
        print(f"WARN: {RAW_DIR} не существует — нечего парсить", file=sys.stderr)
        return 1

    new_by_source: dict[str, list[dict]] = {}
    for src in sources_cfg["sources"]:
        if not src.get("enabled", True):
            continue
        raw_path = RAW_DIR / f"{src['name']}.txt"
        if not raw_path.exists():
            print(f"  skip {src['name']} (нет {raw_path.name})")
            continue
        text = raw_path.read_text(encoding="utf-8")
        if src["kind"] == "tg":
            jobs = parse_telegram(text, src["name"], exclude_keywords, role_keywords)
        elif src["kind"] == "hirehi":
            jobs = parse_hirehi(text, role_keywords)
        else:
            print(f"  WARN: неизвестный kind={src['kind']}")
            continue
        new_by_source[src["name"] if src["kind"] != "hirehi" else "HireHi"] = jobs
        print(f"  parsed {src['name']}: {len(jobs)}")

    # Объединяем: новые перекрывают старые
    merged_by_source = dict(existing)
    merged_by_source.update(new_by_source)

    all_jobs: list[dict] = []
    for arr in merged_by_source.values():
        all_jobs.extend(arr)

    out = {
        "snapshot_date": datetime.date.today().isoformat(),
        "jobs": all_jobs,
    }
    JOBS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nOK: jobs.json обновлён ({len(all_jobs)} вакансий)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
