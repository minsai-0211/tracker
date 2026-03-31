"""
최신 price_comparison CSV를 읽어 docs/data.json으로 변환합니다.
GitHub Actions daily_scrape.yml의 compare job에서 실행됩니다.
"""

import csv
import json
import os
import glob


def find_latest_csv():
    files = glob.glob("data/price_comparison/price_comparison_*.csv")
    if not files:
        return None
    return sorted(files)[-1]


def parse_price(value):
    if not value or value.strip() == "":
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def load_price_comparison(filepath):
    products = []
    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            danawa_price = parse_price(row.get("danawa_price"))
            compuzone_price = parse_price(row.get("compuzone_price"))
            shopdanawa_price = parse_price(row.get("shopdanawa_price"))

            prices = [p for p in [danawa_price, compuzone_price, shopdanawa_price] if p]
            min_price = min(prices) if prices else None

            products.append({
                "category": row["category"],
                "subcategory": row["subcategory"],
                "name": row["name"],
                "danawa_price": danawa_price,
                "danawa_url": row.get("danawa_url") or None,
                "compuzone_price": compuzone_price,
                "compuzone_url": row.get("compuzone_url") or None,
                "shopdanawa_price": shopdanawa_price,
                "shopdanawa_url": row.get("shopdanawa_url") or None,
                "cheapest": row.get("cheapest", ""),
                "min_price": min_price,
            })
    return products


def main():
    filepath = find_latest_csv()
    if not filepath:
        print("price_comparison CSV 파일을 찾을 수 없습니다.")
        return

    filename = os.path.basename(filepath)
    date_str = filename.replace("price_comparison_", "").replace(".csv", "")

    products = load_price_comparison(filepath)

    data = {
        "date": date_str,
        "products": products,
    }

    os.makedirs("docs", exist_ok=True)
    output_path = "docs/data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ {output_path} 생성 완료 ({date_str}, 총 {len(products)}개 제품)")


if __name__ == "__main__":
    main()
