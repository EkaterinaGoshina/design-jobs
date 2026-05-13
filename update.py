#!/usr/bin/env python3
"""
Автономное обновление дашборда: fetch → parse → generate.
Запуск: python3 update.py
Требует: pip install requests beautifulsoup4
"""
import json, re, sys, datetime, time
import requests
from bs4 import BeautifulSoup
from pathlib import Path

HERE = Path(__file__).parent
SOURCES_FILE = HERE / "sources.json"
JOBS_FILE = HERE / "jobs.json"
OUTPUT_FILE = HERE / "dashboard.html"
TEMPLATE_FILE = HERE / "template.html"
INDEX_TEMPLATE_FILE = HERE / "index-template.html"
INDEX_OUTPUT_FILE = HERE / "index.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def fetch_html(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                print(f"  ERROR fetching {url}: {e}", file=sys.stderr)
                return None
            time.sleep(3)


def detect_level(text):
    t = text.lower()
    if re.search(r'\b(lead|head|principal|тимлид)\b', t): return "lead"
    if re.search(r'\bsenior\b|\bсеньор\b|\bстарш', t): return "senior"
    if re.search(r'\bmiddle\b|\bмидл\b', t): return "middle"
    if re.search(r'\bjunior\b|\bджун\b|\bстаж[её]р\b|\bначинающ', t): return "junior"
    return "unknown"


def detect_format(text):
    t = text.lower()
    if re.search(r'удал[её]н|remote', t): return "remote"
    if re.search(r'гибрид|hybrid', t): return "hybrid"
    if re.search(r'офис|onsite|on-site', t): return "office"
    return "unknown"


def detect_role(text, role_keywords):
    t = text.lower()
    # "other" проверяем первым — графический/веб/геймдев всегда вытесняют UX/product
    for kw in role_keywords.get("other", []):
        if kw in t: return "other"
    for role in ("product", "ux"):
        for kw in role_keywords.get(role, []):
            if kw in t: return role
    return "other"


VACANCY_SIGNALS = [
    "ищем", "ищет", "нужен", "нужна", "нужны", "требуется", "требуются",
    "вакансия", "вакансии", "открыта вакансия", "открыт набор",
    "приглашаем", "приглашает",
    "зарплата", "заработная плата", "оплата", "ставка", "от ", "₽",
    "обязанност", "задачи", "требован", "стек", "условия работы",
    "опыт от", "опыт работы",
    "резюме", "портфолио", "откликн",
    "full-time", "fulltime", "part-time", "фриланс", "freelance",
    "удалён", "удаленн", "remote", "офис", "гибрид",
    "vacancy", "hiring", "we're looking", "we are looking",
    "job", "position", "role",
]

NON_VACANCY_SIGNALS = [
    "обучени", "курс", "интенсив", "воркшоп", "вебинар", "онлайн-школ",
    "скидк", "промокод", "регистрир", "бесплатн",
    "крипт", "p2p", "вывод", "спб", "сбп",
    "подписывайся", "подписывайтесь", "читайте", "узнайте",
    "а вы знали", "совет дня", "лайфхак", "как стать", "как найти",
    "как совладать", "как быть", "по моему опыту",
    "реклама", "партнёрский", "промо",
]


def is_excluded(title, body, exclude_keywords):
    haystack = (title + " " + body[:500]).lower()
    return any(kw in haystack for kw in exclude_keywords)


def is_vacancy(title, body):
    haystack = (title + " " + body[:800]).lower()
    if any(s in haystack for s in NON_VACANCY_SIGNALS):
        return False
    return any(s in haystack for s in VACANCY_SIGNALS)


def extract_company(title):
    m = re.search(r'\bв\s+([A-ZА-ЯЁ][\wа-яё&.\- ]{2,40})', title)
    if m:
        return re.sub(r'[.,;].*$', '', m.group(1).strip())
    return "—"


