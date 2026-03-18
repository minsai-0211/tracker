"""
공통 모듈 — scraper_danawa.py / scraper_compuzone.py / scraper_compare.py 에서 공유
"""

import re
import logging
from pathlib import Path

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


def _clean_price(raw: str) -> int | None:
    """
    첫 번째 연속 숫자 그룹만 추출.
    → 배너 등 여러 숫자가 붙어 나오는 오파싱 방지.
    상한 1500만원 (GPU 최고가 기준), 하한 5만원.
    """
    m = re.search(r"[\d,]+", str(raw).replace("\n", " "))
    if not m:
        return None
    val = int(m.group().replace(",", ""))
    if not (50_000 <= val <= 15_000_000):
        return None
    return val


def parse_url_file(path: Path) -> dict[str, list[dict]]:
    """
    가격비교.txt / 컴퓨존_가격비교.txt 파싱.
    URL 없는 미확인 항목도 url=None 으로 보존 → index 밀림 방지.
    반환: { 카테고리: [{"name", "subcategory", "url"}, ...] }
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
                "name": name_part or None,
                "subcategory": current_sub,
                "url": url,
            })
            continue

        sub_m = re.match(r"^-\s*(.+)", line)
        if sub_m:
            text = sub_m.group(1).strip()
            if "미확인" in text:
                name_part = re.sub(r'\s*[-–]\s*미확인.*', '', text).strip()
                categories[current].append({
                    "name": name_part or None,
                    "subcategory": current_sub,
                    "url": None,
                })
            else:
                current_sub = text

    return categories
