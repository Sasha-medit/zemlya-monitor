"""
Робот-сборщик лотов по земле (ЗК РФ) с torgi.gov.ru.
Предназначен для запуска через GitHub Actions по расписанию.

При первом запуске (файла data/lots.json ещё нет) — забирает историю
за последние SEED_DAYS дней. При последующих — только за последние
2 дня (с запасом на задержку публикации), и объединяет с уже накопленными
данными. Лоты с истёкшим сроком подачи заявок автоматически убираются.
"""

import requests
import json
import os
import time
from datetime import datetime, timedelta, timezone

# ============ НАСТРОЙКИ ============
# Коды регионов (как на автономерах).
# 23 - Краснодарский край, 64 - Саратовская область
# Чтобы собирать по всей России - оставьте REGION_CODES = None
REGION_CODES = {"23", "64"}

SEED_DAYS = 30       # сколько дней истории забрать при самом первом запуске
DATA_FILE = "data/lots.json"
# =====================================

BASE = "https://torgi.gov.ru/new/opendata/7710568760-notice"
HEADERS = {"User-Agent": "Mozilla/5.0 (land-monitor-bot; +for personal use)"}


def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None  # файла за эту дату ещё/уже нет - это нормально
            print(f"  [{r.status_code}] {url}")
        except Exception as e:
            print(f"  ошибка ({attempt+1}/{retries}): {e}")
            time.sleep(2)
    return None


def extract_lot_rows(notice_json):
    rows = []
    try:
        notice = notice_json["exportObject"]["structuredObject"]["notice"]
    except (KeyError, TypeError):
        return rows

    common = notice.get("commonInfo", {})
    bidder = notice.get("bidderOrg", {}).get("orgInfo", {})
    conditions = notice.get("biddConditions", {})

    for lot in notice.get("lots", []):
        info = lot.get("biddingObjectInfo", {})
        chars = {c.get("code"): c.get("characteristicValue") for c in info.get("characteristics", [])}

        permitted_use = chars.get("PermittedUse")
        if isinstance(permitted_use, list):
            permitted_use = "; ".join(v.get("name", "") for v in permitted_use if isinstance(v, dict))

        reg_num = common.get("noticeNumber")
        lot_num = lot.get("lotNumber")

        rows.append({
            "lotId": f"{reg_num}_{lot_num}",
            "regNum": reg_num,
            "biddType": common.get("biddType", {}).get("name"),
            "publishDate": common.get("publishDate"),
            "region": info.get("subjectRF", {}).get("name"),
            "address": info.get("estateAddress"),
            "category": info.get("category", {}).get("name"),
            "permittedUse": permitted_use,
            "cadastralNumber": chars.get("CadastralNumber"),
            "areaM2": _to_number(chars.get("SquareZU")),
            "priceMin": _to_number(lot.get("priceMin")),
            "contractType": next(
                (d.get("value", {}).get("name") for d in lot.get("additionalDetails", [])
                 if d.get("code") == "DA_contractType_IPS(ZK)" and isinstance(d.get("value"), dict)),
                None
            ),
            "biddStart": conditions.get("biddStartTime"),
            "biddEnd": conditions.get("biddEndTime"),
            "organizer": bidder.get("name"),
            "link": common.get("href"),
        })
    return rows


def _to_number(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def daterange(d1, d2):
    for i in range((d2 - d1).days + 1):
        yield d1 + timedelta(days=i)


def main():
    os.makedirs("data", exist_ok=True)

    existing = []
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            existing = json.load(f)

    existing_by_id = {row["lotId"]: row for row in existing if row.get("lotId")}
    is_first_run = len(existing) == 0

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=SEED_DAYS if is_first_run else 2)

    print(f"Первый запуск: {is_first_run}. Сканирую с {start} по {today}.")

    new_count = 0
    for day in daterange(start, today):
        next_day = day + timedelta(days=1)
        index_url = (
            f'{BASE}/data-{day.strftime("%Y%m%dT0000")}-'
            f'{next_day.strftime("%Y%m%dT0000")}-structure-20240401.json'
        )
        index_data = fetch_json(index_url)
        if not index_data:
            continue

        matches = [
            item for item in index_data.get("listObjects", [])
            if item.get("documentType") == "notice"
            and item.get("biddTypeCode") == "ZK"
            and (REGION_CODES is None or item.get("subjectEstateCode") in REGION_CODES)
        ]
        print(f"  {day}: подходящих извещений — {len(matches)}")

        for item in matches:
            notice_json = fetch_json(item["href"])
            if not notice_json:
                continue
            for row in extract_lot_rows(notice_json):
                if row["lotId"] not in existing_by_id:
                    existing_by_id[row["lotId"]] = row
                    new_count += 1
            time.sleep(0.2)

    # убираем лоты с истёкшим сроком подачи заявок
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fresh = [
        row for row in existing_by_id.values()
        if not row.get("biddEnd") or row["biddEnd"] >= now_iso
    ]
    fresh.sort(key=lambda r: r.get("publishDate") or "", reverse=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False, indent=1)

    print(f"Новых лотов добавлено: {new_count}. Всего активных в базе: {len(fresh)}.")


if __name__ == "__main__":
    main()