def parse_telegram(html, channel, exclude_keywords, role_keywords):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    for msg_wrap in soup.select(".tgme_widget_message_wrap"):
        msg = msg_wrap.select_one(".tgme_widget_message")
        if not msg:
            continue

        data_post = msg.get("data-post", "")
        m = re.search(r'/(\d+)$', data_post)
        if not m:
            continue
        post_id = int(m.group(1))

        time_el = msg.select_one("a.tgme_widget_message_date time")
        if not time_el:
            continue
        datetime_attr = time_el.get("datetime", "")
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', datetime_attr)
        if date_match:
            pub_date = date_match.group(1)
            time_str = date_match.group(2)
        else:
            pub_date = None
            time_str = time_el.get_text(strip=True)[:5]

        text_el = msg.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text(separator="\n", strip=True)
        if len(text) < 30:
            continue

        title = None
        for line in text.split("\n"):
            line = re.sub(r'[*_`]+', '', line).strip()
            if 5 < len(line) < 200 and not line.lower().startswith("forwarded"):
                title = line
                break
        if not title:
            continue
        if is_excluded(title, text, exclude_keywords):
            continue
        if not is_vacancy(title, text):
            continue

        apply_url = f"https://t.me/{channel}/{post_id}"
        for link in text_el.find_all("a", href=True):
            href = link.get("href", "")
            link_text = link.get_text(strip=True).lower()
            if any(kw in link_text or kw in href.lower()
                   for kw in ["откликн", "ваканс", "подроб", "hh.ru", "career", "jobs"]):
                apply_url = href
                break

        lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() != title]
        desc = " ".join(lines[:2])[:240]

        jobs.append({
            "s": channel,
            "pid": post_id,
            "date": pub_date,
            "time": time_str,
            "t": title[:140],
            "c": extract_company(title),
            "lvl": detect_level(title + " " + text[:300]),
            "fmt": detect_format(text[:500]),
            "role": detect_role(title + " " + text[:200], role_keywords),
            "sal": desc or None,
            "url": apply_url,
        })

    return jobs


def parse_hirehi(html, role_keywords):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    levels = {"intern", "junior", "middle", "senior", "lead", "head"}

    for a in soup.find_all("a", href=re.compile(r'/design/')):
        href = a.get("href", "")
        if not href.startswith("http"):
            href = "https://hirehi.ru" + href

        text = a.get_text(strip=True)
        if not text:
            continue

        words = text.split()
        if not words or words[0].lower() not in levels:
            continue

        level = words[0].lower()
        rest = text[len(level):].strip()
        parts = [p.strip() for p in rest.split(',')]

        title_part = parts[0]
        salary = parts[1] if len(parts) > 1 else ""
        format_part = ", ".join(parts[2:]) if len(parts) > 2 else ""

        title_split = title_part.split(' в ', 1)
        title = title_split[0].strip()
        company = title_split[1].strip() if len(title_split) > 1 else "—"

        m_pid = re.search(r'-(\d+)$', href)
        pid = int(m_pid.group(1)) if m_pid else 0
        if pid == 0:
            continue

        sal = (salary + (", " + format_part if format_part else "")).strip(", ")
        jobs.append({
            "s": "HireHi",
            "pid": pid,
            "t": title,
            "c": company,
            "lvl": level,
            "fmt": detect_format(format_part),
            "role": detect_role(title, role_keywords),
            "sal": sal or None,
            "url": href,
        })

    seen = set()
    unique = []
    for j in jobs:
        if j["pid"] not in seen:
            seen.add(j["pid"])
            unique.append(j)
    return unique


