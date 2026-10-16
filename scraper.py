"""
Cooktop Price Comparison Scraper
Scrapes cooktop data from MediaMarkt.pl and compares prices with Ceneo.pl.
Generates comparison links for Euro RTV AGD and Komputronik, maintains price history,
and saves the output to data/products.json.
"""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

KNOWN_BRANDS = ['ELECTROLUX', 'BOSCH', 'BEKO', 'AMICA', 'SAMSUNG', 'WHIRLPOOL', 'MIDEA', 'SIEMENS', 'GORENJE', 'TEKA', 'HAIER', 'HISENSE']
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
OUTPUT_FILE = os.path.join(DATA_DIR, 'products.json')
OUTPUT_JS_FILE = os.path.join(DATA_DIR, 'products.js')

MEDIAMARKT_INDUCTION_URL = "https://mediamarkt.pl/pl/category/plyty-indukcyjne-do-zabudowy-25591.html"
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
}


def extract_brand(name: str) -> str:
    """Extract brand from product name using known brands list."""
    name_upper = name.upper()
    for brand in KNOWN_BRANDS:
        if re.search(r'\b' + re.escape(brand) + r'\b', name_upper):
            return brand
    return "INNA"


def extract_model(name: str, brand: str) -> str:
    """Extract model number from product name using regex patterns."""
    name_clean = " ".join(name.split())
    # 1. Try word directly following brand name
    if brand and brand != "INNA":
        m = re.search(r'\b' + re.escape(brand) + r'\b\s+([A-Za-z0-9/-]+)', name_clean, re.IGNORECASE)
        if m:
            candidate = m.group(1).split('/')[0].strip().upper()
            if (re.search(r'[A-Z]', candidate) and
                    re.search(r'\d', candidate) and
                    len(candidate) >= 4 and
                    candidate not in {'HOB2HOOD', 'POWERBOOST', 'SLIMFIT', 'FLEXICOOK'} and
                    not re.match(r'^\d+(V|W|HZ|CM)$', candidate)):
                return candidate

    # 2. General search for model token containing letters and digits
    excluded = {'HOB2HOOD', 'POWERBOOST', 'SLIMFIT', 'SLIM-FIT', 'FLEXICOOK', 'HOME-CONNECT'}
    tokens = re.findall(r'\b[A-Za-z0-9-]{4,18}\b', name_clean)
    for t in tokens:
        t_upper = t.upper().split('/')[0]
        if t_upper in excluded or re.match(r'^\d+(V|W|HZ|CM)$', t_upper):
            continue
        if re.search(r'[A-Z]', t_upper) and re.search(r'\d', t_upper):
            return t_upper

    return ""


def extract_size(name: str, model: str) -> dict:
    """
    Extract cooktop width/dimensions from title and model heuristics.
    Categorizes into: '30cm', '60cm', '75cm', '80cm', '90cm'.
    """
    m_cm = re.search(r'(\d{2,3})\s*(?:cm|mm)\b', name, re.IGNORECASE)
    raw_val = None
    if m_cm:
        val = int(m_cm.group(1))
        if val > 100:
            raw_val = round(val / 10)
        else:
            raw_val = val

    if raw_val is None:
        model_upper = (model or "").upper()
        name_upper = name.upper()
        if re.search(r'\b(EIV8|EIS8|EIT8|PIE8|PVS8|PVQ8|PXX8|NZ8|I8)\w*', model_upper or name_upper):
            raw_val = 80
        elif re.search(r'\b(EIV3|EIS3|PIE3|PXX3|PRB3|NZ3|PI3)\w*', model_upper or name_upper):
            raw_val = 30
        elif re.search(r'\b(EIV7|EIS7|PVS7|PVQ7)\w*', model_upper or name_upper):
            raw_val = 75
        elif re.search(r'\b(EIV9|EIS9|PIE9|PXX9|NZ9)\w*', model_upper or name_upper):
            raw_val = 90
        elif 'WLS7' in model_upper or 'WFS7' in model_upper or '7560' in model_upper:
            raw_val = 75
        else:
            raw_val = 60

    if raw_val <= 36:
        category = "30cm"
        label = "30 cm (Domino / 2 pola)"
        width = 30
    elif 55 <= raw_val <= 66:
        category = "60cm"
        label = "60 cm (standard)"
        width = 60
    elif 67 <= raw_val <= 76:
        category = "75cm"
        label = "70-75 cm"
        width = 75
    elif 77 <= raw_val <= 85:
        category = "80cm"
        label = "80 cm"
        width = 80
    elif raw_val >= 86:
        category = "90cm"
        label = "90 cm"
        width = 90
    else:
        category = "60cm"
        label = f"{raw_val} cm"
        width = raw_val

    return {
        "widthCm": width,
        "rawWidth": raw_val,
        "label": label,
        "category": category
    }


