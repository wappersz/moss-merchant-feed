#!/usr/bin/env python3
"""
Moss Electrical -> Google Merchant Centre product feed.

Since the Shopify launch (6 Nov 2026) it reads the shop's public /products.json (price inc VAT,
stock and images, synced from MACE) and writes feed.xml (RSS 2.0, g: namespace). Item ids stay
the MACE product codes (= Shopify SKUs), so Merchant Centre history carries over.
Runs nightly via GitHub Actions; Merchant Centre fetches the published feed.xml daily.
Stdlib only.
"""
import os, re, json, sys, time, csv, html as htmllib
from urllib.parse import unquote
from xml.sax.saxutils import escape
import urllib.request

BASE = "https://shop.mosselectrical.co.uk"
IMAGE_PREFIX = "https://cdn.shopify.com/"
UA = "Mozilla/5.0 (compatible; MossMerchantFeed/2.0)"
RETRIES = 3

# Barcodes (GTIN/EAN) by product code. MACE has no barcode field, so these come from
# manufacturer data files. Never guessed: every row names its source.
GTIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gtin.csv")
GTINS = {}
if os.path.exists(GTIN_FILE):
    with open(GTIN_FILE, newline="") as f:
        GTINS = {r["id"].strip(): r["gtin"].strip() for r in csv.DictReader(f) if r.get("gtin")}
# Shopping title/brand overrides by product code (feed only; the shop keeps its MACE title).
# Facts from the product page or manufacturer datasheet only. Delete a row to revert.
TITLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "titles.csv")
TITLES = {}
if os.path.exists(TITLE_FILE):
    with open(TITLE_FILE, newline="") as f:
        TITLES = {r["id"].strip(): r for r in csv.DictReader(f) if r.get("title")}
# Cable etc. sold by the metre or off a roll/drum has no unit barcode, and a pack barcode
# would match the wrong product, so never send a GTIN for these.
LOOSE_SALE = re.compile(r"\b(per\s*met(re|er)s?|metres?|meters?|/m|rolls?|drums?|reels?|coils?|cut to length|\d+\s*m)\b", re.I)

# First word of product name -> brand. Anything not listed gets no brand (identifier_exists=no covers it).
BRANDS = {
    "Hager": "Hager", "Deta": "Deta", "MK": "MK Electric", "Marshall": "Marshall Tufflex",
    "Kosnic": "Kosnic", "Armeg": "Armeg", "ALL": "All LED", "Vent": "Vent-Axia", "Click": "Click",
    "Unicrimp": "Unicrimp", "Deligo": "Deligo", "Wylex": "Wylex", "Niglon": "Niglon", "CK": "CK Tools",
    "Aico": "Aico", "Gewiss": "Gewiss", "Conlok": "Conlok", "Pemsa": "Pemsa", "Metpro": "Metpro",
    "Wiska": "Wiska", "Adaptaflex": "Adaptaflex", "Nuaire": "Nuaire", "Robus": "Robus", "Wago": "Wago",
    "Appleby": "Appleby", "Saxby": "Saxby", "Duracell": "Duracell", "Dahua": "Dahua",
    "Fumagalli": "Fumagalli", "Aurora": "Aurora", "Timeguard": "Timeguard", "Thorn": "Thorn",
    "Crabtree": "Crabtree", "Eterna": "Eterna", "Prysmian": "Prysmian", "Qvis": "Qvis",
    "Bussmann": "Bussmann", "Knightsbridge": "Knightsbridge", "Schneider": "Schneider Electric",
    "Ansell": "Ansell", "Collingwood": "Collingwood", "Ovia": "Ovia", "Scolmore": "Scolmore",
    "Greenbrook": "Greenbrook", "BG": "BG Electrical", "Fischer": "Fischer", "Rawlplug": "Rawlplug",
    "ABB": "ABB", "Flex7": "Flex7", "Project": "Project EV", "Gripple": "Gripple", "CED": "CED",
}
# Two-word brand names, matched on the start of the product name.
BRAND_PREFIXES = {"Flex 7 ": "Flex7", "D Line ": "D-Line", "Green Lighting ": "Green Lighting"}


