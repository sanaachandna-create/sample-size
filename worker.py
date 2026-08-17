#!/usr/bin/env python3
"""SAMPLE SIZE - schedule worker.

Pulls the NBA schedule from ESPN's public scoreboard and writes
data/today.json for the app to read. Standard library only, so it runs
on a bare GitHub Actions runner with no pip installs.

Optional: set GAME_DATE=YYYYMMDD (env var or first CLI arg) to fetch a
specific day - handy for testing during the offseason when 'today' is empty.
"""
import json
import os
import sys
import urllib.request
import datetime
import pathlib

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
NATIONAL = {"ESPN", "ESPN2", "ABC", "TNT", "NBC", "Peacock",
            "Prime Video", "Amazon Prime", "NBA TV"}


def fetch(date=None):
    url = ESPN + (f"?dates={date}" if date else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def to_ms(iso):
    """ESPN dates look like '2026-10-22T02:30Z'. Return epoch milliseconds."""
    if not iso:
        return None
    try:
        dt = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def transform(data):
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        home = away = None
        for c in comp.get("competitors", []):
            side = {
                "abbr": c.get("team", {}).get("abbreviation", ""),
                "name": c.get("team", {}).get("displayName", ""),
                "rec": (c.get("records") or [{}])[0].get("summary", "0-0"),
            }
            if c.get("homeAway") == "home":
                home = side
            else:
                away = side
        if not home or not away:
            continue
        casts = []
        for b in comp.get("broadcasts", []):
            casts += b.get("names", [])
        casts = list(dict.fromkeys(casts))  # de-dupe, keep order
        channel = " \u00b7 ".join(casts) or "Local \u00b7 League Pass"
        national = any(n in NATIONAL for n in casts)
        games.append({
            "ha": away["abbr"], "away": away["name"], "arec": away["rec"], "arank": "",
            "hh": home["abbr"], "home": home["name"], "hrec": home["rec"], "hrank": "",
            "tip": to_ms(ev.get("date", "")),
            "channel": channel, "national": national,
            "status": comp.get("status", {}).get("type", {}).get("state", "pre"),
            # placeholders the app expects; filled by later stages
            "injuries": [], "lineups": {"away": [], "home": []},
            "players": [], "ref": {}, "h2h": "",
        })
    games.sort(key=lambda g: g["tip"] or 0)
    return games


def main():
    date = os.environ.get("GAME_DATE") or (sys.argv[1] if len(sys.argv) > 1 else None)
    date = (date or "").strip() or None
    try:
        games = transform(fetch(date))
        err = None
    except Exception as e:  # noqa: BLE001 - we want to record any failure
        games, err = [], f"{type(e).__name__}: {e}"
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "ESPN",
        "date": date or "today",
        "count": len(games),
        "error": err,
        "games": games,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/today.json").write_text(json.dumps(out, indent=2))
    msg = f"wrote data/today.json - {len(games)} games"
    print(msg + (f" (error: {err})" if err else ""))


if __name__ == "__main__":
    main()