def parse_hh(src, exclude_keywords, role_keywords):
    HH_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "HH-User-Agent": "design-jobs-dashboard/1.0 (000korsa000@gmail.com)",
    }
    seen_ids = set()
    jobs = []
    date_from = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()

    for query in src.get("queries", []):
        params = {
            "text": query,
            "per_page": 50,
            "order_by": "publication_time",
            "date_from": date_from,
        }
        try:
            r = requests.get(src["url"], params=params, headers=HH_HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  hh.ru error for '{query}': {e}", file=sys.stderr)
            continue

        for v in data.get("items", []):
            vid = int(v["id"])
            if vid in seen_ids:
                continue
            seen_ids.add(vid)

            title = v.get("name", "")
            if is_excluded(title, "", exclude_keywords):
                continue

            exp = v.get("experience", {}).get("id", "")
            lvl = {"noExperience": "junior", "between1And3": "middle",
                   "between3And6": "senior", "moreThan6": "lead"}.get(exp, "unknown")

            sched = v.get("schedule", {}).get("id", "")
            fmt = {"remote": "remote", "fullDay": "office",
                   "flexible": "hybrid"}.get(sched, "unknown")

            sal_obj = v.get("salary") or {}
            sal_parts = []
            if sal_obj.get("from"):
                sal_parts.append(f"от {sal_obj['from']:,}".replace(",", " ") + " ₽")
            if sal_obj.get("to"):
                sal_parts.append(f"до {sal_obj['to']:,}".replace(",", " ") + " ₽")
            sal = " — ".join(sal_parts) if sal_parts else None

            pub = v.get("published_at", "")
            pub_date = pub[:10] if len(pub) >= 10 else None
            time_str = pub[11:16] if len(pub) >= 16 else None

            jobs.append({
                "s": "hh.ru",
                "pid": vid,
                "date": pub_date,
                "time": time_str,
                "t": title[:140],
                "c": v.get("employer", {}).get("name", "—"),
                "lvl": lvl,
                "fmt": fmt,
                "role": detect_role(title, role_keywords),
                "sal": sal,
                "url": v.get("alternate_url", ""),
            })

        time.sleep(0.5)

    return jobs


def parse_hh_rss(src, exclude_keywords, role_keywords):
    """Парсит HH.ru через публичные RSS-фиды — без OAuth, без токенов."""
    seen_ids = set()
    seen_titles = set()   # дедупликация по company+title (защита от спама)
    jobs = []

    for feed_url in src.get("feeds", []):
        xml = fetch_html(feed_url)
        if not xml:
            continue

        soup = BeautifulSoup(xml, "xml")
        for item in soup.find_all("item"):
            title = (item.find("title") or {}).get_text(strip=True)
            link  = (item.find("link")  or {}).get_text(strip=True)
            desc_raw = (item.find("description") or {}).get_text(strip=True)
            pub   = (item.find("pubDate") or {}).get_text(strip=True)

            if not title or not link:
                continue

            # ID из URL: /vacancy/12345678
            m = re.search(r'/vacancy/(\d+)', link)
            vid = int(m.group(1)) if m else 0
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            # Дедупликация по title+company (защита от массового постинга)
            title_key = re.sub(r'\s+', ' ', title.lower().strip())
            # company определим позже, пока используем только title для предварительной проверки

            # Парсим HTML внутри description
            body = BeautifulSoup(desc_raw, "html.parser").get_text(" ", strip=True)

            if is_excluded(title, body, exclude_keywords):
                continue
            if not is_vacancy(title, body):
                # HH-вакансии всегда настоящие — пропускаем is_vacancy только для TG
                pass

            # Компания: "Вакансия компании: Название Создана: ..."
            company = "—"
            m_co = re.search(r'Вакансия компании:\s*(.+?)(?:\s*Создана:|$)', body)
            if m_co:
                company = m_co.group(1).strip()

            # Зарплата: "Предполагаемый уровень месячного дохода: от 100 000 руб."
            sal = None
            m_sal = re.search(r'месячного дохода:\s*([^\n<]+)', body)
            if m_sal:
                raw = m_sal.group(1).strip()
                if raw.lower() not in ("не указан", "не указана", "—", ""):
                    sal = raw

            # Регион
            region = None
            m_reg = re.search(r'Регион:\s*([^\n<]+)', body)
            if m_reg:
                region = m_reg.group(1).strip()

            # Дата: ISO 8601 "2026-05-13T16:38:54+03:00"
            pub_date = None
            time_str = None
            m_iso = re.search(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', pub)
            if m_iso:
                pub_date = m_iso.group(1)
                time_str = m_iso.group(2)

            fmt = detect_format((region or "") + " " + body[:200])

            dedup_key = f"{company.lower().strip()}|{title_key}"
            if dedup_key in seen_titles:
                continue
            seen_titles.add(dedup_key)

            jobs.append({
                "s": "hh.ru",
                "pid": vid,
                "date": pub_date,
                "time": time_str,
                "t": title[:140],
                "c": company,
                "lvl": detect_level(title + " " + body[:300]),
                "fmt": fmt,
                "role": detect_role(title + " " + body[:200], role_keywords),
                "sal": sal,
                "url": link,
            })

        time.sleep(1)

    return jobs


def generate(all_jobs):
    snapshot_date = datetime.date.today().isoformat()
    all_jobs.sort(key=lambda j: (j["s"], -(j.get("pid") or 0)))
    jobs_json = json.dumps(all_jobs, ensure_ascii=False, separators=(",", ":"))

    # dashboard.html (старый шаблон, оставляем для совместимости)
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    output = (template
              .replace("__SNAPSHOT_DATE__", snapshot_date)
              .replace("__JOBS_JSON__", jobs_json))
    OUTPUT_FILE.write_text(output, encoding="utf-8")

    # index.html (объединённый дашборд + трекер — главная страница)
    if INDEX_TEMPLATE_FILE.exists():
        ix_tmpl = INDEX_TEMPLATE_FILE.read_text(encoding="utf-8")
        ix_out = (ix_tmpl
                  .replace("__SNAPSHOT_DATE__", snapshot_date)
                  .replace("__JOBS_JSON__", jobs_json))
        INDEX_OUTPUT_FILE.write_text(ix_out, encoding="utf-8")

    return snapshot_date, len(all_jobs)


def main():
    cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    role_keywords = cfg["role_keywords"]
    exclude_keywords = cfg["exclude_keywords"]

    existing = {}
    if JOBS_FILE.exists():
        for j in json.loads(JOBS_FILE.read_text(encoding="utf-8")).get("jobs", []):
            existing.setdefault(j["s"], []).append(j)

    new_by_source = {}
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        name = src["name"]
        print(f"Fetching {name}...")

        if src["kind"] == "hh":
            jobs = parse_hh(src, exclude_keywords, role_keywords)
        elif src["kind"] == "hh_rss":
            jobs = parse_hh_rss(src, exclude_keywords, role_keywords)
        else:
            html = fetch_html(src["url"])
            if not html:
                print(f"  → skip (fetch failed)")
                continue
            if src["kind"] == "tg":
                jobs = parse_telegram(html, name, exclude_keywords, role_keywords)
            elif src["kind"] == "hirehi":
                jobs = parse_hirehi(html, role_keywords)
            else:
                continue

        key = "HireHi" if src["kind"] == "hirehi" else ("hh.ru" if src["kind"] in ("hh", "hh_rss") else name)
        new_by_source[key] = jobs
        print(f"  → {len(jobs)} вакансий")
        time.sleep(1)

    merged = dict(existing)
    merged.update(new_by_source)
    all_jobs = [j for arr in merged.values() for j in arr]

    JOBS_FILE.write_text(
        json.dumps({"snapshot_date": datetime.date.today().isoformat(), "jobs": all_jobs},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    date, count = generate(all_jobs)
    print(f"\nОК: {count} вакансий → dashboard.html (снимок: {date})")


if __name__ == "__main__":
    main()