def calculate_price_change(history: list[dict], current_mm_price: float | int | None) -> dict:
    """Calculate day-to-day price difference between latest price and previous date entry."""
    if not history or current_mm_price is None:
        return {"trend": "stable", "delta": 0, "percent": 0.0, "previousPrice": None, "previousDate": None}

    today_date = datetime.now().strftime("%Y-%m-%d")
    previous_entries = [h for h in history if h.get('date') != today_date and h.get('mediamarkt')]

    if not previous_entries:
        if len(history) >= 2 and history[-2].get('mediamarkt'):
            prev_entry = history[-2]
        else:
            return {"trend": "stable", "delta": 0, "percent": 0.0, "previousPrice": current_mm_price, "previousDate": today_date}
    else:
        prev_entry = previous_entries[-1]

    prev_price = prev_entry.get('mediamarkt')
    prev_date = prev_entry.get('date')

    if prev_price is None or prev_price <= 0:
        return {"trend": "stable", "delta": 0, "percent": 0.0, "previousPrice": None, "previousDate": prev_date}

    delta = round(current_mm_price - prev_price, 2)
    percent = round((delta / prev_price) * 100, 1)

    if delta < -0.5:
        trend = "down"
    elif delta > 0.5:
        trend = "up"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "delta": delta,
        "percent": percent,
        "previousPrice": prev_price,
        "previousDate": prev_date
    }


def extract_type(name: str) -> str:
    """Detect product type from name."""
    lower_name = name.lower()
    if 'indukcyj' in lower_name:
        return 'induction'
    if 'gazow' in lower_name:
        return 'gas'
    if 'ceramiczn' in lower_name:
        return 'ceramic'
    return 'induction'


def calculate_value_score(rating: float | None, review_count: int, price: float | int | None) -> float:
    """Calculate value score: (rating * sqrt(reviewCount)) / price * 1000."""
    if not rating or rating <= 0 or review_count <= 0 or not price or price <= 0:
        return 0.0
    score = (rating * math.sqrt(review_count)) / price * 1000
    return round(score, 1)


