"""
가격 비교 & Slack 리포트
- danawa_YYYY-MM-DD.csv + compuzone_YYYY-MM-DD.csv 를 읽어 1:1 매칭
- price_comparison_YYYY-MM-DD.csv, gpu_group_summary_YYYY-MM-DD.csv 저장
- Slack DM 리포트 전송
"""

import os
import csv
import logging
import requests
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict
from common import GPU_GROUPS, get_gpu_group

# ── 설정 ─────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID   = os.environ.get("SLACK_USER_ID", "")
# ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

COMPARE_FIELDNAMES = [
    "date", "category", "subcategory", "name",
    "danawa_price", "danawa_url",
    "compuzone_price", "compuzone_url",
    "price_diff", "cheaper",
]
GPU_FIELDNAMES = ["date", "gpu_group", "count", "avg_price", "min_price", "max_price"]


# ── CSV 로드 ──────────────────────────────────────────
def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} 파일을 찾을 수 없습니다.")
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ── 1:1 매칭 ─────────────────────────────────────────
def build_comparison(today: str, dw_rows: list[dict], cz_rows: list[dict]) -> list[dict]:
    """
    두 CSV를 (category, subcategory, name) 기준으로 매칭.
    컴퓨존에 없는 항목은 compuzone_price = None 으로 처리.
    """
    # 컴퓨존 rows를 (category, name) → row 로 인덱싱
    cz_map: dict[tuple, dict] = {}
    for r in cz_rows:
        key = (r["category"], r["name"])
        cz_map[key] = r

    result = []
    for dw in dw_rows:
        key    = (dw["category"], dw["name"])
        cz     = cz_map.get(key)

        dw_price = int(dw["price"]) if dw.get("price") else None
        cz_price = int(cz["price"]) if cz and cz.get("price") else None

        price_diff = None
        cheaper    = None
        if dw_price and cz_price:
            price_diff = cz_price - dw_price
            cheaper = "컴퓨존" if price_diff < 0 else ("다나와" if price_diff > 0 else "동일")

        result.append({
            "date":            today,
            "category":        dw["category"],
            "subcategory":     dw.get("subcategory", ""),
            "name":            dw["name"],
            "danawa_price":    dw_price,
            "danawa_url":      dw.get("url", ""),
            "compuzone_price": cz_price,
            "compuzone_url":   cz.get("url", "") if cz else "",
            "price_diff":      price_diff,
            "cheaper":         cheaper or "",
        })

    return result


# ── GPU 그룹 평균가 ───────────────────────────────────
def calc_gpu_summary(today: str, rows: list[dict]) -> list[dict]:
    group_prices: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r["category"] != "GPU" or not r["danawa_price"]:
            continue
        group = get_gpu_group(r["name"])
        if group:
            group_prices[group].append(r["danawa_price"])

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


# ── CSV 저장 ──────────────────────────────────────────
def save_comparison_csv(rows: list[dict], path: Path):
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COMPARE_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    log.info(f"비교 CSV 저장: {path} ({len(rows)}행)")

def save_gpu_summary(rows: list[dict], path: Path):
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=GPU_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    log.info(f"GPU 요약 저장: {path} ({len(rows)}행)")


# ── Slack ─────────────────────────────────────────────
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
    if msg_resp.json().get("ok"):
        log.info("✅ Slack DM 전송 완료")
    else:
        log.error(f"Slack 전송 실패: {msg_resp.json().get('error')}")


