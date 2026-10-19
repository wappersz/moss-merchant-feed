#!/usr/bin/env python3
"""
Moss Electrical -> Google Merchant Centre product feed (backup until MACE's own feed is live).

Reads every product page listed in the shop sitemap, takes price + stock from the
JSON-LD on each page (live MACE data), and writes feed.xml (RSS 2.0, g: namespace).
Runs nightly via GitHub Actions; Merchant Centre fetches the published feed.xml daily.
Stdlib only.
"""
import re, json, sys, time, html as htmllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote
from xml.sax.saxutils import escape
import urllib.request

BASE = "https://shop.mosselectricalsw.co.uk"
SITEMAP = f"{BASE}/sitemap.xml"
UA = "Mozilla/5.0 (compatible; MossMerchantFeed/1.0)"
WORKERS = 6
RETRIES = 3

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
}


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


def product_urls():
    sm = fetch(SITEMAP)
    urls = re.findall(r"<loc>\s*([^<]*ProductDetails[^<]*)</loc>", sm)
    return list(dict.fromkeys(htmllib.unescape(u.strip()) for u in urls))


def clean(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def txt_between(h, pat):
    m = re.search(pat, h, re.S | re.I)
    return clean(m.group(1)) if m else None


def parse(url):
    h = fetch(url)
    ld = re.search(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", h, re.S | re.I)
    if not ld:
        return None
    raw = ld.group(1).strip()
    try:
        d = json.loads(raw, strict=False)
    except ValueError:
        # MACE emits invalid JSON-LD on some pages (unescaped quotes in names, e.g. 13"),
        # so pull the fields out field-by-field instead.
        def grab(key):
            m = re.search(r'"%s"\s*:\s*"(.*?)"\s*[,}]\s*"?' % key, raw, re.S)
            return m.group(1) if m else None
        name = txt_between(h, r"<h1[^>]*>(.*?)</h1>") or grab("name")
        d = {"name": name, "sku": grab("sku"), "image": grab("image"), "description": "",
             "offers": {"price": grab("price"), "availability": grab("availability"), "url": grab("url")}}
    off = d.get("offers") or {}
    name = clean(d.get("name"))
    code = d.get("sku") or unquote(re.search(r"code=([^&]+)", url, re.I).group(1))
    desc = clean(d.get("description"))
    if not desc:
        body = re.search(r'<meta name="description" content="([^"]*)"', h, re.I)
        desc = clean(body.group(1)) if body else ""
    return {
        "id": code,
        "title": name,
        "description": desc if len(desc) > len(name) else name,
        "link": off.get("url") or url,
        "image": d.get("image"),
        "price": off.get("price"),  # JSON-LD price is inc VAT (checked against page "inc VAT" price)
        "availability": (off.get("availability") or "").split("/")[-1],
        "brand": BRANDS.get(name.split(" ")[0]) if name else None,
    }


def item_xml(p):
    avail = "in_stock" if p["availability"] == "InStock" else "out_of_stock"
    lines = [
        "<item>",
        f"<g:id>{escape(p['id'])}</g:id>",
        f"<g:title>{escape(p['title'][:150])}</g:title>",
        f"<g:description>{escape(p['description'][:5000])}</g:description>",
        f"<g:link>{escape(p['link'])}</g:link>",
        f"<g:image_link>{escape(p['image'])}</g:image_link>",
        f"<g:price>{float(p['price']):.2f} GBP</g:price>",
        f"<g:availability>{avail}</g:availability>",
        "<g:condition>new</g:condition>",
        "<g:identifier_exists>no</g:identifier_exists>",
    ]
    if p["brand"]:
        lines.append(f"<g:brand>{escape(p['brand'])}</g:brand>")
    lines.append("</item>")
    return "\n".join(lines)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    urls = product_urls()
    if limit:
        urls = urls[:limit]
    print(f"{len(urls)} product URLs", file=sys.stderr)

    items, skipped, errors = [], 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in as_completed([ex.submit(parse, u) for u in urls]):
            try:
                p = f.result()
            except Exception:
                errors += 1
                continue
            # Google rejects items without a title, image or a real price
            if not p or not p["title"] or not p["image"] or not p["price"] or float(p["price"]) <= 0:
                skipped += 1
                continue
            items.append(p)

    # Guard: a near-empty run (site down) must not overwrite a good feed
    if len(items) < (50 if not limit else 1):
        print(f"ABORT: only {len(items)} items", file=sys.stderr)
        sys.exit(1)

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
    print(f"feed.xml: {len(items)} items ({in_stock} in stock), {skipped} skipped, {errors} errors", file=sys.stderr)


if __name__ == "__main__":
    main()
