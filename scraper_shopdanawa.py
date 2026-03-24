"""
샵다나와 가격 수집기
- 샵다나와_가격비교.txt 의 URL을 읽어 판매가 수집 (requests + BeautifulSoup)
- 결과: data/shopdanawa/shopdanawa_YYYY-MM-DD.csv
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
SHOPDANAWA_FILE = Path("샵다나와_가격비교.txt")
DELAY_MIN       = 1.5
DELAY_MAX       = 3.5
OUTPUT_DIR      = Path("data/shopdanawa")
# ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

FIELDNAMES = ["date", "category", "subcategory", "name", "price", "url"]


def fetch_shopdanawa(item: dict) -> dict:
    url         = item.get("url")
    preset_name = item.get("name")

    if not url:
        return {"name": preset_name or "미확인", "price": None, "url": ""}

    seq_m = re.search(r"billingInternalProductSeq=(\d+)", url)
    seq   = seq_m.group(1) if seq_m else "unknown"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"  [샵다나와] 요청 실패 [seq={seq}]: {e}")
        return {"name": preset_name or "요청실패", "price": None, "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    # 제품명
    name = preset_name
    if not name:
        for sel in ["h2.goods_name", "h1", ".goods_name", "title"]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get_text(" ", strip=True)
                raw = re.sub(r"\s*:\s*샵다나와.*", "", raw)
                name = raw.strip()
                break

    price = None
    text  = soup.get_text(" ")

    # 1순위: "총 상품금액" — 페이지 하단 고정 위치, 배너 영향 없음
    total_idx = text.find("총 상품금액")
    if total_idx != -1:
        total_text = text[total_idx:total_idx + 80]
        m = re.search(r"([\d,]{5,})\s*원", total_text)
        if m:
            price = _clean_price(m.group(1))

    # 2순위: "판매가" 라벨 이후 슬라이싱
    if not price:
        sale_idx = text.find("판매가")
        if sale_idx != -1:
            sale_text = text[sale_idx:sale_idx + 200]
            m = re.search(r"([\d,]{5,})\s*원", sale_text)
            if m:
                price = _clean_price(m.group(1))

    # 3순위: 메타태그
    if not price:
        for meta_prop in ["product:price:amount", "og:price:amount"]:
            meta = soup.find("meta", property=meta_prop)
            if meta and meta.get("content"):
                price = _clean_price(meta["content"])
                if price:
                    break

    # 4순위: strong/em 태그 중 숫자만 있는 것 (판매가 bold 텍스트)
    if not price:
        for tag in soup.find_all(["strong", "em", "b"]):
            txt = tag.get_text(strip=True).replace(",", "")
            if re.fullmatch(r"\d{5,8}", txt):  # 5~8자리 순수 숫자
                candidate = _clean_price(txt)
                if candidate:
                    price = candidate
                    break

    log.info(
        f"  [seq={seq}] {name or '이름불명'} → {price:,}원"
        if price else
        f"  [seq={seq}] {name or '이름불명'} → 가격불명"
    )
    return {"name": name or "이름불명", "price": price, "url": url}


def save_csv(rows: list[dict], path: Path):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    log.info(f"저장 완료: {path} ({len(rows)}행)")


def main():
    today = date.today().isoformat()
    log.info(f"=== 샵다나와 수집 시작: {today} ===")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_CSV = OUTPUT_DIR / f"shopdanawa_{today}.csv"

    if not SHOPDANAWA_FILE.exists():
        raise FileNotFoundError(f"{SHOPDANAWA_FILE} 파일을 찾을 수 없습니다.")

    categories = parse_url_file(SHOPDANAWA_FILE)
    rows: list[dict] = []

    for cat, items in categories.items():
        log.info(f"\n[{cat}] {len(items)}개 처리 중...")
        for item in items:
            res = fetch_shopdanawa(item)
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
