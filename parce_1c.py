import asyncio
import csv
import os
import re
import json
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

BASE_URL    = "https://consulting.1c.ru/partners/"
OUTPUT_FILE = "ver_1/consulting_1c_partners.csv"
MAX_PAGES   = 30
PAGE_DELAY  = 1500
HEADLESS    = True
SAVE_EVERY  = 3   # сохранять каждые N страниц


async def goto_with_retry(page, url, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return True
        except Exception as e:
            print(f"  [!] попытка {attempt}/{attempts}: {e}")
            await page.wait_for_timeout(3000)
    return False


def extract_maps_data(html):
    """Достаёт массив mapsData из HTML с балансировкой скобок."""
    contacts_by_id = {}
    start = html.find("var mapsData")
    if start == -1:
        return contacts_by_id

    start = html.find("[", start)
    if start == -1:
        return contacts_by_id

    depth = 0
    end = -1
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        return contacts_by_id

    raw = html[start:end]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [!] mapsData не распарсился: {e}")
        return contacts_by_id

    for item in data:
        name_html = item.get("name", "")
        id_match = re.search(r"/partners/(\d+)\.html", name_html)
        pid = id_match.group(1) if id_match else None
        if not pid:
            continue
        text = item.get("text", "")
        email_m = re.search(r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}", text)
        phone_m = re.search(r"\+?[\d\s\(\)\-]{9,}", text.replace("Телефон:", ""))
        contacts_by_id[pid] = {
            "email": email_m.group(0).strip() if email_m else "",
            "phone": phone_m.group(0).strip() if phone_m else "",
        }
    return contacts_by_id


async def collect_page(page, page_num):
    url = f"{BASE_URL}?PAGEN_2={page_num}"
    print(f"[стр. {page_num}] {url}")

    ok = await goto_with_retry(page, url)
    if not ok:
        print("  страница не загрузилась — пропускаем")
        return []

    try:
        await page.wait_for_selector("table.parth_table", timeout=30000)
    except Exception:
        print("  [!] таблица parth_table не появилась")
    await page.wait_for_timeout(1000)

    html = await page.content()

    # --- контакты из mapsData ---
    contacts_by_id = extract_maps_data(html)

    # --- карточки из таблицы ---
    soup = BeautifulSoup(html, "html.parser")
    partners = []
    table = soup.select_one("table.parth_table")
    if not table:
        print("  таблица не найдена — стоп")
        return []

    for tr in table.select("tbody > tr"):
        name_a = tr.select_one("a.link_to_parth")
        if not name_a:
            continue
        name = name_a.get_text(strip=True)
        href = name_a.get("href", "")
        id_match = re.search(r"/partners/(\d+)\.html", href)
        pid = id_match.group(1) if id_match else ""

        city_el = tr.select_one("span.city_parth")
        city = city_el.get_text(strip=True) if city_el else ""

        industries = [a.get_text(strip=True) for a in tr.select("td:nth-child(2) a")]
        statuses = [
            p.get_text(strip=True)
            for p in tr.select("td:nth-child(3) p.status")
            if p.get_text(strip=True) != "Партнер"
        ]

        contacts = contacts_by_id.get(pid, {"email": "", "phone": ""})

        partners.append({
            "id": pid,
            "name": name,
            "city": city,
            "url": "https://consulting.1c.ru" + href if href.startswith("/") else href,
            "email": contacts["email"],
            "phone": contacts["phone"],
            "industries": "; ".join(industries),
            "statuses": "; ".join(statuses),
        })

    return partners


def save_csv(data, filename):
    if not data:
        return
    filename = filename.strip()
    directory = os.path.dirname(filename)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fieldnames = ["id", "name", "city", "url", "email", "phone", "industries", "statuses"]
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        results = []
        seen = set()

        try:
            for page_num in range(1, MAX_PAGES + 1):
                partners = await collect_page(page, page_num)
                if not partners:
                    print("  пусто — останавливаемся")
                    break

                new_count = 0
                for p in partners:
                    if p["id"] in seen:
                        continue
                    seen.add(p["id"])
                    results.append(p)
                    new_count += 1

                print(f"  новых: {new_count} | всего: {len(results)}")

                if new_count == 0:
                    print("  новых нет — останавливаемся")
                    break

                if page_num % SAVE_EVERY == 0:
                    save_csv(results, OUTPUT_FILE)
                    print(f"  ▸ промежуточное сохранение ({len(results)})")

                await page.wait_for_timeout(PAGE_DELAY)
        except KeyboardInterrupt:
            print("\n⚠ Прервано. Сохраняем...")

        await browser.close()

    save_csv(results, OUTPUT_FILE)

    total = len(results)
    with_email = sum(1 for r in results if r["email"])
    print("\n" + "=" * 60)
    print(f"ГОТОВО. Партнёров: {total}, с email: {with_email}")
    print(f"Файл: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())