def parse_price(val) -> float | int | None:
    """Parse numeric price safely."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        p = float(val)
        return int(p) if p.is_integer() else round(p, 2)
    val_clean = str(val).replace('\xa0', ' ').replace(' ', '').replace(',', '.')
    match = re.search(r'(\d+(?:\.\d+)?)', val_clean)
    if match:
        p = float(match.group(1))
        return int(p) if p.is_integer() else round(p, 2)
    return None


def fetch_all_induction_products(session: requests.Session, max_pages: int = 25) -> list[dict]:
    """Fetch all pages of built-in induction cooktops from MediaMarkt."""
    products_raw = []
    seen_urls = set()

    print(f"Scraping MediaMarkt induction cooktops from {MEDIAMARKT_INDUCTION_URL}...")
    for page in range(1, max_pages + 1):
        url = f"{MEDIAMARKT_INDUCTION_URL}?page={page}" if page > 1 else MEDIAMARKT_INDUCTION_URL
        try:
            response = session.get(url, headers=DEFAULT_HEADERS, timeout=15)
            if response.status_code != 200:
                print(f"  Page {page} returned status {response.status_code}, stopping pagination.")
                break
        except Exception as e:
            print(f"  Error fetching page {page}: {e}")
            break

        soup = BeautifulSoup(response.text, 'html.parser')
        scripts = soup.find_all('script', type='application/ld+json')
        page_items = 0

        for script in scripts:
            if not script.string or 'ItemList' not in script.string:
                continue
            try:
                data = json.loads(script.string)
            except Exception:
                continue

            entries = []
            if isinstance(data, list):
                entries.extend(data)
            elif isinstance(data, dict):
                if '@graph' in data and isinstance(data['@graph'], list):
                    entries.extend(data['@graph'])
                entries.append(data)

            for entry in entries:
                if isinstance(entry, dict) and entry.get('@type') == 'ItemList':
                    for list_item in entry.get('itemListElement', []):
                        if isinstance(list_item, dict):
                            prod = list_item.get('item', list_item)
                            if isinstance(prod, dict) and prod.get('name'):
                                p_url = prod.get('url', '')
                                if p_url and p_url in seen_urls:
                                    continue
                                if p_url:
                                    seen_urls.add(p_url)
                                products_raw.append(prod)
                                page_items += 1

        print(f"  Page {page}: {page_items} items collected (Total so far: {len(products_raw)})")

        next_link = soup.find('link', rel='next')
        if not next_link or page_items == 0:
            print(f"  Reached final page (Page {page}).")
            break

        time.sleep(0.3)

    return products_raw


def fetch_ceneo_price(model: str, session: requests.Session) -> float | int | None:
    """Search Ceneo for product model and return lowest price listed."""
    if not model:
        return None

    url = f"https://www.ceneo.pl/;szukaj-{model}"
    try:
        response = session.get(url, headers=DEFAULT_HEADERS, timeout=12)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        matching_prices = []
        all_prices = []

        # 1. Parse category product rows (.cat-prod-row)
        rows = soup.select('.cat-prod-row')
        for row in rows:
            prod_name = row.get('data-productname') or row.get_text()
            p_attr = row.get('data-productminprice') or row.get('data-price')
            price = None
            if p_attr:
                try:
                    price = float(p_attr.replace(',', '.'))
                except ValueError:
                    pass

            if price is None:
                val_el = row.select_one('.value')
                penny_el = row.select_one('.penny')
                if val_el:
                    v_str = re.sub(r'[^\d]', '', val_el.get_text())
                    p_str = re.sub(r'[^\d]', '', penny_el.get_text()) if penny_el else ""
                    if v_str:
                        price = float(f"{v_str}.{p_str}") if p_str else float(v_str)

            if price and price > 100:
                all_prices.append(price)
                if model.lower() in prod_name.lower():
                    matching_prices.append(price)

        # 2. Check offer cards (in case Ceneo redirected directly to product page)
        if not all_prices:
            for item in soup.select('.product-offer, .product-offers__list__item, .offer-box'):
                p_attr = item.get('data-price')
                price = None
                if p_attr:
                    try:
                        price = float(p_attr.replace(',', '.'))
                    except ValueError:
                        pass
                if price is None:
                    val_el = item.select_one('.value')
                    penny_el = item.select_one('.penny')
                    if val_el:
                        v_str = re.sub(r'[^\d]', '', val_el.get_text())
                        p_str = re.sub(r'[^\d]', '', penny_el.get_text()) if penny_el else ""
                        if v_str:
                            price = float(f"{v_str}.{p_str}") if p_str else float(v_str)
                if price and price > 100:
                    all_prices.append(price)

        # 3. Check general price elements
        if not all_prices:
            for val_el in soup.select('.price-format, .total-price, .price'):
                v_el = val_el.select_one('.value')
                p_el = val_el.select_one('.penny')
                if v_el:
                    v_str = re.sub(r'[^\d]', '', v_el.get_text())
                    p_str = re.sub(r'[^\d]', '', p_el.get_text()) if p_el else ""
                    if v_str:
                        price = float(f"{v_str}.{p_str}") if p_str else float(v_str)
                        if price and price > 100:
                            all_prices.append(price)

        # 4. Regex fallback on page text
        if not all_prices:
            for match in re.findall(r'(\d[\d\s]*)(?:,\d{2})?\s*(?:zł|PLN)', response.text, re.IGNORECASE):
                clean = re.sub(r'[^\d]', '', match)
                if clean:
                    p = float(clean)
                    if 100 < p < 100000:
                        all_prices.append(p)

        target_prices = matching_prices if matching_prices else all_prices
        if target_prices:
            min_p = min(target_prices)
            return int(min_p) if min_p.is_integer() else round(min_p, 2)
        return None

    except Exception:
        # If Ceneo search fails for any reason, return None as per requirements
        return None


def load_existing_data(file_path: str) -> dict:
    """Load existing data/products.json if present."""
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not read existing data file ({e})")
    return {"lastUpdated": "", "products": []}


def run_scraper(max_pages: int = 25, max_ceneo_lookups: int = 35, full_ceneo: bool = False):
    """Main execution function for scraping and saving comparison data."""
    session = requests.Session()
    today_date = datetime.now().strftime("%Y-%m-%d")
    yesterday_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    current_timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    print(f"[{current_timestamp}] Starting Induction Cooktop Price Scraper...")
    print(f"Fetching MediaMarkt catalog from: {MEDIAMARKT_INDUCTION_URL}")
    raw_products = fetch_all_induction_products(session, max_pages=max_pages)
    print(f"\nExtracted {len(raw_products)} induction products across pages.")

    existing_data = load_existing_data(OUTPUT_FILE)
    existing_products_map = {p.get('id'): p for p in existing_data.get('products', []) if p.get('id')}

    processed_products = []
    ceneo_queries_count = 0

    for idx, prod_raw in enumerate(raw_products):
        raw_name = prod_raw.get('name', '')
        name = " ".join(raw_name.split())
        brand = extract_brand(name)
        model = extract_model(name, brand)
        p_type = extract_type(name)
        size_info = extract_size(name, model)

        clean_model = re.sub(r'[^a-zA-Z0-9]', '', model).lower() if model else ""
        clean_brand = re.sub(r'[^a-zA-Z0-9]', '', brand).lower()
        prod_id = f"{clean_brand}-{clean_model}" if clean_model else f"{clean_brand}-{idx+1}"

        prod_url = prod_raw.get('url', '')
        if prod_url and not prod_url.startswith('http'):
            prod_url = f"https://mediamarkt.pl{prod_url}"

        image_data = prod_raw.get('image', '')
        if isinstance(image_data, list) and image_data:
            image_url = image_data[0]
        elif isinstance(image_data, dict):
            image_url = image_data.get('url') or image_data.get('contentUrl') or ''
        else:
            image_url = str(image_data)

        offers = prod_raw.get('offers')
        offer = offers[0] if isinstance(offers, list) and offers else (offers if isinstance(offers, dict) else {})
        mediamarkt_price = parse_price(offer.get('price'))

        agg_rating = prod_raw.get('aggregateRating')
        rating = None
        review_count = 0
        if isinstance(agg_rating, dict):
            r_val = agg_rating.get('ratingValue')
            if r_val is not None:
                try:
                    rating = round(float(r_val), 2)
                except (ValueError, TypeError):
                    rating = None
            r_cnt = agg_rating.get('reviewCount')
            if r_cnt is not None:
                try:
                    review_count = int(r_cnt)
                except (ValueError, TypeError):
                    review_count = 0

        # Ceneo Price Lookup with caching
        existing = existing_products_map.get(prod_id)
        ceneo_price = None

        if existing and existing.get('ceneoPrice'):
            ceneo_price = existing['ceneoPrice']

        should_query = (ceneo_price is None or full_ceneo) and (ceneo_queries_count < max_ceneo_lookups)
        if should_query and model:
            ceneo_queries_count += 1
            print(f"[{idx + 1}/{len(raw_products)}] Querying Ceneo ({ceneo_queries_count}/{max_ceneo_lookups}): {brand} {model}...")
            fetched = fetch_ceneo_price(model, session)
            if fetched is not None:
                ceneo_price = fetched
            time.sleep(1.2)

        links = {
            "mediamarkt": prod_url,
            "ceneo": f"https://www.ceneo.pl/;szukaj-{model}" if model else "",
            "euro": f"https://www.euro.com.pl/search.bhtml?keyword={model}" if model else "",
            "komputronik": f"https://www.komputronik.pl/search?query={model}" if model else ""
        }

        value_score = calculate_value_score(rating, review_count, mediamarkt_price or ceneo_price)

        # Price History
        history = []
        if existing and existing.get('priceHistory'):
            history = list(existing['priceHistory'])
        elif existing and existing.get('mediamarktPrice'):
            # Seed yesterday's price so day-to-day comparison works immediately
            history.append({
                "date": yesterday_date,
                "mediamarkt": existing['mediamarktPrice'],
                "ceneo": existing.get('ceneoPrice')
            })

        updated_today = False
        for entry in history:
            if entry.get('date') == today_date:
                entry['mediamarkt'] = mediamarkt_price
                if ceneo_price:
                    entry['ceneo'] = ceneo_price
                updated_today = True
                break

        if not updated_today and mediamarkt_price:
            history.append({
                "date": today_date,
                "mediamarkt": mediamarkt_price,
                "ceneo": ceneo_price
            })

        # Calculate day-to-day price difference
        price_change = calculate_price_change(history, mediamarkt_price)

        product_obj = {
            "id": prod_id,
            "name": name,
            "model": model,
            "brand": brand,
            "type": p_type,
            "size": size_info,
            "image": image_url,
            "mediamarktPrice": mediamarkt_price,
            "ceneoPrice": ceneo_price,
            "priceChange": price_change,
            "rating": rating,
            "reviewCount": review_count,
            "valueScore": value_score,
            "links": links,
            "priceHistory": history
        }
        processed_products.append(product_obj)

    os.makedirs(DATA_DIR, exist_ok=True)
    output_data = {
        "lastUpdated": current_timestamp,
        "category": "Płyty indukcyjne do zabudowy",
        "categoryUrl": MEDIAMARKT_INDUCTION_URL,
        "totalProducts": len(processed_products),
        "products": processed_products
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_JS_FILE, 'w', encoding='utf-8') as f:
        f.write('window.PRODUCTS_DATA = ' + json.dumps(output_data, ensure_ascii=False, indent=2) + ';\n')

    # Automatically keep index.html embedded data in sync
    index_html_path = os.path.join(BASE_DIR, 'index.html')
    if os.path.exists(index_html_path):
        try:
            with open(index_html_path, 'r', encoding='utf-8') as hf:
                html_txt = hf.read()
            import re
            json_blob = json.dumps(output_data, ensure_ascii=False, indent=2)
            pattern = r'(<script id="embeddedData" type="application/json">)(.*?)(</script>)'
            if re.search(pattern, html_txt, re.DOTALL):
                html_txt = re.sub(pattern, r'\g<1>\n' + json_blob + r'\n\g<3>', html_txt, flags=re.DOTALL)
                with open(index_html_path, 'w', encoding='utf-8') as hf:
                    hf.write(html_txt)
                print(f"Successfully synchronized embedded data in: {index_html_path}")
        except Exception as e:
            print(f"Notice: Could not sync embedded data in index.html: {e}")

    print(f"\nSuccessfully wrote data to: {OUTPUT_FILE} and {OUTPUT_JS_FILE}")

    # Summary
    print("\n" + "=" * 60)
    print("                    SCRAPE SUMMARY")
    print("=" * 60)
    print(f"Total products scraped : {len(processed_products)}")
    ceneo_found = sum(1 for p in processed_products if p.get('ceneoPrice') is not None)
    print(f"Ceneo prices matched   : {ceneo_found}/{len(processed_products)}")

    size_counts = {}
    for p in processed_products:
        cat = p.get('size', {}).get('category', 'unknown')
        size_counts[cat] = size_counts.get(cat, 0) + 1
    print(f"Size breakdown         : {dict(sorted(size_counts.items()))}")

    price_drops = [p for p in processed_products if p.get('priceChange', {}).get('trend') == 'down']
    price_increases = [p for p in processed_products if p.get('priceChange', {}).get('trend') == 'up']
    print(f"Day-to-day Price Drops : {len(price_drops)}")
    print(f"Day-to-day Price Rises : {len(price_increases)}")

    mm_prices = [p['mediamarktPrice'] for p in processed_products if p.get('mediamarktPrice')]
    cn_prices = [p['ceneoPrice'] for p in processed_products if p.get('ceneoPrice')]

    if mm_prices:
        avg_mm = sum(mm_prices) / len(mm_prices)
        print(f"Average MediaMarkt price: {avg_mm:.2f} PLN")
    if cn_prices:
        avg_cn = sum(cn_prices) / len(cn_prices)
        print(f"Average Ceneo min price : {avg_cn:.2f} PLN")

    top_values = sorted(processed_products, key=lambda x: x.get('valueScore', 0), reverse=True)[:3]
    print("\nTop 3 Products by Value Score:")
    for rank, p in enumerate(top_values, 1):
        size_label = p.get('size', {}).get('label', '')
        print(f"  {rank}. [{p['valueScore']}] {p['name']} ({size_label}) (MM: {p['mediamarktPrice']} PLN, Rating: {p['rating']})")
    print("=" * 60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Scrape MediaMarkt induction cooktops")
    parser.add_argument("--max-pages", type=int, default=25, help="Max pages to crawl")
    parser.add_argument("--max-ceneo", type=int, default=35, help="Max Ceneo lookups")
    parser.add_argument("--full-ceneo", action="store_true", help="Look up Ceneo for all items")
    args = parser.parse_args()

    run_scraper(max_pages=args.max_pages, max_ceneo_lookups=args.max_ceneo, full_ceneo=args.full_ceneo)
