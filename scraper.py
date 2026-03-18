"""
다나와 가격비교 자동 수집기 v3
- 가격비교.txt 의 URL을 읽어 각 제품 최저가를 수집
- GPU는 모델군별 평균가로 묶어서 별도 CSV에도 저장
- 결과를 CSV에 날짜별로 누적 저장
- 수집 완료 후 Slack DM으로 리포트 전송
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
from collections import defaultdict
from bs4 import BeautifulSoup

# ── 설정 ────────────────────────────────────────────
URL_FILE  = Path("가격비교.txt")   # URL 목록 파일
DELAY_MIN = 1.5
DELAY_MAX = 3.5
# CSV 파일명은 실행 시점 날짜로 동적 생성
# 예: price_history_2026-03-18.csv / gpu_group_summary_2026-03-18.csv

# Slack 설정 — GitHub Actions Secret 에서 주입
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")   # xoxb-...
SLACK_USER_ID   = os.environ.get("SLACK_USER_ID", "")     # U012AB3CD (본인 Slack User ID)
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
GPU_GROUPS = [
    ("RTX 5090",    ["5090"]),
    ("RTX 5080",    ["5080"]),
    ("RTX 5070 Ti", ["5070 Ti", "5070Ti"]),
    ("RTX 5070",    ["5070"]),
    ("RTX 5060 Ti", ["5060 Ti", "5060Ti"]),
    ("RTX 5060",    ["5060"]),
    ("RX 9070 XT",  ["9070 XT", "9070XT"]),
    ("RX 9060 XT",  ["9060 XT", "9060XT"]),
]

def get_gpu_group(name: str) -> str | None:
    for group_name, keywords in GPU_GROUPS:
        for kw in keywords:
            if kw.lower() in name.lower():
                return group_name
    return None


# ── URL 파일 파싱 ─────────────────────────────────────
def parse_url_file(path: Path) -> dict[str, list[dict]]:
    categories: dict[str, list[dict]] = {}
    current = None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue

        m = re.match(r"^\d+\.\s*(.+)", line)
        if m:
            current = m.group(1).strip()
            categories[current] = []
            continue

        if current is None:
            continue

        # URL 위치를 직접 찾아서 제품명과 분리
        url_match = re.search(r'(https?://\S+)', line)
        if url_match:
            url = url_match.group(1)
            name_part = line[:url_match.start()].strip().rstrip('-').strip()
            categories[current].append({"name": name_part if name_part else None, "url": url})

    return categories


# ── 다나와 페이지에서 제품명 + 최저가 파싱 ──────────────
def fetch_product(item: dict) -> dict:
    url = item["url"]
    preset_name = item.get("name")

    pcode_m = re.search(r"pcode=(\d+)", url)
    pcode = pcode_m.group(1) if pcode_m else "unknown"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"  요청 실패 [{pcode}]: {e}")
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
    for meta_prop in ["product:price:amount", "og:price:amount"]:
        meta = soup.find("meta", property=meta_prop)
        if meta and meta.get("content"):
            price = _clean_price(meta["content"])
            if price:
                break

    if not price:
        text = soup.get_text(" ")
        m = re.search(r"최저가\s*([\d,]+)\s*원", text)
        if m:
            price = _clean_price(m.group(1))

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


# ── CSV 저장 ───────────────────────────────────────
FIELDNAMES     = ["date", "category", "pcode", "name", "price", "url"]
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
            continue
        summary.append({
            "date":      today,
            "gpu_group": group_name,
            "count":     len(prices),
            "avg_price": round(sum(prices) / len(prices)),
            "min_price": min(prices),
            "max_price": max(prices),
        })
    return summary


# ── Slack 전송 ─────────────────────────────────────
def send_slack_dm(message: str):
    """SLACK_BOT_TOKEN + SLACK_USER_ID 로 DM 전송."""
    if not SLACK_BOT_TOKEN or not SLACK_USER_ID:
        log.warning("Slack 환경변수 미설정 — 전송 생략")
        return

    # 1) DM 채널 열기
    ch_resp = requests.post(
        "https://slack.com/api/conversations.open",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"users": SLACK_USER_ID},
        timeout=10,
    )
    ch_data = ch_resp.json()
    if not ch_data.get("ok"):
        log.error(f"Slack 채널 열기 실패: {ch_data.get('error')}")
        return

    channel_id = ch_data["channel"]["id"]

    # 2) 메시지 전송
    msg_resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": channel_id, "text": message},
        timeout=10,
    )
    msg_data = msg_resp.json()
    if msg_data.get("ok"):
        log.info("✅ Slack DM 전송 완료")
    else:
        log.error(f"Slack 메시지 전송 실패: {msg_data.get('error')}")


def build_slack_message(
    today: str,
    all_rows: list[dict],
    gpu_summary: list[dict],
    prev_rows: list[dict] | None = None,
) -> str:
    """
    Slack에 보낼 리포트 메시지 생성.
    - GPU 그룹 평균가 요약
    - 카테고리별 전체 제품 가격 리스트
    - 전날 대비 가격 변동 (prev_rows 있을 때)
    """
    lines = [f"📊 *다나와 가격 리포트 — {today}*", ""]

    # ── 1) GPU 그룹 평균가 요약 ─────────────────────
    lines.append("*🎮 GPU 모델군 평균가*")
    for g in gpu_summary:
        lines.append(
            f"  • {g['gpu_group']:12s} │ 평균 {g['avg_price']:>10,}원 "
            f"(최저 {g['min_price']:,} / 최고 {g['max_price']:,})"
        )
    lines.append("")

    # ── 2) 카테고리별 전체 제품 가격 리스트 ───────────
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in all_rows:
        by_cat[r["category"]].append(r)

    cat_emoji = {"CPU": "🖥️", "RAM": "🧠", "GPU": "🎮", "SSD": "💾"}
    for cat, items in by_cat.items():
        emoji = cat_emoji.get(cat, "📦")
        lines.append(f"*{emoji} {cat} 최저가*")
        for r in items:
            price_str = f"{int(r['price']):,}원" if r["price"] else "가격불명 ❌"
            lines.append(f"  • {r['name'][:35]:<35s} │ {price_str}")
        lines.append("")

    # ── 3) 전날 대비 가격 변동 ─────────────────────
    if prev_rows:
        prev_map = {r["pcode"]: r for r in prev_rows}
        changes = []
        for r in all_rows:
            prev = prev_map.get(r["pcode"])
            if prev and r["price"] and prev.get("price"):
                diff = int(r["price"]) - int(prev["price"])
                if diff != 0:
                    arrow = "🔺" if diff > 0 else "🔻"
                    changes.append(
                        f"  {arrow} {r['name'][:30]} │ {diff:+,}원 "
                        f"({int(prev['price']):,} → {int(r['price']):,})"
                    )
        if changes:
            lines.append("*📈 전날 대비 가격 변동*")
            lines.extend(changes)
            lines.append("")
        else:
            lines.append("*📈 전날 대비 가격 변동*")
            lines.append("  변동 없음")
            lines.append("")

    # ── 수집 요약 ───────────────────────────────────
    success = sum(1 for r in all_rows if r["price"])
    fail    = len(all_rows) - success
    lines.append(f"_수집: 총 {len(all_rows)}개 │ 성공 {success}개 │ 실패 {fail}개_")

    return "\n".join(lines)


# ── 메인 ──────────────────────────────────────────
def main():
    today = date.today().isoformat()
    log.info(f"=== 다나와 가격 수집 시작: {today} ===")

    OUTPUT_CSV      = Path(f"price_history_{today}.csv")
    GPU_SUMMARY_CSV = Path(f"gpu_group_summary_{today}.csv")
    log.info(f"저장 파일: {OUTPUT_CSV} / {GPU_SUMMARY_CSV}")

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

    # CSV 저장
    save_to_csv(all_rows, OUTPUT_CSV)

    gpu_summary = []
    if gpu_products:
        log.info("\n=== GPU 모델군 평균가 계산 ===")
        gpu_summary = calc_gpu_summary(today, gpu_products)
        save_gpu_summary(gpu_summary, GPU_SUMMARY_CSV)

    # 전날 CSV 로드 (가격 변동 비교용)
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    prev_csv  = Path(f"price_history_{yesterday}.csv")
    prev_rows = None
    if prev_csv.exists():
        with prev_csv.open(encoding="utf-8-sig") as f:
            prev_rows = list(csv.DictReader(f))
        log.info(f"전날 데이터 로드: {prev_csv} ({len(prev_rows)}행)")
    else:
        log.info("전날 CSV 없음 — 변동 비교 생략")

    # Slack 리포트 전송
    log.info("\n=== Slack 리포트 전송 ===")
    message = build_slack_message(today, all_rows, gpu_summary, prev_rows)
    send_slack_dm(message)

    # 결과 요약
    success = sum(1 for r in all_rows if r["price"])
    fail    = len(all_rows) - success
    log.info(f"\n=== 완료: 성공 {success}개 / 실패 {fail}개 ===")


if __name__ == "__main__":
    main()
