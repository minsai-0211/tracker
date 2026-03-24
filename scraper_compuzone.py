"""
컴퓨존 가격 수집기
- 컴퓨존_가격비교.txt 의 URL을 읽어 판매가 수집 (Playwright — JS 렌더링 대응)
- 판매가 텍스트 패턴을 1순위로 → 이벤트 배너 오파싱 방지
- 결과: compuzone_YYYY-MM-DD.csv
"""

import re
import csv
import time
import random
import logging
from pathlib import Path
from datetime import date
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from common import HEADERS, parse_url_file, _clean_price

# ── 설정 ─────────────────────────────────────────────
COMPUZONE_FILE = Path("컴퓨존_가격비교.txt")
DELAY_MIN      = 1.0
DELAY_MAX      = 2.0
OUTPUT_DIR     = Path("data/compuzone")
# ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

FIELDNAMES = ["date", "category", "subcategory", "name", "price", "url"]


def _parse_cz_price(html: str, preset_name: str | None) -> tuple[str | None, int | None]:
    """
    Playwright로 JS 렌더링된 HTML에서 제품명·가격 추출.

    우선순위:
      1. 판매가/즉시할인가 텍스트 패턴 (라벨 바로 뒤 숫자만) ← 이벤트 배너 오파싱 방지
      2. 컴퓨존 특화 CSS 셀렉터
      3. 메타태그
      4. class 스캔 (fallback)
    """
    soup = BeautifulSoup(html, "html.parser")

    # 제품명
    name = preset_name
    if not name:
        for sel in ["h1.prod_name", "h2.prod_name", ".product_name", "title"]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get_text(" ", strip=True)
                raw = re.sub(r"\s*:\s*컴퓨존.*", "", raw)
                name = raw.strip()
                break

    price = None
    text  = soup.get_text(" ")

    # ── 1순위: 판매가 라벨 바로 뒤 숫자 ────────────────
    # "판매가" 또는 "즉시할인가" 뒤에 비숫자 0~10자 이내로 나오는 첫 숫자만 추출
    for pattern in [
        r"즉시할인가[^\d]{0,10}([\d,]+)\s*원",
        r"판매가[^\d]{0,10}([\d,]+)\s*원",
    ]:
        m = re.search(pattern, text)
        if m:
            price = _clean_price(m.group(1))
            if price:
                break

    # ── 2순위: 컴퓨존 특화 CSS 셀렉터 ──────────────────
    if not price:
        for sel in [
            "span.sell_price",
            "span.instant_price",
            "em.price_num",
            "strong.price",
            ".price_area span",
            ".buy_area .price",
            "#sellPrice",
            "#instantPrice",
        ]:
            tag = soup.select_one(sel)
            if tag:
                # get_text(separator=" ")로 자식 태그 텍스트 분리 후 첫 토큰만
                candidate = _clean_price(tag.get_text(separator=" ").split()[0] if tag.get_text(separator=" ").split() else "")
                if candidate:
                    price = candidate
                    break

    # ── 3순위: 메타태그 ─────────────────────────────────
    if not price:
        for meta_prop in ["product:price:amount", "og:price:amount"]:
            meta = soup.find("meta", property=meta_prop)
            if meta and meta.get("content"):
                price = _clean_price(meta["content"])
                if price:
                    break

    # ── 4순위: class 스캔 (fallback) ────────────────────
    if not price:
        for tag in soup.find_all(
            ["span", "strong", "em", "p"],
            class_=re.compile(r"price|sell|instant|cost", re.I)
        ):
            # 자식 태그 포함 텍스트에서 첫 숫자 덩어리만
            first_token = (tag.get_text(separator=" ").split() or [""])[0]
            candidate = _clean_price(first_token)
            if candidate:
                price = candidate
                break

    return name, price


def fetch_compuzone_batch(items: list[dict]) -> list[dict]:
    """
    Playwright 브라우저 1개로 전체 컴퓨존 제품 순회 수집.
    반환: [{"category", "subcategory", "name", "price", "url"}, ...]
    """
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="ko-KR",
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        page = context.new_page()
        # 이미지·폰트·미디어 차단 → 속도 향상
        page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,mp4,webp}",
            lambda r: r.abort()
        )

        for item in items:
            url         = item.get("url")
            preset_name = item.get("name")
            cat         = item.get("_cat", "")
            subcat      = item.get("subcategory", "")

            # url=None 미확인 항목 → 빈 결과
            if not url:
                results.append({
                    "category":    cat,
                    "subcategory": subcat,
                    "name":        preset_name or "미확인",
                    "price":       None,
                    "url":         "",
                })
                continue

            pno_m      = re.search(r"ProductNo=(\d+)", url)
            product_no = pno_m.group(1) if pno_m else "unknown"

            try:
                # networkidle → domcontentloaded 로 변경해 대기시간 단축
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                # 판매가 태그 렌더링 대기 (최대 4초)
                try:
                    page.wait_for_selector(
                        "span.sell_price, span.instant_price, em.price_num, #sellPrice",
                        timeout=4_000,
                    )
                except PWTimeout:
                    pass

                html = page.content()
                name, price = _parse_cz_price(html, preset_name)

            except PWTimeout:
                log.warning(f"  [컴퓨존] 타임아웃 [ProductNo={product_no}]")
                name, price = preset_name, None
            except Exception as e:
                log.warning(f"  [컴퓨존] 오류 [ProductNo={product_no}]: {e}")
                name, price = preset_name, None

            log.info(
                f"  [ProductNo={product_no}] {name or '이름불명'} → {price:,}원"
                if price else
                f"  [ProductNo={product_no}] {name or '이름불명'} → 가격불명"
            )
            results.append({
                "category":    cat,
                "subcategory": subcat,
                "name":        name or "이름불명",
                "price":       price,
                "url":         url,
            })
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        browser.close()

    return results


def save_csv(rows: list[dict], path: Path, today: str):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "date":        today,
                "category":    r["category"],
                "subcategory": r["subcategory"],
                "name":        r["name"],
                "price":       r["price"],
                "url":         r["url"],
            })
    log.info(f"저장 완료: {path} ({len(rows)}행)")


def main():
    today = date.today().isoformat()
    log.info(f"=== 컴퓨존 수집 시작 (Playwright): {today} ===")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_CSV = OUTPUT_DIR / f"compuzone_{today}.csv"

    if not COMPUZONE_FILE.exists():
        raise FileNotFoundError(f"{COMPUZONE_FILE} 파일을 찾을 수 없습니다.")

    categories = parse_url_file(COMPUZONE_FILE)

    all_items = []
    for cat, items in categories.items():
        for item in items:
            all_items.append({**item, "_cat": cat})

    log.info(f"총 {len(all_items)}개 URL 처리 예정")
    results = fetch_compuzone_batch(all_items)
    save_csv(results, OUTPUT_CSV, today)

    success = sum(1 for r in results if r["price"])
    fail    = len(results) - success
    log.info(f"\n=== 완료: 총 {len(results)}개 │ 성공 {success}개 │ 실패 {fail}개 ===")


if __name__ == "__main__":
    main()
