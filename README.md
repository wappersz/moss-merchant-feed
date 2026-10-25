# Moss Electrical: Google Merchant Centre feed (backup)

Nightly GitHub Action reads every product page on shop.mosselectricalsw.co.uk (price + stock from page JSON-LD)
and publishes `feed.xml`. Merchant Centre "PRODUCTS SOURCE 2" fetches it daily.
Temporary until MACE's own XML feed is switched on.

## Daily history
Each successful run also writes `snapshots/YYYY-MM-DD.csv` (id, price inc VAT, availability, brand, title)
so price and stock history builds up for the Foreman dashboard. History starts 21 Oct 2026
(backfilled from git by `backfill_snapshots.py`; 19-20 Oct dropped as partial first crawls).
