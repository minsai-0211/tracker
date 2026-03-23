"""
다나와 가격 수집기
- 가격비교.txt 의 URL을 읽어 최저가 수집 (requests + BeautifulSoup)
- 결과: danawa_YYYY-MM-DD.csv
"""

import os
import re
import csv
import time
import random
import logging
import requests
from pathlib import Path
from datetime import date
from bs4 import BeautifulSoup
from common import HEADERS, parse_url_file, _clean_price

# ── 설정 ─────────────────────────────────────────────
DANAWA_FILE = Path("가격비교.txt")
DELAY_MIN   = 1.5
DELAY_MAX   = 3.5
OUTPUT_DIR  = Path("data/danawa")
# ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

FIELDNAMES = ["date", "category", "subcategory", "name", "price", "url"]


def fetch_danawa(item: dict) -> dict:
    url         = item.get("url")
    preset_name = item.get("name")

    if not url:
        return {"pcode": "none", "name": preset_name or "미확인", "price": None, "url": ""}

    pcode_m = re.search(r"pcode=(\d+)", url)
    pcode   = pcode_m.group(1) if pcode_m else "unknown"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"  [다나와] 요청 실패 [{pcode}]: {e}")
        return {"pcode": pcode, "name": preset_name or "요청실패", "price": None, "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    name = preset_name
    if not name:
        for sel in ["h3.prod_name", "h1.tit_view", "title"]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get_text(" ", strip=True)
                raw = re.sub(r"\s*:\s*다나와\s*가격비교.*", "", raw)
                name = raw.strip()
                break

    price = None

    # 1순위: 메타태그
    for meta_prop in ["product:price:amount", "og:price:amount"]:
        meta = soup.find("meta", property=meta_prop)
        if meta and meta.get("content"):
            price = _clean_price(meta["content"])
            if price:
                break

    # 2순위: 최저가 텍스트 패턴
    if not price:
        text = soup.get_text(" ")
        m = re.search(r"최저가\s*([\d,]+)\s*원", text)
        if m:
            price = _clean_price(m.group(1))

    # 3순위: class 스캔
    if not price:
        for span in soup.find_all("span", class_=re.compile(r"price|lowest|miPrice", re.I)):
            candidate = _clean_price(span.get_text())
            if candidate:
                price = candidate
                break

    log.info(
        f"  [{pcode}] {name or '이름불명'} → {price:,}원"
        if price else
        f"  [{pcode}] {name or '이름불명'} → 가격불명"
    )
    return {"pcode": pcode, "name": name or "이름불명", "price": price, "url": url}


def save_csv(rows: list[dict], path: Path):
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    log.info(f"저장 완료: {path} ({len(rows)}행)")


def main():
    today = date.today().isoformat()
    log.info(f"=== 다나와 수집 시작: {today} ===")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_CSV = OUTPUT_DIR / f"danawa_{today}.csv"

    if not DANAWA_FILE.exists():
        raise FileNotFoundError(f"{DANAWA_FILE} 파일을 찾을 수 없습니다.")

    categories = parse_url_file(DANAWA_FILE)
    rows: list[dict] = []

    for cat, items in categories.items():
        log.info(f"\n[{cat}] {len(items)}개 처리 중...")
        for item in items:
            res = fetch_danawa(item)
            rows.append({
                "date":        today,
                "category":    cat,
                "subcategory": item.get("subcategory") or "",
                "name":        res["name"],
                "price":       res["price"],
                "url":         res["url"],
            })
            if item.get("url"):
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    save_csv(rows, OUTPUT_CSV)

    success = sum(1 for r in rows if r["price"])
    fail    = len(rows) - success
    log.info(f"\n=== 완료: 총 {len(rows)}개 │ 성공 {success}개 │ 실패 {fail}개 ===")


if __name__ == "__main__":
    main()
