#!/usr/bin/env python3
"""SAMPLE SIZE - news worker.

Pulls NBA headlines from Google News RSS (free, cloud-friendly). Writes
data/news.json for the app, AND fires a Discord push for NEW breaking
items (trades, injuries, signings) so your phone buzzes ASAP.

Env (set in the workflow):
  DISCORD_WEBHOOK   your #sample-size webhook URL (secret)
  FAV_TEAM / WATCHLIST  fallbacks if data/config.json is absent
Reads data/config.json {team, watchlist} when present.
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
DISCORD = (os.environ.get("DISCORD_WEBHOOK") or "").strip()
BREAK_KW = ["traded", "trade", "acquire", "acquires", "acquired", "agrees", "agreed",
            "signs", "signing", "re-sign", "waived", "waive", "released", "buyout",
            "injury", "injured", "surgery", "acl", "torn", "ruptured", "sidelined",
            "ruled out", "out for", "suspended", "suspension", "doubtful", "done for"]


def fetch(query):
    req = urllib.request.Request(RSS.format(q=urllib.parse.quote(query)), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def to_epoch(pubdate):
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            return int(datetime.datetime.strptime(pubdate, fmt)
                       .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    return 0


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
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        title = html.unescape(title)
        if title and link:
            out.append({"title": title, "link": link, "source": source,
                        "ts": to_epoch(pub), "tag": tag})
    return out


def collect(query, tag, cap):
    try:
        return parse(fetch(query), tag)[:cap]
    except Exception:  # noqa: BLE001
        return []


def key_of(title):
    return re.sub(r"\W+", "", title.lower())[:60]


def is_break(title):
    t = title.lower()
    return any(k in t for k in BREAK_KW)


def discord_push(item):
    if not DISCORD:
        return False
    tag = item.get("tag", "league")
    color = {"team": 15744599, "player": 3907575}.get(tag, 10461087)
    body = {"username": "SAMPLE SIZE — WIRE", "embeds": [{
        "title": item["title"][:240], "url": item["link"], "color": color,
        "footer": {"text": (item.get("source") or "NBA") + " · " + tag.upper()}}]}
    try:
        req = urllib.request.Request(DISCORD, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:  # noqa: BLE001
        return False


def load_config():
    try:
        c = json.loads(pathlib.Path("data/config.json").read_text())
        return (c.get("team", "") or "").strip(), [p.strip() for p in (c.get("watchlist") or []) if p.strip()]
    except Exception:  # noqa: BLE001
        return "", []


def main():
    if os.environ.get("TEST_PUSH") == "1":
        ok = discord_push({"title": "TEST — breaking-news pushes are live",
                           "link": "https://sanaachandna-create.github.io/sample-size/",
                           "source": "SAMPLE SIZE", "tag": "team"})
        print("test push:", "sent ✓" if ok else "FAILED — is DISCORD_WEBHOOK set?")
    cfg_team, cfg_watch = load_config()
    fav = cfg_team or (os.environ.get("FAV_TEAM") or "").strip()
    watch = cfg_watch or [p.strip() for p in (os.environ.get("WATCHLIST") or "").split(",") if p.strip()]

    prev = None
    try:
        prev = json.loads(pathlib.Path("data/news.json").read_text())
    except Exception:  # noqa: BLE001
        prev = None
    pushed = (prev or {}).get("pushed", [])
    pushed_set = set(pushed)
    first_run = prev is None

    err = None
    items = []
    pushes = 0
    try:
        items += collect("NBA basketball", "league", 12)
        items += collect("NBA trade", "league", 8)
        items += collect("NBA injury OR injured", "league", 8)
        if fav:
            items += collect(f'"{fav}" NBA', "team", 12)
        for p in watch[:6]:
            items += collect(f'"{p}" NBA', "player", 6)

        rank = {"team": 0, "player": 1, "league": 2}
        seen = {}
        for it in items:
            k = key_of(it["title"])
            if k not in seen or rank[it["tag"]] < rank[seen[k]["tag"]]:
                seen[k] = it
        items = sorted(seen.values(), key=lambda x: x["ts"], reverse=True)

        # breaking-news push (skip the very first run so we don't burst)
        breaking = [it for it in items if is_break(it["title"]) and key_of(it["title"]) not in pushed_set]
        breaking = sorted(breaking, key=lambda x: x["ts"], reverse=True)
        if first_run:
            for it in breaking:
                pushed.append(key_of(it["title"]))
        else:
            for it in breaking[:5]:  # cap per run to avoid flooding
                if discord_push(it):
                    pushes += 1
                pushed.append(key_of(it["title"]))
        pushed = pushed[-500:]
        items = items[:30]
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "team": fav, "count": len(items), "pushed_this_run": pushes,
        "error": err, "items": items, "pushed": pushed,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/news.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {len(items)} headlines, pushed {pushes}" + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