def brand_for(name):
    for prefix, brand in BRAND_PREFIXES.items():
        if name.startswith(prefix):
            return brand
    return BRANDS.get(name.split(" ")[0])


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def find_mpn(code, title):
    """Manufacturer part number = a title token that also matches the end of the Moss code
    (e.g. HAG624SEL2126 + "Hager 624SEL212/6 ..." -> 624SEL212/6). Both sources must agree,
    otherwise None (better no MPN than a wrong one)."""
    c, best = norm(code), None
    for tok in (title or "").split()[1:]:
        tok = tok.strip("(),;:")
        n = norm(tok)
        if len(n) >= 4 and re.search(r"\d", n) and c.endswith(n) and len(c) - len(n) <= 4:
            if not best or len(n) > len(norm(best)):
                best = tok
    return best


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def clean(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def shopify_products():
    """Every published product from the public /products.json, 250 per page."""
    out, page = [], 1
    while True:
        batch = json.loads(fetch(f"{BASE}/products.json?limit=250&page={page}"))["products"]
        if not batch:
            return out
        out += batch
        page += 1
        time.sleep(1)


def items_from(products):
    for pr in products:
        img = (pr.get("images") or [{}])[0].get("src")
        name = clean(pr.get("title"))
        desc = clean(pr.get("body_html"))
        multi = len(pr["variants"]) > 1
        for v in pr["variants"]:
            code = (v.get("sku") or "").strip()
            if not code:
                continue
            link = f"{BASE}/products/{pr['handle']}" + (f"?variant={v['id']}" if multi else "")
            title = name if not multi or v.get("title") in (None, "Default Title") else f"{name} {v['title']}"
            yield {
                "id": code,
                "title": title,
                "description": desc if len(desc) > len(title) else title,
                "link": link,
                "image": img.split("?")[0] if img else None,
                "price": v.get("price"),  # shop prices include VAT (taxesIncluded = true)
                "availability": "InStock" if v.get("available") else "OutOfStock",
                # Shopify vendor is the real brand, except the store-name placeholder on unbranded items
                "brand": (pr.get("vendor") or "").strip() if (pr.get("vendor") or "").strip() not in ("", "Moss Electrical") else brand_for(name),
                "mpn": find_mpn(code, name),
                "barcode": (v.get("barcode") or "").strip(),
            }


def item_xml(p):
    avail = "in_stock" if p["availability"] == "InStock" else "out_of_stock"
    lines = [
        "<item>",
        f"<g:id>{escape(p['id'])}</g:id>",
        f"<g:title>{escape((TITLES.get(p['id'], {}).get('title') or p['title'])[:150])}</g:title>",
        f"<g:description>{escape(p['description'][:5000])}</g:description>",
        f"<g:link>{escape(p['link'])}</g:link>",
        f"<g:image_link>{escape(p['image'])}</g:image_link>",
        f"<g:price>{float(p['price']):.2f} GBP</g:price>",
        f"<g:availability>{avail}</g:availability>",
        "<g:condition>new</g:condition>",
    ]
    brand = (TITLES.get(p["id"], {}).get("brand") or "").strip() or p["brand"]
    if brand:
        lines.append(f"<g:brand>{escape(brand)}</g:brand>")
    gtin = GTINS.get(p["id"]) or (p.get("barcode") if re.fullmatch(r"\d{8}|\d{12,14}", p.get("barcode") or "") else None)
    if gtin and LOOSE_SALE.search(p["title"]):
        gtin = None
    if gtin:
        lines.append(f"<g:gtin>{escape(gtin)}</g:gtin>")  # groups us into the shared Shopping listing
    if p["brand"] and p.get("mpn"):
        lines.append(f"<g:mpn>{escape(p['mpn'])}</g:mpn>")  # brand + MPN = identifiers exist
    elif not gtin:
        lines.append("<g:identifier_exists>no</g:identifier_exists>")
    lines.append("</item>")
    return "\n".join(lines)


SHOP_PREFIX = BASE + "/"
MIN_ITEMS = 800            # full catalogue is ~1,029; far fewer = scraper or site broken
MAX_DROP = 0.20            # more than 20% fewer items than the last good feed = stop
MAX_SWING_SHARE = 0.10     # more than 10% of prices moving by over 30% = stop


def previous_feed():
    """Last published feed (in the repo), as {id: price}."""
    if not os.path.exists("feed.xml"):
        return {}
    xml = open("feed.xml", encoding="utf-8").read()
    prev = {}
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        i = re.search(r"<g:id>(.*?)</g:id>", item)
        pr = re.search(r"<g:price>([\d.]+) GBP</g:price>", item)
        if i and pr:
            prev[htmllib.unescape(i.group(1))] = float(pr.group(1))
    return prev


def fail(msg):
    # Non-zero exit = GitHub marks the run failed and emails the repo owner; old feed stays published.
    print(f"SAFETY CHECK FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def safety_checks(items):
    bad = [p["id"] for p in items
           if not p["link"].startswith(SHOP_PREFIX) or not str(p["image"]).startswith(IMAGE_PREFIX)]
    if bad:
        fail(f"{len(bad)} items link off the shop domain, e.g. {bad[:3]}")
    if len(items) < MIN_ITEMS:
        fail(f"only {len(items)} items (minimum {MIN_ITEMS})")
    prev = previous_feed()
    if prev:
        if len(items) < len(prev) * (1 - MAX_DROP):
            fail(f"{len(items)} items vs {len(prev)} last time")
        common = [p for p in items if p["id"] in prev and prev[p["id"]] > 0]
        swings = [p["id"] for p in common if abs(float(p["price"]) - prev[p["id"]]) / prev[p["id"]] > 0.30]
        if common and len(swings) > len(common) * MAX_SWING_SHARE:
            fail(f"{len(swings)} of {len(common)} prices moved over 30%, e.g. {swings[:3]}")
        print(f"checks ok: {len(items)} items, {len(swings)} big price moves", file=sys.stderr)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    products = shopify_products()
    print(f"{len(products)} products", file=sys.stderr)

    items, skipped, seen = [], 0, set()
    for p in items_from(products):
        # Google rejects items without a title, image or a real price; ids must be unique
        if not p["title"] or not p["image"] or not p["price"] or float(p["price"]) <= 0 or p["id"] in seen:
            skipped += 1
            continue
        seen.add(p["id"])
        items.append(p)
    if limit:
        items = items[:limit]

    # Guard: a near-empty run (site down) must not overwrite a good feed
    if len(items) < (50 if not limit else 1):
        print(f"ABORT: only {len(items)} items", file=sys.stderr)
        sys.exit(1)
    if not limit:
        safety_checks(items)

    items.sort(key=lambda p: p["id"])
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">\n<channel>\n'
        "<title>Moss Electrical Online</title>\n"
        f"<link>{BASE}/</link>\n"
        "<description>Moss Electrical Online product feed</description>\n"
        + "\n".join(item_xml(p) for p in items)
        + "\n</channel>\n</rss>\n"
    )
    with open("feed.xml", "w", encoding="utf-8") as fh:
        fh.write(feed)

    in_stock = sum(1 for p in items if p["availability"] == "InStock")
    print(f"feed.xml: {len(items)} items ({in_stock} in stock), {skipped} skipped", file=sys.stderr)

    if not limit:
        try:
            write_snapshot(items)
        except Exception as e:  # history is a nice-to-have; never let it block the feed
            print(f"snapshot failed: {e}", file=sys.stderr)


SNAPSHOT_DIR = "snapshots"
SNAPSHOT_FIELDS = ["id", "price", "availability", "brand", "title"]


def write_snapshot(items):
    """One CSV per day (UTC date) so price + stock history builds up for the dashboard.
    Only written after safety checks pass, so a bad run never lands in history."""
    import csv
    from datetime import datetime, timezone
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SNAPSHOT_FIELDS, extrasaction="ignore")
        w.writeheader()
        for p in items:
            w.writerow({**p, "price": f"{float(p['price']):.2f}"})
    print(f"snapshot: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
