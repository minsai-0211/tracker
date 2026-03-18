"""
다나와 가격비교 자동 수집기 v2
- 가격비교.txt 의 URL을 읽어 각 제품 최저가를 수집
- GPU는 모델군별 평균가로 묶어서 별도 CSV에도 저장
- 결과를 CSV에 날짜별로 누적 저장
"""

import re
import csv
import time
import random
import logging
import requests
from pathlib import Path
from datetime import date
from collections import defaultdict
from bs4 import BeautifulSoup

# ── 설정 ────────────────────────────────────────────
URL_FILE        = Path("가격비교.txt")        # URL 목록 파일
OUTPUT_CSV      = Path("price_history.csv")   # 개별 제품 누적 저장
GPU_SUMMARY_CSV = Path("gpu_group_summary.csv")  # GPU 모델군 평균가 누적 저장
DELAY_MIN = 1.5
DELAY_MAX = 3.5
# ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── GPU 모델군 키워드 매핑 ────────────────────────────
# 제품명에서 아래 키워드를 순서대로 검색해 그룹을 결정
GPU_GROUPS = [
    ("RTX 5090",  ["5090"]),
    ("RTX 5080",  ["5080"]),
    ("RTX 5070 Ti", ["5070 Ti", "5070Ti"]),
    ("RTX 5070",  ["5070"]),          # 5070 Ti 이후에 검사해야 중복 방지
    ("RTX 5060 Ti", ["5060 Ti", "5060Ti"]),
    ("RTX 5060",  ["5060"]),
    ("RX 9070 XT", ["9070 XT", "9070XT"]),
    ("RX 9060 XT", ["9060 XT", "9060XT"]),
]

def get_gpu_group(name: str) -> str | None:
    """제품명에서 GPU 모델군을 반환. 해당 없으면 None."""
    for group_name, keywords in GPU_GROUPS:
        for kw in keywords:
            if kw.lower() in name.lower():
                return group_name
    return None


# ── URL 파일 파싱 ─────────────────────────────────────
# 지원 형식:
#   제품명 - https://...          (원본 가격비교.txt 형식)
#   https://...                   (URL만 있는 형식)
def parse_url_file(path: Path) -> dict[str, list[dict]]:
    """
    가격비교.txt 를 읽어
    { 카테고리: [{"name": 제품명, "url": url}, ...] } 딕셔너리로 반환.
    """
    categories: dict[str, list[dict]] = {}
    current = None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue

        # 카테고리 헤더: "1. CPU" 형태
        m = re.match(r"^\d+\.\s*(.+)", line)
        if m:
            current = m.group(1).strip()
            categories[current] = []
            continue

        if current is None:
            continue

        # URL 위치를 직접 찾아서 제품명과 분리
        # (제품명에 하이픈이 포함된 경우에도 안전하게 처리)
        url_match = re.search(r'(https?://\S+)', line)
        if url_match:
            url = url_match.group(1)
            name_part = line[:url_match.start()].strip().rstrip('-').strip()
            prod_name = name_part if name_part else None
            categories[current].append({"name": prod_name, "url": url})
            continue

    return categories


