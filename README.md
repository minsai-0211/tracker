# 다나와 + 컴퓨존 + 샵다나와 가격 트래커 v5

가격비교.txt (다나와) + 컴퓨존_가격비교.txt (컴퓨존) + 샵다나와_가격비교.txt (샵다나와)에 등록된 제품 링크를
매일 자동으로 수집하고, 3사 가격을 비교해 CSV로 누적 저장합니다.

## 파일 구조

```
├── scraper_danawa.py                 # 다나와 수집 (requests)
├── scraper_compuzone.py              # 컴퓨존 수집 (Playwright)
├── scraper_shopdanawa.py             # 샵다나와 수집 (requests)
├── scraper_compare.py                # 3사 비교 + Slack 리포트
├── common.py                         # 공통 유틸 (파싱, GPU 그룹 등)
├── 가격비교.txt                       # 다나와 URL 목록
├── 컴퓨존_가격비교.txt                 # 컴퓨존 URL 목록
├── 샵다나와_가격비교.txt               # 샵다나와 URL 목록
├── requirements.txt
├── data/
│   ├── danawa/                       # danawa_YYYY-MM-DD.csv
│   ├── compuzone/                    # compuzone_YYYY-MM-DD.csv
│   ├── shopdanawa/                   # shopdanawa_YYYY-MM-DD.csv
│   ├── price_comparison/             # price_comparison_YYYY-MM-DD.csv
│   └── gpu_group_summary/            # gpu_group_summary_YYYY-MM-DD.csv
└── .github/workflows/daily_scrape.yml
```

## 출력 파일

### price_comparison_YYYY-MM-DD.csv — 다나와 vs 컴퓨존 vs 샵다나와 비교
| 컬럼 | 설명 |
|------|------|
| date | 수집 날짜 |
| category | CPU / RAM / GPU / SSD |
| subcategory | 서브카테고리 |
| name | 제품명 |
| danawa_price | 다나와 최저가 (원) |
| danawa_url | 다나와 링크 |
| compuzone_price | 컴퓨존 판매가 (원) |
| compuzone_url | 컴퓨존 링크 |
| shopdanawa_price | 샵다나와 판매가 (원) |
| shopdanawa_url | 샵다나와 링크 |
| cheapest | 최저가 쇼핑몰 (동일가 시 / 구분으로 복수 표시) |

### gpu_group_summary_YYYY-MM-DD.csv — GPU 모델군 평균가 (다나와 기준)
| 컬럼 | 설명 |
|------|------|
| date | 수집 날짜 |
| gpu_group | RTX 5090 / RTX 5080 / RTX 5070 Ti / RTX 5070 / RTX 5060 Ti / RTX 5060 / RX 9070 XT / RX 9060 XT |
| count | 해당 그룹 수집 제품 수 |
| avg_price | 평균가 (원) |
| min_price | 최저가 (원) |
| max_price | 최고가 (원) |

## URL 파일 형식 (3개 파일 공통)

```
1. CPU

- 게이밍 & 작업용 (AMD)
AMD 라이젠9-6세대 9950X3D (그래니트 릿지) (멀티팩 정품) - https://...
...

2. RAM
...
```

> ⚠️ **중요:** 세 URL 파일의 제품 순서는 반드시 동일해야 합니다.
> 순서가 다르면 가격 매칭이 어긋납니다.

## 로컬 실행

```bash
pip install -r requirements.txt
playwright install chromium --with-deps  # 컴퓨존 수집 시 필요

python scraper_danawa.py      # 다나와 수집
python scraper_compuzone.py   # 컴퓨존 수집
python scraper_shopdanawa.py  # 샵다나와 수집
python scraper_compare.py     # 비교 & Slack 리포트
```

## GitHub Actions 자동화

- 매일 오전 09:05 KST 자동 실행 (UTC 00:05)
- Job 1 (다나와) + Job 2 (컴퓨존) + Job 3 (샵다나와) 병렬 수집 → Job 4 (비교 & Slack) 순으로 실행
- 수집 결과를 `data/` 하위 폴더에 날짜별로 누적 커밋
- Actions 탭 → "다나와 가격 자동 수집" → Run workflow 로 수동 실행 가능
- Secrets 설정: `SLACK_BOT_TOKEN`, `SLACK_USER_ID`

## Slack 리포트 예시

```
📊 다나와 vs 컴퓨존 vs 샵다나와 가격 리포트 — 2026-03-24

🎮 GPU 모델군 평균가 (다나와 기준)
  • RTX 5090      │ 평균  3,200,000원 (최저 2,980,000 / 최고 3,450,000)
  • RTX 5080      │ 평균  1,800,000원 ...

🖥️ CPU
  _게이밍 & 작업용 (AMD)_
    • AMD 라이젠9-6세대 9950X3D    │ 다나와  1,059,000원 │ 컴퓨존  1,065,000원 │ 샵다나와  1,059,220원 │ 최저: 다나와 🏆
    • AMD 라이젠7-6세대 9800X3D    │ 다나와    620,000원 │ 컴퓨존    615,000원 │ 샵다나와    618,000원 │ 최저: 컴퓨존 🏆
```
