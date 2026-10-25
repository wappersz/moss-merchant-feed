#!/usr/bin/env python3
"""
One-off: rebuild snapshots/YYYY-MM-DD.csv from feed.xml versions in git history
(the nightly feed was committed before daily snapshots existed). Last commit per UTC day wins.
Never overwrites a snapshot that already exists. Stdlib only.
"""
import csv, os, re, subprocess, html as htmllib

from build_feed import SNAPSHOT_DIR, SNAPSHOT_FIELDS


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def parse_feed(xml):
    rows = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(t):
            m = re.search(rf"<g:{t}>(.*?)</g:{t}>", item, re.S)
            return htmllib.unescape(m.group(1)) if m else ""
        price = re.search(r"([\d.]+) GBP", tag("price"))
        rows.append({
            "id": tag("id"),
            "price": price.group(1) if price else "",
            "availability": "InStock" if tag("availability") == "in_stock" else "OutOfStock",
            "brand": tag("brand"),
            "title": tag("title"),
        })
    return rows


def main():
    by_day = {}
    # oldest first, so the last commit of each day overwrites earlier ones
    for line in reversed(git("log", "--format=%H %cd", "--date=format-local:%Y-%m-%d", "--", "feed.xml").split("\n")):
        if line.strip():
            sha, day = line.split()
            by_day[day] = sha
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    for day, sha in sorted(by_day.items()):
        path = os.path.join(SNAPSHOT_DIR, f"{day}.csv")
        if os.path.exists(path):
            print(f"skip {path} (exists)")
            continue
        rows = parse_feed(git("show", f"{sha}:feed.xml"))
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=SNAPSHOT_FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"{path}: {len(rows)} items from {sha[:7]}")


if __name__ == "__main__":
    main()
