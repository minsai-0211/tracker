"""
다나와 + 컴퓨존 가격비교 자동 수집기 v4
- 가격비교.txt       → 다나와 URL
- 컴퓨존_가격비교.txt → 컴퓨존 URL
- 동일 제품을 나란히 비교해 CSV 및 Slack 리포트 출력
"""

import os
import re
import csv
import time
import random
import logging
import requests
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict
from bs4 import BeautifulSoup

# ── 설정 ────────────────────────────────────────────
DANAWA_FILE   = Path("가격비교.txt")
COMPUZONE_FILE = Path("컴퓨존_가격비교.txt")
DELAY_MIN = 1.5
DELAY_MAX = 3.5

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID   = os.environ.get("SLACK_USER_ID", "")
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


# ── URL 파일 파싱 (다나와 / 컴퓨존 공통) ─────────────
def parse_url_file(path: Path) -> dict[str, list[dict]]:
    """
    가격비교.txt / 컴퓨존_가격비교.txt 를 읽어
    { 카테고리: [{"name": 제품명, "subcategory": 서브카테고리, "url": url}, ...] } 반환.
    """
    categories: dict[str, list[dict]] = {}
    current = None
    current_sub = None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue

        m = re.match(r"^\d+\.\s*(.+)", line)
        if m:
            current = m.group(1).strip()
            current_sub = None
            categories[current] = []
            continue

        if current is None:
            continue

        url_match = re.search(r'(https?://\S+)', line)
        if url_match:
            url = url_match.group(1)
            name_part = line[:url_match.start()].strip().rstrip('-').strip()
            categories[current].append({
                "name": name_part if name_part else None,
                "subcategory": current_sub,
                "url": url,
            })
            continue

        sub_m = re.match(r"^-\s*(.+)", line)
        if sub_m:
            current_sub = sub_m.group(1).strip()

    return categories


# ── 다나와 가격 파싱 ──────────────────────────────────
def fetch_danawa(item: dict) -> dict:
    url = item["url"]
    preset_name = item.get("name")

    pcode_m = re.search(r"pcode=(\d+)", url)
    pcode = pcode_m.group(1) if pcode_m else "unknown"

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
        f"  [다나와] [{pcode}] {name or '이름불명'} → {price:,}원"
        if price else
        f"  [다나와] [{pcode}] {name or '이름불명'} → 가격불명"
    )
    return {"pcode": pcode, "name": name or "이름불명", "price": price, "url": url}


# ── 컴퓨존 가격 파싱 ──────────────────────────────────
def fetch_compuzone(item: dict) -> dict:
    url = item["url"]
    preset_name = item.get("name")

    pno_m = re.search(r"ProductNo=(\d+)", url)
    product_no = pno_m.group(1) if pno_m else "unknown"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"  [컴퓨존] 요청 실패 [ProductNo={product_no}]: {e}")
        return {"product_no": product_no, "name": preset_name or "요청실패", "price": None, "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")

    name = preset_name
    if not name:
        for sel in ["h1.prod_name", "h2.prod_name", "title"]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get_text(" ", strip=True)
                raw = re.sub(r"\s*:\s*컴퓨존.*", "", raw)
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

    # 2순위: 판매가/최저가 텍스트 패턴
    if not price:
        text = soup.get_text(" ")
        for pattern in [
            r"판매가\s*([\d,]+)\s*원",
            r"최저가\s*([\d,]+)\s*원",
            r"즉시할인가\s*([\d,]+)\s*원",
        ]:
            m = re.search(pattern, text)
            if m:
                price = _clean_price(m.group(1))
                if price:
                    break

    # 3순위: 가격 관련 class 스캔
    if not price:
        for span in soup.find_all(["span", "strong", "em"],
                                   class_=re.compile(r"price|Price|sell|Sale|cost|Cost", re.I)):
            candidate = _clean_price(span.get_text())
            if candidate and 10_000 <= candidate <= 15_000_000:
                price = candidate
                break

    log.info(
        f"  [컴퓨존] [ProductNo={product_no}] {name or '이름불명'} → {price:,}원"
        if price else
        f"  [컴퓨존] [ProductNo={product_no}] {name or '이름불명'} → 가격불명"
    )
    return {"product_no": product_no, "name": name or "이름불명", "price": price, "url": url}


