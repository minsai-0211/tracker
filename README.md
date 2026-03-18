# 다나와 가격 트래커 v3

가격비교.txt 에 등록된 다나와 제품 링크를 매일 자동으로 수집하고 CSV로 누적 저장합니다.

## 파일 구조

```
├── scraper.py                          # 메인 수집 스크립트
├── 가격비교.txt                         # 수집 대상 제품 목록 (URL 관리)
├── requirements.txt
├── price_history_YYYY-MM-DD.csv        # 개별 제품 날짜별 가격 (자동 생성)
├── gpu_group_summary_YYYY-MM-DD.csv    # GPU 모델군별 평균가 (자동 생성)
└── .github/workflows/daily_scrape.yml
```

## 출력 파일

### price_history_YYYY-MM-DD.csv — 개별 제품
| 컬럼 | 설명 |
|------|------|
| date | 수집 날짜 |
| category | CPU / RAM / GPU / SSD |
| subcategory | 서브카테고리 |
| pcode | 다나와 제품코드 |
| name | 제품명 |
| price | 최저가 (원) |
| url | 다나와 링크 |

### gpu_group_summary_YYYY-MM-DD.csv — GPU 모델군 평균가
| 컬럼 | 설명 |
|------|------|
| date | 수집 날짜 |
| gpu_group | RTX 5090 / RTX 5080 / RTX 5070 Ti / RTX 5070 / RTX 5060 Ti / RTX 5060 / RX 9070 XT / RX 9060 XT |
| count | 해당 그룹 수집 제품 수 |
| avg_price | 평균가 (원) |
| min_price | 최저가 (원) |
| max_price | 최고가 (원) |

## 가격비교.txt 형식

```
1. CPU

- 게이밍 & 작업용 (AMD)
AMD 라이젠9-6세대 9950X3D (그래니트 릿지) (멀티팩 정품) - https://prod.danawa.com/info/?pcode=...

2. RAM
...
```

## 로컬 실행

```bash
pip install -r requirements.txt
python scraper.py
```

## GitHub Actions 자동화

- 매일 오전 09:00 KST 자동 실행
- 수집 결과를 price_history_*.csv, gpu_group_summary_*.csv 에 날짜별로 커밋
- Actions 탭 → "다나와 가격 자동 수집" → Run workflow 로 수동 실행 가능
- Secrets 설정: `SLACK_BOT_TOKEN`, `SLACK_USER_ID`
