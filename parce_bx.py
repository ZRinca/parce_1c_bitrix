import asyncio
import csv
import os
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# ------------------- Настройки -------------------
PARTNERS_URL      = "https://www.bitrix24.ru/partners/"
OUTPUT_FILE       = "ver_1/bitrix24_partners.csv"   # папка создастся автоматически
SCROLL_PAUSE      = 2500
MAX_EMPTY_SCROLLS = 3
PAGE_DELAY        = 800
HEADLESS          = True
SAVE_EVERY        = 20
# --------------------------------------------------


async def collect_partner_links(page):
    """Прокручивает каталог и собирает ссылки на страницы всех партнёров."""
    print("=" * 60)
    print("ШАГ 1: Сбор ссылок на партнёров")
    print("=" * 60)
    print(f"Открываем каталог: {PARTNERS_URL}")

    await page.goto(PARTNERS_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    seen_links = set()
    last_count = 0
    no_progress = 0

    while True:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE)

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/partners/partner/" in href:
                full = href.split("?")[0]
                if full.startswith("/"):
                    full = "https://www.bitrix24.ru" + full
                seen_links.add(full)

        current_count = len(seen_links)
        print(f"  Собрано уникальных ссылок: {current_count}")

        if current_count == last_count:
            no_progress += 1
            if no_progress >= MAX_EMPTY_SCROLLS:
                print("  Новые карточки больше не появляются. Завершаем сбор.")
                break
        else:
            no_progress = 0
            last_count = current_count

    return sorted(seen_links)


async def extract_contacts(page, url):
    """Переходит на страницу партнёра и извлекает контакты."""
    result = {
        "name": "",
        "email": "",
        "phone": "",       # приоритет — номер "По Битрикс24 звоните"
        "phone_alt": "",   # второй номер (общий)
        "address": "",
        "url": url,
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        # Пытаемся раскрыть блок контактов
        try:
            btn = page.locator('text="Показать контакты"')
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # --- Название ---
        h1 = soup.find("h1")
        if h1:
            result["name"] = h1.get_text(strip=True)

        # --- Блок контактов ---
        block = soup.find(
            "div", class_="bx-partner-detail-description-contacts-content"
        )
        if not block:
            print("    (блок контактов не найден)")
            return result

        # --- Email ---
        mail = block.find("a", href=lambda h: h and h.startswith("mailto:"))
        if mail:
            result["email"] = mail.get_text(strip=True)

        # --- Адрес и телефоны ---
        phones = []        # все номера, кроме "По Битрикс24 звоните"
        b24_phone = ""     # номер из строки "По Битрикс24 звоните"

        for p in block.find_all("p"):
            text = p.get_text(" ", strip=True)

            if text.startswith("Адрес") and not result["address"]:
                result["address"] = text[len("Адрес"):].strip(" :")

            m = re.search(r"[\+8][\d\s\(\)\-]{9,}", text)
            if not m:
                continue
            phone = m.group(0).strip()

            low = text.lower()
            if "звоните" in low or "битрикс24" in low or "битрикс 24" in low:
                if not b24_phone:
                    b24_phone = phone
            else:
                phones.append(phone)

        # Приоритет: сначала "По Битрикс24 звоните", потом обычный.
        # Если один из них отсутствует — дублируем оставшийся в обе колонки.
        if b24_phone and phones:
            result["phone"] = b24_phone
            result["phone_alt"] = phones[0]
        elif b24_phone:
            result["phone"] = b24_phone
            result["phone_alt"] = b24_phone
        elif phones:
            result["phone"] = phones[0]
            result["phone_alt"] = phones[0]

    except Exception as e:
        print(f"    ОШИБКА: {e}")

    return result


def save_csv(data, filename):
    """Сохраняет данные в CSV, создавая папку при необходимости."""
    if not data:
        return

    # Убираем пробелы по краям и создаём папку, если её нет
    filename = filename.strip()
    directory = os.path.dirname(filename)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "email", "phone", "phone_alt", "address", "url"],
        )
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

        # Шаг 1 — ссылки
        links = await collect_partner_links(page)
        print(f"\nВсего собрано ссылок: {len(links)}\n")

        # Шаг 2 — контакты
        print("=" * 60)
        print("ШАГ 2: Извлечение контактов")
        print("=" * 60)

        results = []
        try:
            for i, url in enumerate(links, 1):
                print(f"[{i}/{len(links)}] {url}")
                data = await extract_contacts(page, url)
                results.append(data)

                if data["email"]:
                    print(
                        f"    ✔ {data['name']} — {data['email']} — "
                        f"{data['phone']} / {data['phone_alt']}"
                    )
                else:
                    print(f"    ✖ {data['name']} — email не найден")

                if i % SAVE_EVERY == 0:
                    save_csv(results, OUTPUT_FILE)
                    print(f"    ▸ промежуточное сохранение ({i} записей)")

                await page.wait_for_timeout(PAGE_DELAY)
        except KeyboardInterrupt:
            print("\n⚠ Прервано пользователем. Сохраняем накопленные данные...")

        await browser.close()

    save_csv(results, OUTPUT_FILE)

    total = len(results)
    with_email = sum(1 for r in results if r["email"])
    print("\n" + "=" * 60)
    print(f"ГОТОВО. Всего партнёров: {total}")
    print(f"С email:   {with_email}")
    print(f"Без email: {total - with_email}")
    print(f"Файл: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())