def build_slack_message(
    today: str,
    rows: list[dict],
    gpu_summary: list[dict],
    prev_rows: list[dict] | None = None,
) -> str:
    lines = [f"📊 *다나와 vs 컴퓨존 가격 리포트 — {today}*", ""]

    # GPU 평균가
    lines.append("*🎮 GPU 모델군 평균가 (다나와 기준)*")
    for g in gpu_summary:
        lines.append(
            f"  • {g['gpu_group']:12s} │ 평균 {int(g['avg_price']):>10,}원 "
            f"(최저 {int(g['min_price']):,} / 최고 {int(g['max_price']):,})"
        )
    lines.append("")

    # 카테고리별 제품 비교
    cat_emoji = {"CPU": "🖥️", "RAM": "🧠", "GPU": "🎮", "SSD": "💾"}
    cat_order = ["CPU", "RAM", "GPU", "SSD"]

    structured: dict[str, dict[str, list[dict]]] = {c: {} for c in cat_order}
    for r in rows:
        cat = r["category"]
        sub = r.get("subcategory") or "기타"
        structured.setdefault(cat, {}).setdefault(sub, []).append(r)

    for cat in cat_order:
        if not structured.get(cat):
            continue
        lines.append(f"*{cat_emoji.get(cat, '📦')} {cat}*")
        for sub, items in structured[cat].items():
            lines.append(f"  _{sub}_")
            for r in items:
                dw  = f"{int(r['danawa_price']):,}원"    if r["danawa_price"]    else "미확인 ❌"
                cz  = f"{int(r['compuzone_price']):,}원" if r["compuzone_price"] else "미확인 ❌"
                if r["cheaper"] == "컴퓨존":
                    diff_str = f"컴퓨존 {abs(r['price_diff']):,}원 저렴 🔵"
                elif r["cheaper"] == "다나와":
                    diff_str = f"다나와 {abs(r['price_diff']):,}원 저렴 🟢"
                elif r["cheaper"] == "동일":
                    diff_str = "동일가 ⚪"
                else:
                    diff_str = "비교불가"
                lines.append(
                    f"    • {r['name'][:28]:<28s} │ 다나와 {dw:>12s} │ 컴퓨존 {cz:>12s} │ {diff_str}"
                )
        lines.append("")

    # 전날 대비 변동 (다나와 기준)
    if prev_rows:
        prev_map = {(r["category"], r["name"]): r for r in prev_rows}
        changes = []
        for r in rows:
            prev = prev_map.get((r["category"], r["name"]))
            if prev and r["danawa_price"] and prev.get("danawa_price"):
                diff = int(r["danawa_price"]) - int(prev["danawa_price"])
                if diff != 0:
                    arrow = "🔺" if diff > 0 else "🔻"
                    changes.append(
                        f"  {arrow} {r['name'][:28]} │ {diff:+,}원 "
                        f"({int(prev['danawa_price']):,} → {int(r['danawa_price']):,})"
                    )
        lines.append("*📈 전날 대비 가격 변동 (다나와)*")
        lines.extend(changes) if changes else lines.append("  변동 없음")
        lines.append("")

    dw_ok = sum(1 for r in rows if r["danawa_price"])
    cz_ok = sum(1 for r in rows if r["compuzone_price"])
    lines.append(f"_수집: 총 {len(rows)}개 │ 다나와 성공 {dw_ok}개 │ 컴퓨존 성공 {cz_ok}개_")

    return "\n".join(lines)


# ── 메인 ─────────────────────────────────────────────
def main():
    today = date.today().isoformat()
    log.info(f"=== 가격 비교 & Slack 리포트: {today} ===")

    dw_csv = Path(f"data/danawa/danawa_{today}.csv")
    cz_csv = Path(f"data/compuzone/compuzone_{today}.csv")

    dw_rows = load_csv(dw_csv)
    cz_rows = load_csv(cz_csv)
    log.info(f"다나와 {len(dw_rows)}행, 컴퓨존 {len(cz_rows)}행 로드 완료")

    rows        = build_comparison(today, dw_rows, cz_rows)
    gpu_summary = calc_gpu_summary(today, rows)

    compare_dir = Path("data/price_comparison")
    compare_dir.mkdir(parents=True, exist_ok=True)
    save_comparison_csv(rows, compare_dir / f"price_comparison_{today}.csv")

    if gpu_summary:
        gpu_dir = Path("data/gpu_group_summary")
        gpu_dir.mkdir(parents=True, exist_ok=True)
        save_gpu_summary(gpu_summary, gpu_dir / f"gpu_group_summary_{today}.csv")

    # 전날 비교
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    prev_path = Path(f"data/price_comparison/price_comparison_{yesterday}.csv")
    prev_rows = None
    if prev_path.exists():
        prev_rows = load_csv(prev_path)
        log.info(f"전날 데이터 로드: {prev_path} ({len(prev_rows)}행)")
    else:
        log.info("전날 CSV 없음 — 변동 비교 생략")

    message = build_slack_message(today, rows, gpu_summary, prev_rows)
    send_slack_dm(message)

    dw_ok = sum(1 for r in rows if r["danawa_price"])
    cz_ok = sum(1 for r in rows if r["compuzone_price"])
    log.info(f"\n=== 완료: 총 {len(rows)}개 │ 다나와 성공 {dw_ok}개 │ 컴퓨존 성공 {cz_ok}개 ===")


if __name__ == "__main__":
    main()