# ── 다나와 페이지에서 제품명 + 최저가 파싱 ──────────────
def fetch_product(item: dict) -> dict:
    """단일 URL에서 제품명과 최저가를 파싱해 반환."""
    url = item["url"]
    preset_name = item.get("name")  # 파일에 적힌 제품명 (있을 경우 우선 사용)

    pcode_m = re.search(r"pcode=(\d+)", url)
    pcode = pcode_m.group(1) if pcode_m else "unknown"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"  요청 실패 [{pcode}]: {e}")
        return {"pcode": pcode, "name": preset_name or "요청실패", "price": None, "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── 제품명 ───────────────────────────────────────
    # 파일에 적힌 이름이 있으면 그것을 우선 사용, 없으면 페이지에서 파싱
    name = preset_name
    if not name:
        for sel in ["h3.prod_name", "h1.tit_view", "title"]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get_text(" ", strip=True)
                raw = re.sub(r"\s*:\s*다나와\s*가격비교.*", "", raw)
                name = raw.strip()
                break

    # ── 최저가 ───────────────────────────────────────
    price = None

    # 방법 1: 메타 태그
    for meta_prop in ["product:price:amount", "og:price:amount"]:
        meta = soup.find("meta", property=meta_prop)
        if meta and meta.get("content"):
            price = _clean_price(meta["content"])
            if price:
                break

    # 방법 2: "최저가 N원" 텍스트 패턴
    if not price:
        text = soup.get_text(" ")
        m = re.search(r"최저가\s*([\d,]+)\s*원", text)
        if m:
            price = _clean_price(m.group(1))

    # 방법 3: price 관련 클래스 span
    if not price:
        for span in soup.find_all("span", class_=re.compile(r"price|lowest|miPrice", re.I)):
            candidate = _clean_price(span.get_text())
            if candidate and 10_000 <= candidate <= 15_000_000:
                price = candidate
                break

    log.info(
        f"  [{pcode}] {name or '이름불명'} → {price:,}원"
        if price else
        f"  [{pcode}] {name or '이름불명'} → 가격불명"
    )
    return {"pcode": pcode, "name": name or "이름불명", "price": price, "url": url}


def _clean_price(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


# ── CSV 누적 저장 ──────────────────────────────────
FIELDNAMES = ["date", "category", "pcode", "name", "price", "url"]
GPU_FIELDNAMES = ["date", "gpu_group", "count", "avg_price", "min_price", "max_price"]

def save_to_csv(rows: list[dict], path: Path):
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    log.info(f"CSV 저장 완료: {path} ({len(rows)}행 추가)")


def save_gpu_summary(gpu_rows: list[dict], path: Path):
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=GPU_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(gpu_rows)
    log.info(f"GPU 그룹 요약 저장 완료: {path} ({len(gpu_rows)}행 추가)")


# ── GPU 그룹 평균가 계산 ───────────────────────────
def calc_gpu_summary(today: str, gpu_products: list[dict]) -> list[dict]:
    """
    GPU 제품 목록에서 모델군별 평균/최저/최고가를 계산해 반환.
    GPU_GROUPS 순서대로 출력됨.
    """
    group_prices: dict[str, list[int]] = defaultdict(list)

    for p in gpu_products:
        if not p["price"] or not p["name"]:
            continue
        group = get_gpu_group(p["name"])
        if group:
            group_prices[group].append(p["price"])
        else:
            log.warning(f"  GPU 그룹 미분류: {p['name']}")

    summary = []
    for group_name, _ in GPU_GROUPS:
        prices = group_prices.get(group_name, [])
        if not prices:
            log.warning(f"  [{group_name}] 수집된 가격 없음")
            continue
        summary.append({
            "date":      today,
            "gpu_group": group_name,
            "count":     len(prices),
            "avg_price": round(sum(prices) / len(prices)),
            "min_price": min(prices),
            "max_price": max(prices),
        })
        log.info(
            f"  [{group_name}] {len(prices)}개 → "
            f"평균 {summary[-1]['avg_price']:,}원 "
            f"(최저 {summary[-1]['min_price']:,} / 최고 {summary[-1]['max_price']:,})"
        )
    return summary


# ── 메인 ──────────────────────────────────────────
def main():
    today = date.today().isoformat()
    log.info(f"=== 다나와 가격 수집 시작: {today} ===")

    if not URL_FILE.exists():
        raise FileNotFoundError(f"{URL_FILE} 파일을 찾을 수 없습니다.")

    categories = parse_url_file(URL_FILE)
    total_urls = sum(len(v) for v in categories.values())
    log.info(f"카테고리 {len(categories)}개, 총 URL {total_urls}개 수집 예정")

    all_rows = []
    gpu_products = []

    for cat, items in categories.items():
        log.info(f"\n[{cat}] {len(items)}개 처리 중...")
        for item in items:
            product = fetch_product(item)
            row = {
                "date":     today,
                "category": cat,
                "pcode":    product["pcode"],
                "name":     product["name"],
                "price":    product["price"],
                "url":      product["url"],
            }
            all_rows.append(row)

            if cat == "GPU":
                gpu_products.append(product)

            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # 개별 제품 CSV 저장
    save_to_csv(all_rows, OUTPUT_CSV)

    # GPU 모델군 평균가 계산 & 저장
    if gpu_products:
        log.info("\n=== GPU 모델군 평균가 계산 ===")
        gpu_summary = calc_gpu_summary(today, gpu_products)
        save_gpu_summary(gpu_summary, GPU_SUMMARY_CSV)

    # 결과 요약
    success = sum(1 for r in all_rows if r["price"])
    fail    = len(all_rows) - success
    log.info(f"\n=== 완료: 성공 {success}개 / 실패 {fail}개 ===")


if __name__ == "__main__":
    main()
