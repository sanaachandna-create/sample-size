#!/usr/bin/env python3
"""SAMPLE SIZE - news worker.

Pulls headlines from Google News RSS (free, cloud-friendly - not IP-blocked
like ESPN/NBA) for league NBA news, the user's favorite team, and any
watchlist players. De-dupes, keeps the freshest, writes data/news.json.
Standard library only.

Config via env vars (set in the workflow):
  FAV_TEAM   e.g. "Atlanta Hawks"      (blank = league only)
  WATCHLIST  e.g. "Trae Young, Jalen Johnson"
"""
import json
import os
import re
import html
import urllib.request
import urllib.parse
import datetime
import pathlib
import xml.etree.ElementTree as ET

RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SampleSizeBot/1.0)"}


def fetch(query):
    url = RSS.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def parse(xml_bytes, tag):
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        # Google prepends " - Source" to titles; split it off
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        title = html.unescape(title)
        ts = to_epoch(pub)
        if title and link:
            out.append({"title": title, "link": link, "source": source,
                        "ts": ts, "pub": pub, "tag": tag})
    return out


def to_epoch(pubdate):
    # e.g. "Mon, 14 Sep 2026 18:03:00 GMT"
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            dt = datetime.datetime.strptime(pubdate, fmt).replace(tzinfo=datetime.timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return 0


def collect(query, tag, cap):
    try:
        return parse(fetch(query), tag)[:cap]
    except Exception:  # noqa: BLE001
        return []


def load_config():
    try:
        c = json.loads(pathlib.Path("data/config.json").read_text())
        return (c.get("team", "") or "").strip(), [p.strip() for p in (c.get("watchlist") or []) if p.strip()]
    except Exception:  # noqa: BLE001
        return "", []


def main():
    cfg_team, cfg_watch = load_config()
    fav = cfg_team or (os.environ.get("FAV_TEAM") or "").strip()
    watch = cfg_watch or [p.strip() for p in (os.environ.get("WATCHLIST") or "").split(",") if p.strip()]
    err = None
    items = []
    try:
        items += collect("NBA basketball", "league", 12)
        if fav:
            items += collect(f'"{fav}" NBA', "team", 12)
        for p in watch[:6]:
            items += collect(f'"{p}" NBA', "player", 6)
        # de-dupe by title, keep the richest tag order team>player>league
        rank = {"team": 0, "player": 1, "league": 2}
        seen = {}
        for it in items:
            key = re.sub(r"\W+", "", it["title"].lower())[:60]
            if key not in seen or rank[it["tag"]] < rank[seen[key]["tag"]]:
                seen[key] = it
        items = sorted(seen.values(), key=lambda x: x["ts"], reverse=True)[:30]
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        items = []
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "team": fav, "count": len(items), "error": err, "items": items,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/news.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {len(items)} headlines" + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