def _clean_price(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", str(raw))
    val = int(digits) if digits else None
    # 단위 이상한 값 필터 (10만원 미만, 1.5억 초과는 제외)
    if val and not (100_000 <= val <= 150_000_000):
        return None
    return val


# ── 두 소스 매칭 및 비교 행 생성 ──────────────────────
def build_comparison_rows(
    today: str,
    cat: str,
    danawa_items: list[dict],
    cz_items: list[dict],
    danawa_results: dict,   # pcode → fetch 결과
    cz_results: dict,       # product_no → fetch 결과
) -> list[dict]:
    """
    같은 순서로 등록된 제품을 index 기반으로 1:1 매칭해 비교 행을 만든다.
    (두 파일의 제품 순서가 동일하다고 가정)
    """
    rows = []
    length = max(len(danawa_items), len(cz_items))

    for i in range(length):
        dw_item = danawa_items[i] if i < len(danawa_items) else None
        cz_item = cz_items[i]    if i < len(cz_items)    else None

        dw_res = None
        cz_res = None
        name   = None
        subcat = None

        if dw_item:
            pcode  = re.search(r"pcode=(\d+)", dw_item["url"])
            pcode  = pcode.group(1) if pcode else "unknown"
            dw_res = danawa_results.get(pcode)
            name   = dw_item.get("name") or (dw_res and dw_res.get("name"))
            subcat = dw_item.get("subcategory")

        if cz_item:
            pno    = re.search(r"ProductNo=(\d+)", cz_item["url"])
            pno    = pno.group(1) if pno else "unknown"
            cz_res = cz_results.get(pno)
            if not name:
                name = cz_item.get("name") or (cz_res and cz_res.get("name"))
            if not subcat:
                subcat = cz_item.get("subcategory")

        dw_price = dw_res["price"] if dw_res else None
        cz_price = cz_res["price"] if cz_res else None

        # 가격 차이 계산 (컴퓨존 - 다나와)
        price_diff = None
        cheaper    = None
        if dw_price and cz_price:
            price_diff = cz_price - dw_price
            if price_diff < 0:
                cheaper = "컴퓨존"
            elif price_diff > 0:
                cheaper = "다나와"
            else:
                cheaper = "동일"

        rows.append({
            "date":           today,
            "category":       cat,
            "subcategory":    subcat or "",
            "name":           name or "이름불명",
            "danawa_price":   dw_price,
            "danawa_url":     dw_item["url"] if dw_item else "",
            "compuzone_price": cz_price,
            "compuzone_url":  cz_item["url"] if cz_item else "",
            "price_diff":     price_diff,   # 컴퓨존 - 다나와 (음수면 컴퓨존이 저렴)
            "cheaper":        cheaper or "",
        })

    return rows


# ── GPU 그룹 평균가 계산 (다나와 기준) ────────────────
def calc_gpu_summary(today: str, gpu_rows: list[dict]) -> list[dict]:
    group_prices: dict[str, list[int]] = defaultdict(list)

    for r in gpu_rows:
        if not r["danawa_price"] or not r["name"]:
            continue
        group = get_gpu_group(r["name"])
        if group:
            group_prices[group].append(r["danawa_price"])
        else:
            log.warning(f"  GPU 그룹 미분류: {r['name']}")

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


# ── CSV 저장 ───────────────────────────────────────
COMPARE_FIELDNAMES = [
    "date", "category", "subcategory", "name",
    "danawa_price", "danawa_url",
    "compuzone_price", "compuzone_url",
    "price_diff", "cheaper",
]
GPU_FIELDNAMES = ["date", "gpu_group", "count", "avg_price", "min_price", "max_price"]

def save_comparison_csv(rows: list[dict], path: Path):
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COMPARE_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    log.info(f"비교 CSV 저장 완료: {path} ({len(rows)}행 추가)")

def save_gpu_summary(gpu_rows: list[dict], path: Path):
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=GPU_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(gpu_rows)
    log.info(f"GPU 그룹 요약 저장 완료: {path} ({len(gpu_rows)}행 추가)")


# ── Slack 전송 ─────────────────────────────────────
def send_slack_dm(message: str):
    if not SLACK_BOT_TOKEN or not SLACK_USER_ID:
        log.warning("Slack 환경변수 미설정 — 전송 생략")
        return

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
    lines = [f"📊 *다나와 vs 컴퓨존 가격 리포트 — {today}*", ""]

    # ── 1) GPU 그룹 평균가 요약 (다나와 기준) ──────────
    lines.append("*🎮 GPU 모델군 평균가 (다나와 기준)*")
    for g in gpu_summary:
        lines.append(
            f"  • {g['gpu_group']:12s} │ 평균 {g['avg_price']:>10,}원 "
            f"(최저 {g['min_price']:,} / 최고 {g['max_price']:,})"
        )
    lines.append("")

    # ── 2) 카테고리별 다나와 vs 컴퓨존 비교 ──────────
    cat_emoji = {"CPU": "🖥️", "RAM": "🧠", "GPU": "🎮", "SSD": "💾"}
    cat_order = ["CPU", "RAM", "GPU", "SSD"]

    structured: dict[str, dict[str, list[dict]]] = {c: {} for c in cat_order}
    for r in all_rows:
        cat = r["category"]
        sub = r.get("subcategory") or "기타"
        if cat not in structured:
            structured[cat] = {}
        structured[cat].setdefault(sub, []).append(r)

    for cat in cat_order:
        if not structured.get(cat):
            continue
        emoji = cat_emoji.get(cat, "📦")
        lines.append(f"*{emoji} {cat}*")
        for sub, items in structured[cat].items():
            lines.append(f"  _{sub}_")
            for r in items:
                dw  = f"{int(r['danawa_price']):,}원"   if r["danawa_price"]   else "미확인 ❌"
                cz  = f"{int(r['compuzone_price']):,}원" if r["compuzone_price"] else "미확인 ❌"

                if r["cheaper"] == "컴퓨존":
                    diff_str = f"컴퓨존 {abs(r['price_diff']):,}원 저렴 🟢"
                elif r["cheaper"] == "다나와":
                    diff_str = f"다나와 {abs(r['price_diff']):,}원 저렴 🔵"
                elif r["cheaper"] == "동일":
                    diff_str = "동일가 ⚪"
                else:
                    diff_str = "비교불가"

                name_str = r["name"][:28]
                lines.append(
                    f"    • {name_str:<28s} │ 다나와 {dw:>12s} │ 컴퓨존 {cz:>12s} │ {diff_str}"
                )
        lines.append("")

    # ── 3) 전날 대비 가격 변동 (다나와 기준) ──────────
    if prev_rows:
        prev_map = {(r["category"], r["name"]): r for r in prev_rows}
        changes = []
        for r in all_rows:
            key = (r["category"], r["name"])
            prev = prev_map.get(key)
            if prev and r["danawa_price"] and prev.get("danawa_price"):
                diff = int(r["danawa_price"]) - int(prev["danawa_price"])
                if diff != 0:
                    arrow = "🔺" if diff > 0 else "🔻"
                    changes.append(
                        f"  {arrow} {r['name'][:28]} │ {diff:+,}원 "
                        f"({int(prev['danawa_price']):,} → {int(r['danawa_price']):,})"
                    )
        lines.append("*📈 전날 대비 가격 변동 (다나와)*")
        if changes:
            lines.extend(changes)
        else:
            lines.append("  변동 없음")
        lines.append("")

    # ── 수집 요약 ───────────────────────────────────
    dw_ok  = sum(1 for r in all_rows if r["danawa_price"])
    cz_ok  = sum(1 for r in all_rows if r["compuzone_price"])
    total  = len(all_rows)
    lines.append(
        f"_수집: 총 {total}개 │ 다나와 성공 {dw_ok}개 │ 컴퓨존 성공 {cz_ok}개_"
    )

    return "\n".join(lines)


# ── 메인 ──────────────────────────────────────────
def main():
    today = date.today().isoformat()
    log.info(f"=== 다나와 + 컴퓨존 가격 수집 시작: {today} ===")

    COMPARE_CSV     = Path(f"price_comparison_{today}.csv")
    GPU_SUMMARY_CSV = Path(f"gpu_group_summary_{today}.csv")
    log.info(f"저장 파일: {COMPARE_CSV} / {GPU_SUMMARY_CSV}")

    if not DANAWA_FILE.exists():
        raise FileNotFoundError(f"{DANAWA_FILE} 파일을 찾을 수 없습니다.")
    if not COMPUZONE_FILE.exists():
        raise FileNotFoundError(f"{COMPUZONE_FILE} 파일을 찾을 수 없습니다.")

    danawa_cats   = parse_url_file(DANAWA_FILE)
    compuzone_cats = parse_url_file(COMPUZONE_FILE)

    # ── 다나와 수집 ──
    log.info("\n=== [1/2] 다나와 수집 시작 ===")
    danawa_results: dict[str, dict] = {}   # pcode → result
    for cat, items in danawa_cats.items():
        log.info(f"\n[다나와/{cat}] {len(items)}개 처리 중...")
        for item in items:
            res = fetch_danawa(item)
            danawa_results[res["pcode"]] = res
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # ── 컴퓨존 수집 ──
    log.info("\n=== [2/2] 컴퓨존 수집 시작 ===")
    cz_results: dict[str, dict] = {}       # product_no → result
    for cat, items in compuzone_cats.items():
        log.info(f"\n[컴퓨존/{cat}] {len(items)}개 처리 중...")
        for item in items:
            res = fetch_compuzone(item)
            cz_results[res["product_no"]] = res
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # ── 비교 행 생성 ──
    log.info("\n=== 가격 비교 매칭 ===")
    all_rows: list[dict] = []
    gpu_rows: list[dict] = []

    all_cats = sorted(set(list(danawa_cats.keys()) + list(compuzone_cats.keys())))
    for cat in all_cats:
        dw_items = danawa_cats.get(cat, [])
        cz_items = compuzone_cats.get(cat, [])
        rows = build_comparison_rows(today, cat, dw_items, cz_items, danawa_results, cz_results)
        all_rows.extend(rows)
        if cat == "GPU":
            gpu_rows.extend(rows)

    # ── CSV 저장 ──
    save_comparison_csv(all_rows, COMPARE_CSV)

    gpu_summary = []
    if gpu_rows:
        log.info("\n=== GPU 모델군 평균가 계산 ===")
        gpu_summary = calc_gpu_summary(today, gpu_rows)
        save_gpu_summary(gpu_summary, GPU_SUMMARY_CSV)

    # ── 전날 데이터 로드 ──
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    prev_csv  = Path(f"price_comparison_{yesterday}.csv")
    prev_rows = None
    if prev_csv.exists():
        with prev_csv.open(encoding="utf-8-sig") as f:
            prev_rows = list(csv.DictReader(f))
        log.info(f"전날 데이터 로드: {prev_csv} ({len(prev_rows)}행)")
    else:
        log.info("전날 CSV 없음 — 변동 비교 생략")

    # ── Slack 리포트 ──
    log.info("\n=== Slack 리포트 전송 ===")
    message = build_slack_message(today, all_rows, gpu_summary, prev_rows)
    send_slack_dm(message)

    dw_ok = sum(1 for r in all_rows if r["danawa_price"])
    cz_ok = sum(1 for r in all_rows if r["compuzone_price"])
    log.info(f"\n=== 완료: 총 {len(all_rows)}개 │ 다나와 성공 {dw_ok}개 │ 컴퓨존 성공 {cz_ok}개 ===")


if __name__ == "__main__":
    main()
